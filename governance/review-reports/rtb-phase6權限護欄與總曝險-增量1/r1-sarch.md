severity: major

### 1. 新設一張「預留表」偏離既有「begin() 時的不變事實掛在嘗試紀錄第 1 列」慣例
severity: major
blocking: 是 屬於審查鏡頭明列的第一項(預留表與嘗試紀錄的關係),且有具體、可驗證的架構分家證據
引句:「執行行程的資料庫新增一張預留表,每把鍵最多一列:冪等鍵(主鍵)、租戶、廣告、預留金額、預留時間,只增不改」

說明:設計要存的四個欄位——冪等鍵、廣告、預留金額、預留時間——其中「鍵」「廣告(campaign_id)」「時間(written_at)」三個在 `attempts` 表的第 1 列早就有了,而本專案處理「begin() 那一刻算出、之後永遠不變、只跟這把鍵綁一次」的新事實時,既有慣例是用 `ADDED_COLUMNS`/補欄位機制把它加進 `attempts` 表,不是另開一張表。証據:`attempt_store.py` 自己的說明——「第 1 列另外帶任務、修訂、動作、預期版本與完整提案快照:收件口會清掉過期提案,重新授權要有自己的依據」(file 見下),以及 Phase 3 增量 3 就是照這個模式,把「憑證到期時間」「寫入後版本」用 `ADDED_COLUMNS` 加進同一張表,而不是另開表。

照字面「另開一張表」實作會造成兩個具體、跟現有做法對不上的後果:①F7 的稽核查詢(算某租戶 24 小時內預留總額、扣掉已失敗的鍵)從「單表查詢」變成「兩表 JOIN」,而這張新表沒有 `ADDED_COLUMNS`/收件表 CHECK 重建那一套既有維護機制可用,是全新一條要重新設計的維護路徑;②設計完全沒指名這張新表的 schema 由哪個模組建立——現有架構的唯一規則是「收件口資料庫模組同時是整個執行行程資料庫唯一開連線、開交易的地方:它也建外部寫入嘗試的表」,新表如果不掛進同一套建表/連線責任(`inbox_store` 的 `SCHEMA + attempt_store.SCHEMA` 组合),就會出現第二個「誰負責開表」的例外。這兩點都是設計層級沒說清楚、字面實作容易做錯的架構缺口。
file: `src/rtb/executor/attempt_store.py:5-6`
file: `src/rtb/executor/attempt_store.py:61-65`
file: `docs/rtb-production-agent-demo-knowledge/Systems/提案收件口.md:26`

### 2. 「跟憑證一起帶回(同一次讀設定檔的結果)」沒有配套介面設計,字面實作容易把讀檔案塞進全域寫入鎖交易內
severity: major
blocking: 是 屬於審查鏡頭第三項(設定檔讀取與停機分類)的介面缺口,且會打破「盡量少在寫入鎖內做事」的既有效能假設,S336/S340 的多工作者測試會因此明顯變慢或需要重新設計
引句:「跟憑證一起帶回(同一次讀設定檔的結果);開始一筆時算已用額度,加上這筆會超過門檻」

說明:現有 `Signer` Protocol 的 `sign()` 只回傳 `str`(見 file),`CapabilitySigner.sign()` 內部讀到的 `Tenant`(名稱、上限)算完就丟掉,沒有任何管道把「租戶名稱與門檻」帶出來給 `execution.py` 的 `_take()` 用。要做到設計說的「同一次讀設定檔的結果」,字面上只有兩條路:(a) 改 `Signer.sign()` 的回傳型別,把門檻一起帶出來——但合約 S330-S340 完全沒提到要動這個介面;(b) 在 `_take()` 的交易裡(拿到 SQLite 立即寫入鎖之後)另外呼叫一次 `capability_signer.load_tenants()`。

現有架構從沒有在「已開交易」的狀態下呼叫讀設定檔的程式——`_sign()` 在 `_process()`、`_after_expiry()`、`_reconcile_not_found()` 三處都是在交易外呼叫的,而「開始一筆」的寫入鎖本來就讓所有工作者排隊(`attempt_store.py`:「這樣『取件與開始一筆』能放在同一個交易裡,並行檢查也一定在寫入鎖之內」),`執行迴圈.md` 也明講「呼叫 DSP 期間不開任何資料庫交易」這條盡量少佔鎖的原則。若字面實作走(b),`capability_signer._read_config_securely` 那一串開目錄、開檔、`fstat`、讀檔案的系統呼叫,就會被塞進這個所有工作者互斥排隊的臨界區——S340「3000 個廣告」端到端測試會因為每一次「開始一筆」都多一次檔案 I/O 而明顯變慢,且沒有任何合約或測試釘住「這個查核不得在交易內重讀設定檔」,是字面實作容易做錯、又沒有既有慣例可循的架構缺口。
file: `src/rtb/executor/execution.py:115-120`
file: `src/rtb/executor/attempt_store.py:7-11`
file: `docs/rtb-production-agent-demo-knowledge/Systems/執行迴圈.md:26`
file: `src/rtb/executor/capability_signer.py:51-74`
