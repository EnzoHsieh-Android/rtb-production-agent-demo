severity: clean

# 資安審查:分析行程任務租約(Phase 4 增量 3b)

被審材料:`/Users/enzo/rtb-3b/governance/review-reports/code-phase4-analyzer-lease/r1-snapshot.patch`
範圍:`src/rtb/analyzer/flow.py`、`src/rtb/analyzer/task_store.py`、`tests/analyzer/test_task_lease.py`
威脅模型(如題目所述):分析行程不持有寫入金鑰;只防「忘記放租約」,不防「同一 OS 使用者刻意繞過」(已知、不在本次範圍內裁決)。

## 1. 不可信輸入流到危險操作(SQL/命令/路徑注入、反序列化、eval)

已看,無。

- 新增的 SQL 全部走參數化占位符,沒有任何字串插值組 SQL:
  - `acquire_lease`:`INSERT INTO task_leases VALUES (?, ?, ?, ?)`,參數為 `(task_id, next_seq, owner, _iso(...))`(`task_store.py:312-315`)。
  - `_append_release`:`INSERT INTO task_leases VALUES (?, ?, NULL, ?)`(`task_store.py:346-349`)。
  - `_current_lease` 的 `SELECT ... WHERE task_id = ?`(`task_store.py:329-332`)。
  - `commit_step` 新增的租約檢查 `self._lease_allows(task_id, lease, now)` 是純 Python 邏輯比對,不涉及字串拼 SQL。
- 測試裡唯一的 `subprocess.run` 呼叫(`test_a_holder_that_dies_after_paying_is_analysed_again_once_the_lease_expires`)用的是參數列表 `[sys.executable, "-c", CHILD, str(path), str(calls), NOW.isoformat()]`,不是 `shell=True`,不存在字串拼接/shell 插值;`CHILD` 是寫死在原始碼裡的常數字串,不含任何外部或測試間可控輸入,`env` 也是寫死字典 `{"PYTHONPATH": SRC}`。符合 R18(不用 shell 拼指令)。
- 沒有新的 `pickle`/`eval`/`exec`/動態匯入。
- `_is_live` 用字串比大小(`lease[2] > _iso(now)`)判斷到期,兩邊都是本地程式碼產生的固定格式 UTC 字串,不是外部輸入。

## 2. 登入與權限(租約擁有者能不能被偽造來繞過圍籬)

已看,推論一則,severity 定 minor,不 blocking。

- 圍籬的關鍵其實是單調遞增的 `lease_seq`,不是 `owner` 字串本身:`_holds()` 比對 `(lease_seq, owner)` 兩者都要對得上(`task_store.py:335-337`),但因為同一個 `task_id` 下 `lease_seq` 本身已經是遞增且提交/放掉都嚴格核對「目前最新那一列」,`owner` 欄位在正常流程裡不構成額外的安全邊界——它只是拿來在同一個行程內把「這是誰的收據」印出來給 debug 用,語意上比較像標籤而非權杖。
- 若攻擊者能直接寫入同一顆 SQLite 檔案(例如用另一支程式插入一列 `task_leases`,`owner` 猜對或不猜都行,反正 `lease_seq` 才是真正在比的欄位),即可偽造出「持有租約」的狀態、或反過來讓合法持有者的 `commit_step` 因為序號被搶先而失敗。**但**題目已明白框定「分析行程不持有寫入金鑰」「同一 OS 使用者的限制已列為已知、不防刻意繞過」,這條路徑正好落在被排除的威脅模型裡,不算本次新增的洞——推論。
- `uuid.uuid4().hex` 當 `owner`(`flow.py:70`)完全是行程內部產生、不外流、不做任何權杖用途(見第 4 類),偽造它本身沒有安全意義。

## 3. 密鑰與個資(進 log/錯誤訊息)

已看,無。

