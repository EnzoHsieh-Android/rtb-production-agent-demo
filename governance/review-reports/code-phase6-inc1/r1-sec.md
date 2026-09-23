severity: major

### 1. 總曝險擋下時的停下紀錄可被同一筆交易的回滾吞掉,且拿掉這道收據檢查全測試仍綠(假綠)

severity: major
blocking: 是 破壞 `record_stop` 自稱「只增不改、不清理」的持久證據合約;變異測試證實這個分支對收據失效的防線沒有任何測試守著

引句:「if not self.store.ack_blocked(tx, receipt, now, code):」

說明:`execution.py` 的 `_take()` 在 `attempt_store.begin()` 丟出 `AggregateLimitReached` 時,先呼叫 `self.store.record_stop(...)` 寫入停下紀錄,再呼叫 `self.store.ack_blocked(...)`;若 `ack_blocked` 回傳 `False`(收據失效),程式碼緊接著 `raise LeaseLost(...) from full`——但這個 `raise` 仍在 `with self.store.transaction() as tx:` 之內,而 `immediate_transaction`(`src/rtb/sqlitekit.py:54`)的規則是「任何例外都回滾」,所以連同前面剛寫入的 `write_stops` 那一列也會被一起撤銷。這跟同一支函式裡緊鄰的 `TooManyUnresolved` 分支的寫法不一致:那個分支故意不檢查 `self.store.release(...)` 的回傳值,正是為了不讓收據失效把已經寫下的停下紀錄捲進回滾(對照 `execution.py:438-442`)。

實驗(輸入 → 預期 → 實際):把 `InboxStore.ack_blocked` monkeypatch 成只在 `code == aggregate_limit_reached` 時回傳 `False`(模擬「收據剛好在這個時間點失效」),提交一筆會被總曝險擋下的提案。
- 輸入:租戶門檻 10、提案要佔用 50 的加預算,`ack_blocked` 對這個分支強制回傳 `False`。
- 預期(依 `record_stop` 文件字面承諾「同一份提案同一種類只記一列」):`write_stops` 應該有恰好一列。
- 實際:`write_stops` 為空、`attempts` 為空,`proposals` 那一列的 `disposition` 停在 `in_progress`(收據等於作廢,下一輪會被重新取件),完全沒有留下這次總曝險擋下的證據。

另外用變異測試驗證這條防線本身沒有測試守著:把 `if not self.store.ack_blocked(...): raise LeaseLost(...) from full` 改成跟 `TooManyUnresolved` 分支一樣、直接呼叫但不檢查回傳值,`tests/executor/` 整套 363 個測試全部維持綠燈(不是變紅),包含新寫的 `test_every_stop_leaves_one_durable_record_per_proposal`(它沒有覆蓋收據失效這條路)。

reachability 的誠實說明:目前程式碼的交易模型下,同一個 `BEGIN IMMEDIATE` 交易由頭到尾握著排他寫入鎖,`extend()` 成功之後、同一個 `now` 底下 `ack_blocked` 理論上不會失敗,所以這條分支在目前寫法下我沒能從外部(攻擊者可控的並行請求)構造出真正觸發它的路徑——這點作者在計劃裡自己也承認「S336(並行合約)沒有對應變異」正是同一類「寫入鎖外讀額度」的擔心。但這正是判準裡「測試在被守的程式拿掉後仍然綠」的情況:防線存在卻沒有測試證明它必要,而且一旦未來重構(例如把 `extend`/`ack_blocked` 拆成兩段、或這段交易時間拉長到跨過收據可能被接手的窗口)使它變得可觸發,後果就是「提案反覆重試又沒留證據」——恰好是本次審查被指名要查的攻擊面。建議至少讓這個分支比照 `TooManyUnresolved` 分支不檢查 `ack_blocked` 的回傳值,或把 `record_stop` 移到 `ack_blocked` 確認成功之後,以免持久證據的承諾被交易語意就地推翻。

file: `src/rtb/executor/execution.py:443-449`
file: `src/rtb/executor/inbox_store.py:618-635`
