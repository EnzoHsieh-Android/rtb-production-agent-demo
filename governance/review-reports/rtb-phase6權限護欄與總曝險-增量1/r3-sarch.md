severity: clean

查了什麼:針對凍結快照「## 增量 1 設計:總曝險預留與事故 F7」一節,逐字核對第 3 版設計文字與現有程式的六個架構對齊點——

1. **新部分索引只用既有欄位**:設計明寫「新增一個部分索引:只收已驗證列、按寫入時間;只用既有欄位」(針對 `state`、`written_at` 這兩個既有欄位),對照 `src/rtb/executor/inbox_store.py` 的 `SCHEMA`(先整份 `executescript` 建表建索引、後在 `_migrate_columns()` 補欄位)與 `src/rtb/executor/attempt_store.py:45-58` 既有三個部分索引,確認新索引不會重蹈第 2 輪抓到的「索引參照 Phase 6 才補的欄位」那個 `no such column` 錯誤。
2. **補欄不回填**:設計把預留欄位(租戶、預留金額)明寫「不回填」,比照 `attempt_store.ADDED_COLUMNS`(`written_version`、`capability_expires_at`)與 `inbox_store._ADDED_COLUMNS`/`_missing_columns()` 既有的補欄位機制,第 1 輪已把原本「另開一張預留表」的設計改成掛在嘗試紀錄第一列,現版本與此機制一致。
3. **擋下紀錄表**:設計明寫「由收件口模組統一建表」,對照 `Systems/提案收件口.md` 的既有規則「收件口資料庫模組同時是整個執行行程資料庫唯一開連線、開交易的地方…它也建外部寫入嘗試的表」,新表是全新表(用 `CREATE TABLE IF NOT EXISTS`)而非既有表加欄位,不會撞上索引/補欄位的次序問題。
4. **簽發介面回傳改法**:設計明寫簽發器回傳「多帶租戶名稱與總額上限」,對照 `src/rtb/executor/execution.py` 的 `Signer` Protocol 與 `DspPort` 既有用 `CampaignView`/`WriteAnswer`/`OperationRecord` 等 dataclass 包裝回傳值的慣例,且 `_sign()` 在交易外呼叫(對照 `_process()` 內 `signed = precheck(...) or self._sign(proposal)` 在 `_take()` 交易之前),與「簽發時讀設定檔(在交易外)、開始一筆交易裡用這個值比對」的設計語意一致。
5. **缺欄與不合法的分流**:設計「缺這欄→這個租戶門檻當 0,其他租戶照常」對「值不合法→整份設定檔不合法」的分流,對照 `capability_signer._parse_tenants()` 既有的逐租戶迴圈驗證結構(目前所有欄位都是「不合法就整份丟 SigningRefused」),新欄位只在「缺欄」這一種情況特意分流、不影響既有迴圈對其他欄位或其他租戶的既有行為,結構相容。
6. **跟增量 2 的護欄表與三份清單斷言接不接得上**:核對增量 2 「單筆規則代碼」(6 個)+「非單筆代碼」(2 個,含「增量 1 的總曝險擋下原因」)+「歷史相容代碼」(現在是空的)= 8 個,與增量 1 最終會使 `BlockCode` 列舉共有既有 6 個加增量 1 新增 1 個(總曝險已滿)加增量 2 新增 1 個(單筆加預算超過比例上限)= 8 個成員數一致,聯集覆蓋、無交集;且增量 1「跟其他增量的銜接」段與增量 2「實作前提」段對 [S338]/[S406] 的依賴關係雙向都有寫明,順序(增量 1 先合併)一致。

第 1、2 輪設計審的架構對齊席已用實際重現(SQLite `no such column`、簽發介面缺口、另開表偏離補欄慣例)抓到三個 major 問題,均已折入第 3 版並在快照中可見對應修正文字;本輪重新核對現況與程式碼,沒有找到這六個範圍內的新分家點。
