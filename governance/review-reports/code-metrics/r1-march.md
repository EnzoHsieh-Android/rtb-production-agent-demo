severity: minor

# r1-march 架構對齊席報告

範圍:對照 src/rtb/dsp/(store、server、errors)與設計計劃「程式結構原則(clean code)」,看新增內容有沒有引入第二種做法或跨層直呼。

## 對齊良好處(不列 finding)

- 分層一致:業務規則(window 只能 1h、1d、7d,廣告不存在,無資料列)全在 store.get_metrics,跟既有 `_validate` 放在 store 同一種做法;server 的 `_get_metrics` 只做 HTTP 參數解析並轉呼叫 store。store 沒有混入 HTTP。
- 錯誤階層一致:MetricsNotFound 繼承 PermanentError,並登記進 ERROR_TABLE(404, 代碼 metrics_not_found),與 CampaignNotFound 同一套機制;與 campaign_not_found 是不同代碼,沒有混淆。
- 資料型別一致:MetricsRecord 是 frozen dataclass、docstring 中文,seed_metrics 對應既有 seed_campaign。
- 領域層用結果型別、DSP 用例外的差異有合理理由:指標的空值是預期中的資料狀態,DSP 錯誤是協定失敗。理由記在 `確定性指標計算.md` 的 WHY 行,但沒有明講這是與 DSP 刻意不同(見 finding 4)。
- 沒有重造既有工具:指標函式是新增,沒有與 store 或 server 重複的輔助函式。
- 純判斷與外部動作分開、依賴方向往內:metrics.py 只 import math 與 dataclasses,符合計劃。

## Findings

### 1. banned-api 生效範圍是「除了 dsp 與 tests 以外全部」,不是「只有領域層」

severity: minor
blocking: 否 目前 src/rtb 只有 domain 與 dsp,尚無實際誤傷,是潛在問題。

- 位置:pyproject.toml 的 `[tool.ruff.lint.flake8-tidy-imports.banned-api]` 與 `[tool.ruff.lint.per-file-ignores]`。
- 引句:「DSP 與測試本來就要用這些,所以在它們的路徑上關掉這條規則。」
- 問題:ruff 的 banned-api 是全域清單,新設定用「在 dsp 與 tests 關掉」反向表達。計劃已預告會有「DSP 客戶端」層(要用 http.client 或 urllib)、模型客戶端層(要用 anthropic),它們會被這條規則誤擋,得再各自加 per-file-ignores,等於每加一層就多一個例外路徑。
- 重現:在領域層以外的新路徑放 import sqlite3,規則照樣觸發。
  指令:`echo "import sqlite3" | .venv/bin/python -m ruff check --config pyproject.toml --stdin-filename src/rtb/client/_p.py -`
  輸出:含 TID251,計數為 1(我用 grep -c 驗證)。
- 較貼合意圖的做法:把清單放到 src/rtb/domain/ 底下自己的 ruff 設定(ruff 支援階層設定),或至少在註解寫明「新增非領域層要在這裡加例外」。
- 佐證:`/Users/enzo/rtb-production-agent-demo/pyproject.toml:12-27`

### 2. 禁用清單比計劃寫的窄,第三方網路與資料庫客戶端沒被擋

severity: minor
blocking: 否 屬機械守衛覆蓋率,不是慣例衝突;與計劃寫的清單有落差。

- 位置:pyproject.toml banned-api 清單。
- 引句:「"urllib.request".msg = "領域層不得碰網路"」
- 問題:計劃文字列的是「urllib」與「模型客戶端」,實作只禁 urllib.request。測試只驗那六個模組。
- 重現(我在 domain 路徑下逐一餵入):`import urllib3`、`import requests` 都沒有 TID251(計數 0);`from urllib import request` 與 `import http.server` 有被抓到(計數 1)。所以只有標準函式庫網路模組有守衛。
- 補充:專案「零依賴」家規下,第三方客戶端目前確實不會出現,此條降為 minor;若要嚴格,可加 requests、httpx、urllib3、aiohttp 或改禁 `urllib`。
- 佐證:`/Users/enzo/rtb-production-agent-demo/docs/rtb-production-agent-demo-knowledge/Projects/RTB_Agent_Phase0架構.md:233`

### 3. server 內對請求路徑出現第二種解析方式

severity: minor
blocking: 否 行為目前等價,只是同一處理器內有兩套解析。

- 位置:src/rtb/dsp/server.py 的 `_route` 與新增的 `_get_metrics`。
- 引句:「windows = parse_qs(urlsplit(self.path).query).get("window", [])」
- 問題:既有路由用 `self.path.split("?")[0]` 取路徑,新程式用 urlsplit 取查詢字串。同一個 self.path 在同一個類別裡被兩種不同方法拆。輸入 `/campaigns/c1/metrics?window=1d#x` 時,路由把 `#x` 留在查詢部分,而 urlsplit 會把它當 fragment 去掉,兩邊對「哪一段是什麼」的定義不同。
- 建議:把路徑與查詢一次拆好(例如 `_route` 用 urlsplit 取 path,並把 query 交給 handler),讓解析只有一處。
- 佐證:`/Users/enzo/rtb-production-agent-demo/src/rtb/dsp/server.py:156` 與 `:196`

### 4. MetricResult.reason 是裸字串常數,與 DSP 的型別化做法不同,且這個差異沒有明寫

severity: minor
blocking: 否 屬慣例一致性,沒有已知的錯誤行為。

- 位置:src/rtb/domain/metrics.py 的 NO_DENOMINATOR 等常數與 MetricResult。
- 引句:「NO_DENOMINATOR = "no_denominator"」
- 問題:專案在 DSP 層的立場是「呼叫端靠型別分辨」(errors.py 的類別階層);領域層的原因卻是 `str | None`,拼錯字或傳入任意字串型別檢查抓不到,MetricResult(value=1.0, reason="x") 這類自相矛盾的狀態也造得出來。這可以是合理取捨(結果型別輕量),但筆記只講了為何不用 0,沒講為何和 DSP 的型別化錯誤不同、也沒說何時要升級成 Enum 或 StrEnum。
- 建議:改 StrEnum,或在 `確定性指標計算.md` 加一行取捨與回頭條件。
- 佐證:`/Users/enzo/rtb-production-agent-demo/src/rtb/dsp/errors.py:1`

## 各檔

- docs 兩篇筆記:已讀,無 finding(除 finding 4 的補記建議)。
- tests/dsp/test_store.py、tests/dsp/test_server.py:已讀,無 finding。
- tests/domain/test_metrics.py:已讀,無 finding。以 subprocess 呼叫 ruff 與 test_store 既有以 subprocess 檢查匯入的做法同類,沒有引入第二種做法。
- src/rtb/dsp/errors.py:已讀,無 finding。

總結:最嚴重等級為 minor,blocking 條數為 0。
