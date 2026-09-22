"""共用的 HTTP 用戶端基礎(跟 httpkit.py 是伺服器基礎、sqlitekit.py 是資料庫基礎同一層):
DSP 用戶端與收件口用戶端都用它,不各自造輪子。

逾時沒有預設值——呼叫端一定要自己決定要等多久,不會有人忘記設定而讓請求永遠卡住。
標頭只接受封閉列舉(`ClientHeader`),不接受任意字典:不管呼叫端用什麼方式組出一個標頭
名稱字串,只要它不是這個列舉的成員,就送不出去。這是唯一的真正防線;掃描原始碼裡有沒有
出現故障注入標頭那個字樣只是輔助訊號,不是安全機制本身。
"""

import json
from enum import StrEnum
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class ClientHeader(StrEnum):
    """本專案的用戶端允許送出的標頭,封閉列舉;要加新的就在這裡加一個成員,不開放任意字串。"""

    IDEMPOTENCY_KEY = "Idempotency-Key"


def request_json(
    url: str,
    method: str,
    body: dict[str, Any] | None,
    timeout_seconds: float,
    headers: dict[ClientHeader, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """送一個 JSON 請求,回傳 (狀態碼, 解析後的內容)。非 2xx 狀態碼回傳,不丟例外;
    逾時、連線失敗、內容不是合法 JSON 一律讓例外原樣往外傳,呼叫端自己決定怎麼處理。
    """
    for key in headers or {}:
        if not isinstance(key, ClientHeader):
            raise TypeError(f"標頭名稱必須是 ClientHeader 的成員,得到 {key!r}")
    raw = None if body is None else json.dumps(body).encode()
    request_headers = {"Content-Type": "application/json"}
    request_headers.update({str(key): value for key, value in (headers or {}).items()})
    request = Request(url, data=raw, method=method, headers=request_headers)  # noqa: S310
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - 只用來打本機的 DSP/收件口,網址由呼叫端(dsp_client/inbox_client)寫死,不是外部輸入
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())
    except TimeoutError:
        raise
    except OSError as error:
        if isinstance(error, TimeoutError):
            raise
        raise
