severity: minor

### 已看:模擬 DSP 對 window=1h 的正常回應會不會被誤殺(①)
讀了 `src/rtb/dsp/store.py` 的 `get_metrics`/`seed_metrics` 與 `src/rtb/dsp/server.py` 的 `_get_metrics`:`window_name` 是查詢用的資料表鍵(`WHERE campaign_id = ? AND window_name = ?`),回應裡的 `window` 欄位就是請求時帶的那個字串,不是伺服器另外推算出來的值,正常路徑下一定等於請求值,不會被新檢查誤殺。
DSP 沒有 1h 指標時,`store.get_metrics` 找不到列會丟 `MetricsNotFound`,`server.py` 的 `_STATUS_MAP`(第 73 行)把它映成 HTTP 404;`dsp_client._get` 先檢查 `status != 200` 就丟 `DspRequestFailed`(`dsp_client.py` 第 118~119 行),根本不會走到 `_trusted` 裡新的 `window == REQUESTED_WINDOW` 檢查。也就是「查無資料」跟「時間窗不合格」是兩條不同的失敗路徑,彼此沒有交互,兩者都導向同一種上層結果(整批讀取失敗、留在原狀態重試),行為上等價,不會有「明明有資料卻被新檢查誤判成查無資料」這種混淆。

### 已看:請求路徑與檢查是不是同一個常數(②的主要部分)
`dsp_client.py` 裡請求 query string(`metrics_path = f".../metrics?window={REQUESTED_WINDOW}"`,第 66 行)跟白名單檢查(`"window": lambda value: value == REQUESTED_WINDOW`,第 42 行)用的是同一個模組級常數 `REQUESTED_WINDOW`(第 19 行),沒有另外寫死字串,這支檔案內部是單一來源。

## F1 `policy.py` 的 1 小時換算跟 `dsp_client.py` 的 `REQUESTED_WINDOW` 沒有共用同一個來源,只靠註解互相呼應
severity: minor
blocking: 否 — 目前兩邊常數值都固定對應「1 小時」,行為一致,pytest 全綠;只是缺乏機械綁定,不是這次 diff 造成的現有壞行為
引句:「用戶端只要 1 小時的指標(示範規則也只按 1 小時換算);回應的時間窗跟請求的不同就當可信欄位不合格」
這行新加的註解(`dsp_client.py` 第 17 行)把「示範規則也只按 1 小時換算」當成佐證寫進來,但實際上兩處是兩個獨立寫死的值:`dsp_client.py:19` 的 `REQUESTED_WINDOW = "1h"` 只管請求與白名單檢查;`policy.py:30` 的 `ELAPSED_FRACTION_1H = 1 / 24`(`pacing()` 用它換算配速)是另一個完全獨立的字面常數,兩者之間沒有任何 import 或型別把它們綁在一起,也沒有測試在兩者之間打樁。如果以後有人只改 `REQUESTED_WINDOW`(例如換成 "1d")卻忘了同步改 `policy.py` 的換算比例,`_trusted` 檢查依然全綠(因為它只跟自己請求的窗口比對),但 `pacing()` 會拿「一天的指標」硬套「一小時的預算佔比」算配速,悄悄算出錯誤的配速結論而不會有任何測試變紅——這是註解暗示「兩邊已經對齊」但程式碼層面其實沒有機械保證的落差。目前值仍然一致,不影響這次合入,但兩邊沒有共用常數這件事本身就是後續改動容易踩到的坑,建議至少留一條 `REVISIT` 或把 `policy.py` 改成引用 `dsp_client.REQUESTED_WINDOW`/衍生值。

### 已看:新測試參數是否真的咬得住(③)
在暫存目錄複製一份 `src`/`tests`,把 `METRICS_FIELDS["window"]` 的檢查改回舊的 `isinstance(value, str) and value in {"1h", "1d", "7d"}`(等同拿掉這次收緊)後重跑 `tests/analyzer/test_dsp_client.py`:
```
FAILED tests/analyzer/test_dsp_client.py::test_a_malformed_trusted_field_fails_the_whole_fetch[metrics-window-7d]
FAILED tests/analyzer/test_dsp_client.py::test_a_malformed_trusted_field_fails_the_whole_fetch[metrics-window-1d]
2 failed, 33 passed in 1.09s
```
把檢查換回這次的 `value == REQUESTED_WINDOW` 後,同一份檔案 `35 passed`。確認新加的 `("metrics", "window", "7d")`、`("metrics", "window", "1d")` 這兩個參數化案例真的咬得住這次的改動,拿掉檢查會翻紅。

### 已看:計劃合約句 S205 與筆記跟程式是否一致(④)
`git -C /Users/enzo/rtb-3b diff 004a7de..HEAD -- docs/` 顯示:
- `RTB_Phase7提示注入與信任邊界_計劃.md` 的 S205 合約句已從「狀態不在已知清單、或廣告編號跟任務的不同」改成中間多插「時間窗跟請求的不同」,跟程式行為(`window` 不等於 `REQUESTED_WINDOW` 就進 `bad` 清單、整批失敗)一致。
- 同一篇「## 增量 1 設計」的指標可信欄位敘述也把「時間窗(1h/1d/7d)」改成「時間窗(必須等於用戶端請求的 1h;2026-09-23 使用者裁定收緊,原本寫 1h/1d/7d 任一個,DSP 對 1h 的請求回 7d 也會被當成可信收下)」,跟程式與新註解一致,也補了一條「已裁定(2026-09-23,增量 1 代碼審帶出):指標的時間窗收緊成「等於用戶端請求的時間窗」」的決策記錄。
- `Systems/分析行程流程與檢查點.md` 裡管這支檔的 `RULE:` 行也同步把「時間窗(1h/1d/7d)」改成「時間窗(必須等於請求的 1h,2026-09-23 使用者裁定收緊)」。
這幾處筆記跟這次程式改動的敘述對得上,沒有發現落差;不過此次審的 diff(`r1-snapshot.patch`)本身只涵蓋 `src/rtb/analyzer/dsp_client.py` 與 `tests/analyzer/test_dsp_client.py` 兩支檔,docs 的改動是同一段 commit 歷史裡的另一批寫回,不在這份 patch 引句範圍內,上面的比對是用 `file:` 佐證方式核對,不算 diff 內 finding。

⚠ 交編排者:F1 是設計層面的耦合風險而非這次 diff 引入的錯誤,已列為 minor/不擋;若編排者認為「示範規則」階段不值得為此加一層綁定,可以直接接受不處理,但建議之後有人動 `REQUESTED_WINDOW` 時要連帶檢查 `policy.py` 的 `ELAPSED_FRACTION_1H`。

最終結論:本輪沒有發現 blocker 或 major,共 1 條 minor、不擋。
