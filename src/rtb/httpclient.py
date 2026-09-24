"""共用的 HTTP 用戶端基礎(跟 httpkit.py 是伺服器基礎、sqlitekit.py 是資料庫基礎同一層):
DSP 用戶端與收件口用戶端都用它,不各自造輪子。

逾時沒有預設值——呼叫端一定要自己決定要等多久,不會有人忘記設定而讓請求永遠卡住。
標頭只接受封閉列舉(`ClientHeader`),不接受任意字典:不管呼叫端用什麼方式組出一個標頭
名稱字串,只要它不是這個列舉的成員,就送不出去。這是唯一的真正防線;掃描原始碼裡有沒有
出現故障注入標頭那個字樣只是輔助訊號,不是安全機制本身。

不自動跟隨 HTTP 重新導向:網址是呼叫端寫死的信任假設,只保證「第一個請求送去哪裡」,不保證
「最終回應是誰給的」——應答端只要回一個 3xx 就能把請求接到任意主機,呼叫端毫無感知。3xx 一律
變成 HTTPError 往外傳,不自動跟。
"""

import json
import time
from enum import StrEnum
from typing import Any
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_RESPONSE_BYTES = 64 * 1024  # 跟 httpkit.py 的 MAX_BODY_BYTES 對稱:回應本文也不能無界讀入記憶體


class ClientHeader(StrEnum):
    """本專案的用戶端允許送出的標頭,封閉列舉;要加新的就在這裡加一個成員,不開放任意字串。"""

    IDEMPOTENCY_KEY = "Idempotency-Key"
    # 寫入能力憑證。字串照抄共用格式模組的標頭名稱,由測試比對兩邊一致;不匯入那個模組,
    # 免得分析行程經共用用戶端間接載入憑證模組
    CAPABILITY = "X-Capability"
    # DSP 唯讀稽核金鑰(Phase 9 增量 3 代碼審第 2 輪):只給維運套件讀 DSP 列操作端點,不是故障注入
    # 標頭;字串同樣照抄共用格式模組、由測試比對
    AUDIT_KEY = "X-Dsp-Audit-Key"


class _NoRedirect(HTTPRedirectHandler):
    """回 None 讓 urllib 把 3xx 當成一般的錯誤狀態碼丟 HTTPError,不建新請求去跟。"""

    def redirect_request(
        self, _req: Request, _fp: Any, _code: int, _msg: str, _headers: Any, _newurl: str,
    ) -> Request | None:
        return None


# 不走任何代理(Phase 12 代碼審 r3 s1):預設的代理處理會讀環境裡的 HTTP_PROXY,帶著稽核金鑰的請求會
# 送去代理、代理的回應也會被當成對方的回應;本專案只連本機回送位址,一律直連
_opener = build_opener(ProxyHandler({}), _NoRedirect)


def request_json(
    url: str,
    method: str,
    body: dict[str, Any] | None,
    timeout_seconds: float,
    headers: dict[ClientHeader, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """送一個 JSON 請求,回傳 (狀態碼, 解析後的內容)。非 2xx 狀態碼回傳,不丟例外;
    逾時、連線失敗、內容不是合法 JSON、回應本文超過上限一律讓例外原樣往外傳,呼叫端自己決定
    怎麼處理。
    """
    validated_headers = dict(headers or {})  # 先具現化一次,不對呼叫端傳入的物件重複走訪
    # (重複走訪同一個不可信的 dict 物件兩次,若它是自訂子類別可以讓兩次走訪回傳不同內容,
    # 驗證跟實際取值就會不同步)
    for key in validated_headers:
        if not isinstance(key, ClientHeader):
            raise TypeError(f"標頭名稱必須是 ClientHeader 的成員,得到 {key!r}")
    raw = None if body is None else json.dumps(body).encode()
    request_headers = {"Content-Type": "application/json"}
    request_headers.update({str(key): value for key, value in validated_headers.items()})
    request = Request(url, data=raw, method=method, headers=request_headers)  # noqa: S310
    deadline = time.monotonic() + timeout_seconds
    try:
        with _opener.open(request, timeout=timeout_seconds) as response:
            return response.status, _parsed(response.status, response, deadline)
    except HTTPError as error:
        return error.code, _parsed(error.code, error, deadline)
    except TimeoutError:
        raise
    except OSError as error:
        if isinstance(error, TimeoutError):
            raise
        raise


class UnreadableResponse(ValueError):
    """拿到了狀態碼、但本文不是合法 JSON 或超過上限(Phase 9 增量 1 代碼審第 1 輪):帶著狀態碼往外丟,
    呼叫端能依狀態碼分類(500 加 HTML 本文是 5xx,不是「沒有狀態碼的讀不懂」)。是 ValueError 的
    子類別,既有只接 ValueError 的呼叫端不受影響。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _parsed(status: int, source: Any, deadline: float) -> Any:
    try:
        return json.loads(_read_capped(source, deadline))
    except ValueError as exc:  # JSON 解不開、編碼不對、超過上限
        raise UnreadableResponse(status, str(exc)) from exc


def _read_capped(response: Any, deadline: float) -> bytes:
    """分段讀回應本文,每段之間核對總位元組數上限與總期限。

    `urlopen(..., timeout=timeout_seconds)` 的 timeout 只保證單一次阻塞的 socket 操作不超過
    這個秒數,不保證整個請求的總耗時——只要伺服器每次都在逾時前送出一點點資料(慢速持續送),
    一次讀到底的 `response.read()` 可以被拖到不設限。這裡改成有界的分段讀取:總位元組數超過
    `MAX_RESPONSE_BYTES`、或下一段開始前已經過了 `deadline`,就直接放棄,不繼續等。
    這不是逐位元組精確的總期限(單一段 `read1()` 呼叫本身仍可能阻塞到接近 timeout_seconds 才
    回來),是比「完全沒有總上限」更緊的盡力而為版本,跟伺服器端 `httpkit.py` 用
    `threading.Timer` 中止連線一樣,都是同一種「無法做到絕對精確,但比不做好」的取捨。

    用 `read1()` 不是 `read()`:一般的 `read(n)` 是 `io.BufferedIOBase` 的行為,會在內部
    重複呼叫底層 socket 直到湊滿 n 個位元組(或連線關閉)才回傳——對慢速持續送資料的回應,
    一次 `read(8192)` 呼叫本身就可能悶著等到整包收完,回到這個迴圈時已經沒有意義。
    `read1(n)` 保證最多只做一次底層讀取,收到多少就回傳多少,才能讓下面的期限檢查真的有機會
    在資料收完之前介入。
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError("回應在整體期限內沒有讀完(慢速持續送資料)")
        chunk = response.read1(8192)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise ValueError(f"回應本文超過上限 {MAX_RESPONSE_BYTES} 位元組")
        chunks.append(chunk)
    return b"".join(chunks)
