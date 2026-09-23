severity: clean

查了什麼(攻擊者角度,鎖定這一輪修正:忙碌代碼分流、假 DSP 鎖範圍擴大,對照 r2-delta.patch 與完整 r2-snapshot.patch):

1. **忙碌退出碼分流(`EXIT_HALTED` → `EXIT_BUSY`)**:核對 `runner.py` 的 `_loop`、`_open_and_recover` 改動——這只是把「連續 3 輪/3 次忙碌」的退出碼從系統錯誤改成獨立代碼,不影響任何互斥或資料寫入路徑;忙碌重試(`sleep(interval)` 加 `busy_streak`/`range(BUSY_LIMIT)`)的交易邊界跟 r1 一致,忙碌時交易本來就沒進展,不會留半筆、也不會把重試次數用來繞過收據或序號核對。找不到能被攻擊者利用來拖住系統或誤導監督程式的縫。
2. **假 DSP(`FakeDsp`)鎖範圍擴大**:`read_campaign`、`_lookup` 這一輪補上 `self._lock`,核對是否因此打開死結或遮住真的競態——`on_read` 沒有像 `on_write` 一樣被任何並行測試當成攔截點使用(逐一查過 `test_multi_worker.py`、`test_execution.py`、`test_reconcile.py`、`test_runner.py` 裡所有 `on_read =` 賦值,都是單執行緒情境操作,不是柵欄/阻塞點),鎖是 `RLock` 且 `apply()`/`_apply()` 重入正確,不會跟 `write()`/`void()` 既有的鎖互卡。
3. **實驗驗證(在臨時目錄跑,未動 repo)**:把 `read_campaign`/`_lookup` 還原成這一輪之前(未加鎖)的版本,重跑 `test_multi_worker.py` 30 次,全部照樣綠——證明這兩個鎖是假 DSP 內部狀態(`campaigns`/`operations`/`read_failures` 等)的執行緒安全強化,不是任何 S127–S136 合約測試在守的東西;也確認這個 venv 是標準 GIL 版 Python 3.14(`sys._is_gil_enabled()` = True),不是 free-threaded build,所以就算沒有這兩把鎖,字典讀寫也不會真的損毀,頂多是 `read_failures`/`lookup_failures` 這種「檢查後才減」的計數在極端排程下多觸發一次失敗分支(更保守,不會讓測試假綠)。鎖擴大本身沒有引入新洞,也沒有遮蔽既有的真並行合約。
4. **連帶檢查 `_no_progress`/`_write`/`_verification_timeout` 的收據分流**(這輪唯一改到生產路徑的另一處,`execution.py`):確認「有收據」分支只在 `extend()` 已通過之後才可能走到,`receipt is None` 分支只給收件表沒有處理中訊息的舊鍵用;沒找到多工作者正常競爭會誤觸系統停機、或反過來讓有收據卻被繞過的路徑,跟 r1-sec 的結論一致,這輪沒有回歸。

沒有找到攻擊者可利用的洞、破壞合約、資料損壞,或會被拿掉互斥仍呈假綠的測試。
