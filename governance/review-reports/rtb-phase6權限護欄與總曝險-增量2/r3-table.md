severity: minor

本輪鏡頭:規則表、三份清單(單筆/非單筆/歷史相容)與回退。逐項核對「增量 2 上線」與「增量 2 回退後」兩個狀態下 [S404] 的聯集/交集斷言是否都成立、BLOCK_TRIGGERS 被取代後「同一操作先前已失敗」的覆蓋有沒有丟、規則表在順序與迴避上照字面做不做得出。

## 兩個狀態下的集合算式都驗過,成立

引句:「三份明列清單的聯集應等於擋下原因列舉、兩兩交集應是空的」

實際核對 `src/rtb/executor/inbox_store.py:73-81` 現有 `BlockCode`(6 個成員)加上增量 1 的 `aggregate_limit_exceeded`(依計劃 [S330]-[S340] 節)與增量 2 新成員 `budget_increase_too_large`,共 8 個成員。

- 增量 2 上線:單筆規則代碼 6(表上六列)+ 非單筆代碼 2(`operation_previously_failed`、`aggregate_limit_exceeded`)+ 歷史相容代碼 0 = 8,兩兩不重疊,護欄表代碼集合等於單筆規則代碼集合。成立。
- 增量 2 回退後:單筆規則代碼變 5(拿掉 `budget_increase_too_large`)+ 歷史相容代碼 1(接住 `budget_increase_too_large`)+ 非單筆代碼仍 2 = 8,兩兩不重疊,護欄表(拿掉比例那幾列後剩 5 列)仍等於單筆規則代碼集合。成立。

這是第 2 輪 compat 席抓到的 blocking 問題(`r2-compat.md` F1:回退只挪表格列、沒處理三份清單的第三份,聯集斷言會紅)在本輪(r3)的修正:新增「歷史相容代碼」清單、把 `budget_increase_too_large` 在回退時搬過去,兩個狀態都驗算過,沒有破綻,已折平。

## BLOCK_TRIGGERS 被取代後,「同一操作先前已失敗」的覆蓋有搬,但沒有掛編號合約

引句:「同一操作先前已失敗原本在那張表的觸發,搬成邊界表之外的一支單獨測試,不丟覆蓋」

核對 `tests/executor/test_execution.py:152-179`:現行 `BLOCK_TRIGGERS` 是唯一同時斷言「`OPERATION_PREVIOUSLY_FAILED` 這條規則真的會被觸發、且結果是 `Result.IDLE`(不是 `BLOCKED`)」的地方(`_previously_failed` 觸發函式、第 175-179 行的雙分支斷言)。全域搜尋確認 `BLOCK_TRIGGERS` 只在這一支檔案內使用(`grep -rl BLOCK_TRIGGERS` 除治理報告外只有這一處),换成邊界表不會有其他檔案的殘留引用。快照這句話把去路交代清楚(搬成獨立測試),字面可執行、也對上第 2 輪的顧慮;但 [S400]-[S408] 沒有任何一條掛這支「獨立測試」,不像其他覆蓋都有 `[test:]` 綁定的合約編號。若之後有人只照 S400-S408 逐條核對完成度,會漏掉這一句純散文指示,這支測試被漏寫也不會被任何機械斷言擋下(`[S404]` 的聯集/交集斷言不要求非單筆代碼要有觸發測試)。這一句本身可執行、方向對,只是傳遞性比其他項弱,列為文件精度問題。

## 規則表順序與迴避,照字面做得出來

核對 `src/rtb/executor/execution.py:280-287`(`precheck`,`!=` 比對版本,對應「版本已變的成立側」的「現況比觀察到的舊」也要擋,已修正 r1→r2 間「不成立側」的筆誤)與 `src/rtb/executor/capability_signer.py:110-119`(`sign` 先查 `_tenant_of` 對應 `campaign_not_allowed`,再查 `max_budget` 對應 `over_budget_cap`)。表上 1-4 列全在 `precheck`(依碼序執行、命中即回),5-6 列全在 `sign`(`precheck` 先跑完才會呼叫 `sign`),兩處合起來自然保證「同時成立回表上最前一條」([S405]),不需要額外的跨模組協調。數值規則邊界列迴避其他規則的做法(比例列把單一廣告上限設高、上限列把現況預算設高且加量在比例內;四條存在/等值規則的通過側同時滿足比例內與低於上限)在現有 `tests/executor/fakes.py` 的 `Harness`/`write_config` 上可以直接組出對應輸入,沒有發現迴避不了的組合。

沒有發現會擋的問題;上述兩點均屬已折入或文件精度層級。
