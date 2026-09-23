severity: clean

## 1 不可信輸入流到危險操作(owner 進 SQL 是否參數化)

已看,無。`advance()` 新增的 `owner: str = "analyzer"` 全程只流進 `TaskStore.acquire_lease()`,而 `acquire_lease` 用的是 `"INSERT INTO task_leases VALUES (?, ?, ?, ?)"`(`src/rtb/analyzer/task_store.py`)參數化寫入,`owner` 沒有被拼進 SQL 字串、也沒有被拿去組檔案路徑或指令列。而且這個 `owner` 目前唯一的呼叫來源是分析行程自己的啟動邏輯(比照執行側 `runner.py` 用 `os.getpid()`/時間戳組字串),不是外部可控輸入,沒有攻擊者能操縱的入口。

## 2 權限:固定預設擁有者能不能被另一個呼叫端冒用來繞過圍籬

已看,無(有機械證據)。表面上看,`owner` 預設值固定是 `"analyzer"`,如果分析行程還沒接上真正的啟動程式,所有呼叫端目前都共用同一個字面量;直覺會擔心「冒用同一個 owner 字串」能不能騙過租約圍籬、讓自己看起來是原持有者。但讀 `_holds()`/`_lease_allows()`/`commit_step()` 可以確認圍籬比對的鍵是 `(task_id, lease_seq)`,`lease_seq` 是資料庫端單調遞增、由 `acquire_lease()` 交易內部決定的序號,呼叫端傳進來的 `owner` 完全不參與「是否放行寫入」的判斷路徑(`_lease_allows` 對「沒帶收據」的情況只看 `current is None or not _is_live`,對「帶收據」的情況呼叫 `_holds`,而 `_holds` 雖然也比對 `owner`,但攻擊者要偽造收據必須先合法拿到那一次 `acquire_lease()` 回傳的 `LeaseReceipt`,拿不到就沒有可用的 `lease_seq`)。也就是說,就算兩個呼叫端共用一模一樣的 `owner` 標籤,誰能提交完全由誰先在 `acquire_lease()` 拿到那把遞增的 `lease_seq` 決定,`owner` 相同不會讓後來者偽造出跟先來者一樣的 `lease_seq`。本輪測試 `test_callers_sharing_one_owner_are_still_fenced_by_the_lease_sequence`(`tests/analyzer/test_task_lease.py`)直接覆蓋了這個疑慮:同一個 owner 的舊收據在新收據取得後既 `commit_step` 失敗、`release_lease` 也失敗,新持有者不受影響。程式碼裡的說明也明講這是刻意設計:「擋住舊持有者的是租約序號(每次取得都不同),不是擁有者」。因此固定/共用預設擁有者不構成繞過圍籬的路徑。

## 3 密鑰與個資

已看,無。`owner` 只是行程/工作者身分標籤(對照執行側 `os.getpid()-線程id-呼叫序號` 的舊寫法,或未來啟動程式傳入的工作者名稱),不含密鑰、token、個資;本輪 diff 沒有新增任何 log 輸出會把 `owner` 或其他欄位打印出去(`grep owner src/rtb/analyzer/*.py` 只看到寫入 SQL 與資料類別欄位,沒有 print/logging 呼叫)。

## 4 加密與隨機數

已看,無。本輪沒有新增或修改任何加密、雜湊、隨機數產生邏輯。`lease_seq` 是資料庫遞增整數,不是拿來當防偽/防猜的安全權杖用途(它只是同一個 SQLite 檔案內部的樂觀鎖版本號,威脅模型也明講「同一作業系統使用者可直接改資料庫檔」已列為已知限制、不防刻意繞過),所以序號可預測不構成安全問題。

## 5 執行邊界(測試裡的 subprocess 與環境)

已看,無。這一輪 delta 反而是把 `os.getpid()`、`threading.get_ident()`、`itertools.count()` 這些讀行程/執行緒環境資訊的程式碼整段刪掉,改成純粹接呼叫端傳入的字串參數,減少了對行程環境的依賴面。測試改動只用 `monkeypatch.setattr(TaskStore, "release_lease", broken)` 與例外注入(`_PlannedCrash`),沒有新增 `subprocess`、`os.system`、`eval`/`exec` 或任何 shell 呼叫。

## 6 行動端

已看,無。純後端 Python(SQLite 租約邏輯),與行動端無關。

## 新依賴

已看,無。diff 只新增標準函式庫 `datetime.timedelta` 的用法,沒有引入任何新的第三方套件。
