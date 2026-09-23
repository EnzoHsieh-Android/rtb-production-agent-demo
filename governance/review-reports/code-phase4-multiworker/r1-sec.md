severity: clean

查了什麼(全程站在攻擊者角度,鎖定 src/tests 這批 patch 改到的三支檔:attempt_store.py、execution.py、runner.py,對照設計文件 S127–S136):

1. **雙寫預算**:`begin()` 的 `CampaignLocked` 檢查與 `transition()`/`_current()` 的序號 CAS,都在 `store.transaction()`(BEGIN IMMEDIATE)同一個立即取得寫入鎖的交易裡完成,SQLite 對寫入者完全序列化;逐步追蹤兩個工作者同時 `begin()` 同廣告、同時 `_write()` 同一把鍵的交易順序,兩邊都會有一方在重新讀到最新列後被擋下(CampaignLocked 或 CAS 回 None),不會出現兩份寫入都落地。S130/S131 的並行測試(含變異檢查:拿掉互斥就要翻紅)對應覆蓋。
2. **重啟恢復誤殺活著的工作者**:核對「一筆嘗試中在它存在的每一刻都有對應的處理中訊息」這個不變量——`receive()`(建訊息)一定在 `begin()`(建嘗試列)之前的交易裡先提交,`_recover()` 又把 `in_progress_keys` 與 `recover_in_flight` 包在同一個交易讀一致快照,逐一驗證多個並發重啟/實際 worker 交錯的時序都維持這個不變量,找不到 held 集合會漏掉活著嘗試的路徑。S127/S129/S133 測試直接斷言 held 鍵在重啟後原封不動。
3. **舊鍵序號對不上的放棄 vs 停機分流**(`_no_progress`):確認「有收據」分支只有在 `extend()` 已通過(收據仍有效)之後 CAS 仍失敗才會走到,追蹤所有呼叫路徑,唯一能造成這種情況的是繞過租約直接寫嘗試紀錄——沒有找到多工作者正常競爭會誤觸系統停機的路徑;`test_a_sequence_mismatch_with_a_valid_receipt_still_halts` 直接單元測試兩個分支。
4. **忙碌重試被利用來拖住系統**:`_loop` 的 `busy_streak` 與 `_open_and_recover` 的 `range(BUSY_LIMIT)` 都是各自獨立交易、忙碌時的交易本來就回滾,不會留半筆;連續 3 次/3 輪的門檻與「一輪成功即歸零」都照設計文件,且是已知留了 REVISIT 日期的可接受風險(不是這次改動新增的洞)。
5. **作廢後舊請求仍被套用**:假 DSP 新增的 `threading.RLock` 是否真的讓 `write()`/`void()` 互斥、不會讓失去租約的舊呼叫在作廢之後還套用——追蹤鎖的持有範圍與 S132 測試(A 失去租約後晚到的寫入必須回收到 B 已寫入的同一個結果、DSP 只套用一次),沒有找到可以讓舊請求繞過的縫隙。

沒有找到攻擊者可利用的洞、破壞合約、資料損壞或會被拿掉互斥仍呈假綠的測試。
