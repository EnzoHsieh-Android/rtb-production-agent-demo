severity: major
<!-- 來源:codex exec --sandbox read-only,取 stdout 最後一次完整輸出;原文未改 -->

## F1 完整性斷言會把總曝險代碼誤當成單筆護欄

severity: major

blocking: 是 — 增量 1 合併後，列舉會多出「總曝險已滿」，但規則表沒有這一列，照字面實作完整性測試必然失敗或被迫削弱斷言。

引句:「列舉裡每一個擋下原因在表上都至少有一列擋下、一列通過。」

現有測試確實以整個 `BlockCode` 列舉作為完整集合，而不是只取單筆規則：`tests/executor/test_execution.py:152`、`tests/executor/test_execution.py:162`。增量 2 又明定以增量 1 已合併為前提，因此不能只按目前六個成員推理。

具體例：增量 1 合併後列舉包含 `aggregate_limit_reached`，護欄表仍只有六列。預期是完整性測試只要求「單筆護欄集合」有通過與擋下案例；照目前條款則要求總曝險代碼也出現在單筆表中，但設計同時宣告增量 2 不做總曝險。應明定單筆護欄代碼集合，或把總曝險列納入表格及其案例。

## F2 規格要求的 0→1 放行在真實執行路徑到不了 precheck

severity: major

blocking: 是 — 表格測試使用替身 DSP 可以通過，但正式 DSP 用戶端會先把目前預算 0 判成回應不可讀，造成規格要求放行的提案延後並最終可能進死信。

引句:「現況 0 → 1 過、2 擋;現況 100 → 1(減)過;暫停一律過。」

DSP 可以種入預算 0，種資料入口沒有正數檢查：`src/rtb/dsp/store.py:272`。分析端也把 0 視為合法現況：`src/rtb/analyzer/dsp_client.py:67`。但執行端 DSP 用戶端只接受大於 0 的預算，讀到 0 會拋出 `DspUnavailable`，根本不會呼叫比例檢查：`src/rtb/executor/dsp_client.py:30`、`src/rtb/executor/dsp_client.py:47`。正式處理流程遇到這個例外會釋放租約，而非放行：`src/rtb/executor/execution.py:357`。

具體例：DSP 現況為 `budget=0, status=active, version=1`，提案為 `new_budget=1, observed_version=1`。設計預期通過並寫入 1；現況實作會回讀取失敗，沒有簽發或寫入。設計必須選定其一：讓正式用戶端接受零預算並補正式路徑測試，或刪除 0→1 的放行承諾並說明零預算不屬於執行端合約。

## F3 舊資料庫重建被假定能自動涵蓋下一個新代碼

severity: major

blocking: 是 — 增量 1 的條款只要求辨識它自己的新代碼，不能保證增量 2 加入新代碼時仍會觸發重建。

引句:「增量 2 沿用,不再改那段程式,只驗新代碼在重建後寫得進去。」

目前的重建判斷正是硬編碼檢查一個既有成員，而非比較完整列舉或完整欄位定義：`src/rtb/executor/inbox_store.py:289`。資料表的 `CHECK` 則在建表時由當下列舉展開並永久寫死：`src/rtb/executor/inbox_store.py:96`。

具體例：資料庫由增量 1 建立，限制已包含 `aggregate_limit_reached`，但尚無 `budget_increase_too_large`。若增量 1 按自己的條款只把重建哨兵改成檢查前者，增量 2 開啟資料庫時會判定不需重建；之後寫入比例擋下代碼便觸發 SQLite `CHECK` 錯誤。預期是開啟時重建並保留舊列。增量 1 必須新增明確合約：重建判斷要核對所有目前的 `BlockCode` 成員；否則增量 2 必須允許修改遷移程式，不能宣告完全沿用。

比例公式、版本檢查先於比例檢查，以及權限類原因合併為 `not_permitted`，都與現有流程及使用者裁定一致。

指定測試未能啟動：唯讀環境沒有可寫的暫存目錄，pytest 在收集前即因 `TemporaryFile` 失敗；沒有改動 repo。