- 本次改動沒有新增任何 log/print/例外訊息會帶出密鑰或個資。`_release_keeping_the_original_error` 只在 `sqlite3.Error`/`DatabaseBusy` 時靜默吞掉、其餘原例外照樣往外傳,吞掉的路徑本身不記錄任何欄位內容(`flow.py:112-120`)。
- `LeaseReceipt`、`task_leases` 表存的都是 `task_id`、序號、`owner`(隨機 hex 字串,非密鑰)、到期時間,沒有票證、密碼、token 之類敏感欄位。
- `MAX_ERROR_DETAIL_LENGTH`/`error_detail` 相關行為本次沒有變動邏輯,只是新增的租約參數不影響它的寫入路徑。

## 4. 加密與傳輸、可預測的隨機數(uuid4 當擁有者是否有安全意義)

已看,無安全意義、無風險。

- `uuid.uuid4().hex` 是密碼學安全亂數來源(基於 `os.urandom`),但此處拿它當 `owner` 純粹是「同一輪呼叫的識別標籤」,不是用來做存取控制的票證或防猜測的秘密值(見第 2 類分析——真正圍籬是遞增的 `lease_seq`,不是這個字串猜不猜得到)。就算換成可預測的計數器,對本設計的安全性也不會有實質差異,所以即便隨機性強也談不上「加分」,弱了也談不上「扣分」。
- 沒有新的加密、雜湊、或網路傳輸相關程式碼;本次改動完全侷限在本機 SQLite。

## 5. 執行邊界(測試會不會執行不可信位置的檔、shell 插值、環境變數)

已看,無。

- 測試唯一的子行程呼叫使用固定原始碼字串(`CHILD`)透過 `-c` 直接傳給 `python`,不是從檔案系統路徑載入、也不是任何測試執行時才組出來的可變路徑,不存在「執行不可信位置檔案」的問題。
- `env={"PYTHONPATH": SRC}` 完全取代環境變數(不是 `os.environ | {...}` 疊加),`SRC` 是用 `Path(__file__).resolve().parents[2] / "src"` 算出的固定相對路徑,測試檔案本身在 repo 內、不受外部輸入影響。這樣寫可能讓子行程找不到系統 `PATH`(如果 `sys.executable` 不是絕對路徑就會出問題),但這是可靠性問題不是資安洞,而且 `sys.executable` 一般是絕對路徑,不歸類為漏洞。
- 沒有 `shell=True`、沒有從網路或使用者輸入接來的路徑被 `open()`/`exec()`。

## 6. 行動端

無(本次改動不涉及行動端程式碼)。

## 額外檢查

- **新依賴**:無。只多 `import sqlite3`、`import uuid`,皆為標準庫,已在既有依賴之列。
- **並行寫入正確性**(非資安範疇但順帶核對過沒有變成資料完整性洞):`commit_step` 在同一個 `immediate_transaction` 內先查租約、再查序號,`acquire_lease`/`release_lease` 也各自包在交易內,搭配 SQLite `immediate_transaction` 的鎖語意,沒有看到明顯的 TOCTOU 可被利用來偽造「已花過的錢」或雙重提交;這與威脅模型排除的「刻意繞過同行程/同使用者限制」不同,屬於防止「忘記」而非防止「攻擊」的機制,如題目背景所述。

## 小結

這批 diff 是分析行程內部、同一顆本機 SQLite 上的租約互斥機制,所有新 SQL 皆參數化、無 shell 插值、無新反序列化路徑、無日誌洩密、無新依賴。`owner` 用 `uuid4` 純屬行程內標籤而非安全權杖,其可預測性與否對本設計不構成安全差異。是否有人能繞過同一 OS 使用者的租約(直接寫 DB)落在題目已聲明排除的威脅模型內,不算本次新增的漏洞,故未列為 finding、只在第 2 類中以推論方式記錄以供留痕。整體判定 `severity: clean`,沒有可提交 blocker/major 的發現。
