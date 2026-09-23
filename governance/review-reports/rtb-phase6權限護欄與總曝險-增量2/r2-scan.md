severity: minor

範圍:只審「## 增量 2 設計:護欄表格」節的第 5 點(寫入掃描)與 [S408],對照 `/Users/enzo/rtb-3b/src/rtb/analyzer/`、`/Users/enzo/rtb-3b/src/rtb/httpclient.py`、`/Users/enzo/rtb-3b/tests/analyzer/test_boundaries.py`。

## 結論先講

「管請求函式在哪裡被提到」這條規則照字面用 ast 做得出來,而且我用 Python 的 `ast` 模組對三個「開發者一不小心」的樣本(`from rtb import httpclient` 再 `httpclient.request_json(...)`、`import rtb.httpclient as h`、`from rtb.httpclient import request_json as rq` 改名呼叫)實際跑過:三種都能被一個「一般水準、細心」的實作正確逮到,不是抓不到。但要做對,實作者得跨過三個 Python import 語意本身的陷阱,而快照的文字沒有把這三個陷阱寫清楚——這是我要報的洞,都判 minor(文件精度,不是行為錯誤或漏合約,因為只要照著「以任何形式提到」這句話的精神去實作,結果仍然是對的)。核對既有程式碼(`dsp_client.py`、`inbox_client.py`、`httpclient.py`)沒有發現會被這套規則誤報的合法用法。

## F1 「不准匯入共用 HTTP 用戶端模組」沒指出 `from pkg import submodule` 這種 import 形式的解析陷阱

severity: minor
blocking: 否 — 措辭/實作指引精度問題,不是合約缺漏;規則本身的措辭「以任何形式提到」在精神上已經涵蓋這個情況

引句:「其他模組(共用 HTTP 用戶端自己除外)不准匯入共用 HTTP 用戶端模組,也不准以任何形式提到這個函式名。」

Python 的 `from rtb import httpclient` 在 AST 上是 `ImportFrom(module='rtb', names=[alias(name='httpclient')])`,`node.module` 是 `"rtb"` 不是 `"rtb.httpclient"`。我寫了一段掃描器原型驗證:只有把 `ImportFrom.module` 跟 `alias.name` 接起來(`f"{node.module}.{alias.name}"`)比對,才能認出這其實是在匯入 `rtb.httpclient` 這個模組;若實作者照字面直覺寫「`node.module == "rtb.httpclient"`」,`from rtb import httpclient` 這種寫法就會漏網——正是題目問的「開發者一不小心」情境之一。不過因為後續 `httpclient.request_json(...)` 的呼叫仍是 `Attribute(attr="request_json")`,只要掃描器對「函式名有沒有被提到」是用「任何 Attribute 節點的 `.attr` 是否等於 `request_json`」這種與匯入方式無關的通用比對(而不是先解析匯入路徑、只認特定變數名),這個案例照樣會在呼叫點被逮到,不會整個漏過去。file: `/tmp/p6d2_probe/scan.py`(本次驗證用的原型腳本,已用三個樣本跑過)。

## F2 命名空間套件(沒有 `__init__.py`)在「載入後取本專案模組清單」這步可能讓沒防到 `__file__` 是 None 的實作當掉

severity: minor
blocking: 否 — 會讓測試明顯紅(不是靜默漏測),照 PEP 420 命名空間套件本身也沒有原始碼要掃,略過是正確行為

引句:「掃描範圍:不只分析行程套件的檔案。比照既有「在乾淨子行程載入整個分析行程」那支測試,真的載入分析行程,取出這次載入進來、屬於本專案的每一個模組」

核對發現 `src/rtb/`、`src/rtb/analyzer/`、`src/rtb/domain/` 都沒有 `__init__.py`(`find` 找不到),是 PEP 420 命名空間套件。子行程載入後 `sys.modules['rtb']`、`sys.modules['rtb.analyzer']`、`sys.modules['rtb.domain']` 這幾個套件層級的模組物件沒有原始碼檔案(`__file__` 通常是 `None` 或不存在),不能直接拿去 `ast.parse`。快照沒提到這個情況要跳過,若實作者照字面「每一支都掃」去寫,遇到這幾個套件模組會丟例外而不是「掃出乾淨」,第一次跑測試就會發現、不是安全性風險,但值得在設計裡補一句「跳過沒有原始碼檔案的模組」以免每個人各自重踩一次。

## 觀察:既有兩支允許檔案的呼叫寫法完全落在規則的字面定義內,沒有誤報風險

核對 `/Users/enzo/rtb-3b/src/rtb/analyzer/dsp_client.py`(第 31、118、193 行)與 `/Users/enzo/rtb-3b/src/rtb/analyzer/inbox_client.py`(第 18、61 行):兩支檔案都只用 `from rtb.httpclient import request_json` 直接匯入函式,三次呼叫全部是字面呼叫、方法是字面字串常數(`"GET"` 兩次、`"POST"` 一次)、都沒有傳 `headers` 參數。`inbox_client.py` 的 POST 網址是 `f"{base_url}/proposals"`,以提案路徑結尾,跟快照描述一致。`src/rtb/httpclient.py` 本身只有一處出現 `request_json`(`def request_json(` 的函式定義),那是 `ast.FunctionDef.name`(字串屬性),不是 `ast.Name`/`ast.Attribute` 節點,一般用「走訪 Name/Attribute 節點比對函式名」的掃描法不會誤把它當成一次「提到」,不會誤報共用用戶端模組自己。`src/rtb/analyzer/` 底下其餘檔案(`flow.py`、`task_store.py`、`policy.py`、`instrumented.py`)與 `src/rtb/domain/*.py`、`src/rtb/sqlitekit.py` 完全沒有出現 `request_json` 或匯入 `rtb.httpclient`(全部 grep 0 筆),載入分析行程後會被帶進 `sys.modules` 的本專案模組集合裡沒有既有違規。

## 觀察:「包進 lambda」與「偏函式包一層」在規則的演算法下不會漏放行也不會誤傷合法用法

規則的正確實作方式是「函式名(或屬性存取)的每一次出現,要嘛就是某個 `Call` 節點的 `func` 位置,要嘛就違規」,這個判準跟外層有沒有包一層 `lambda` 無關——`lambda: request_json(url, "GET", None, t)` 裡的 `request_json(...)` 本身仍是一個直接呼叫節點,會被正常驗證(方法是不是字面常數、有沒有帶 headers);而 `functools.partial(request_json, method="POST")` 這種寫法裡 `request_json` 出現在參數位置而非 `Call.func`,依上述判準會被判定違規,不需要另外特判「這是包在 partial 裡」。我在原型腳本裡也驗證了改名匯入(`from rtb.httpclient import request_json as rq` 再呼叫 `rq(...)`)——只要掃描器追蹤 import 別名到後續的 `Name` 使用點(不是單純比對「request_json」這串文字本身有沒有出現在呼叫式裡),一樣抓得到、抓得對。這幾種手法都不是規則本身的漏洞,是提醒實作者:掃描器不能用「文字有沒有出現」這種粗糙做法,要用「符號在語法樹上的位置」判斷,快照裡「以任何形式提到」、「一律紅」這幾句話已經隱含這個要求,但沒有明講「因此需要追蹤 import 別名」,跟 F1、F2 是同一類「文件沒替實作者把 import 語意的坑鋪平」的精度問題。
