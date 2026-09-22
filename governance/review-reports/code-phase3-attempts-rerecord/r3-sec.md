severity: major

### 1. 未結案計數的「負數當毀損」防線只擋總和,擋不住互相抵銷,S6 廣告互斥可被一把毀損鍵悄悄繞過
severity: major
blocking: 是 已用可執行 PoC 證實:同一廣告下,一把正常未結案鍵 + 一把被多寫一列終點的毀損鍵,`unresolved_count` 回報 0(不是負數,不會觸發保護),第三把鍵因此順利 `begin()` 成功,廣告同時掛著兩把未結案鍵。
引句:「同一把鍵出現第二列終點列,算法的前提不成立:當成毀損,不放行」

`unresolved_count` 用「第 1 列數 − 終點列數」的差值判斷,只在**總和**為負時才拋 `CorruptedAttemptRow`(`src/rtb/executor/attempt_store.py:220`)。但只要同一個廣告裡,一把鍵多出一列終點(讓終點計數多 +1),恰好被另一把仍在 `in_flight` 的正常鍵(讓第 1 列計數多 +1)抵銷,差值就會落回 0,完全看不出毀損,也不會擋下新寫入。用 `.venv` 重現:先 `begin()` 一把鍵 A(廣告 `c1`,留在 `in_flight`),再用原始 SQL 模擬「未來某段程式的位元/邏輯錯誤」把一把獨立的壞鍵寫成兩列 `failed`(campaign_id 同樣是 `c1`,這正是測試檔 `tests/executor/test_attempt_store.py` 裡 `test_a_negative_unresolved_count_is_treated_as_a_corrupted_table` 驗證的同一種毀損,只是這次不是它唯一,還多一把健康鍵陪跑),接著 `unresolved_count(tx, "c1")` 回報 `0`,再 `begin()` 第三把鍵直接 `created=True`。廣告 `c1` 因此同時有鍵 A 與第三把鍵兩筆未結案嘗試——一旦增量 3/4 把這條路接上 DSP,就是同一個廣告被兩條路徑各自送出寫入,正是判準裡的「重複寫入」。此發現與本次代碼審自己的上一輪外家報告(`governance/review-reports/code-phase3-attempts-rerecord/r3-x1.md` 第 1 條)記錄的問題相同,獨立重現後確認在這份凍結 patch 裡仍未解決。

### 2. `recover_in_flight` 的逐鍵隔離只接住 `ValueError`,型別錯誤(如 BLOB 寫入時間)仍會讓整批回滾,重演這支修正宣稱已解決的「全部鎖死」
severity: major
blocking: 是 已用可執行 PoC 證實:一把鍵的 `written_at` 若是 bytes 而非字串,`recover_in_flight` 丟出未被接住的 `TypeError`,交易整批回滾,連另一把完全健康的鍵都留在 `in_flight`,而且每次重啟都會在同一點原地爆炸。
引句:「歷史列讀不回來的鍵跳過並回報:不然一把壞鍵會讓每次重啟都在同一點整批失敗」

`recover_in_flight` 新增的 `try: row = latest(tx, key) / except CorruptedAttemptRow: unreadable.append(key); continue`,而 `CorruptedAttemptRow` 只由 `_row()` 在捕到 `ValueError` 時拋出。SQLite 的表沒有 `STRICT` 約束,型別不保證;`written_at.replace("Z", "+00:00")` 若欄位是 `bytes`(而不是預期的 `str`),丟的是 `TypeError`,不是 `ValueError`,直接穿過這層 `except`。重現:建兩把 `in_flight` 鍵(健康、受害),把受害鍵的 `written_at` 用原始 SQL 改成 `b"bad"`,呼叫 `recover_in_flight`:整段拋出 `TypeError: a bytes-like object is required, not 'str'`,交易回滾;之後讀健康鍵的狀態仍是 `in_flight`(預期應已轉成 `unknown`)。這正是這支修正自己聲稱要解決的舊問題(「一把壞鍵會讓每次重啟都在同一點整批失敗」)——只是換一種型別的毀損就能繞過,而且後果比「跳過一把鍵」更糟:是**每一把**嘗試中的鍵(不只毀損那把)每次重啟都恢復不了,永久鎖住它們各自的廣告,得等人工介入。此發現與 `governance/review-reports/code-phase3-attempts-rerecord/r3-x1.md` 第 2 條記錄的問題相同,獨立重現後確認仍未解決。
file: `src/rtb/executor/attempt_store.py:167`
file: `src/rtb/executor/attempt_store.py:169`

### 3. 交易憑證只驗身分與型別,不驗證交易是否真的用 `BEGIN IMMEDIATE` 排隊搶鎖;善意重構下仍可重現上一輪判定為 blocking 的「自己開延遲交易」繞過
severity: major
blocking: 是 已用可執行 PoC 證實:即使不開新連線、不出 `inbox_store.py`/`attempt_store.py` 這兩支「允許」檔案,只要把 `InboxStore._conn` 用一般 `BEGIN`(而非 `BEGIN IMMEDIATE`)開交易,再用公開可 import 的 `EXECUTOR_TRANSACTION_ISSUER` 包成 `ExecutorTransaction`,`attempt_store.begin()` 照樣寫入成功——正是這段 docstring 自己說「不收」的情境。
引句:「自己開的連線、自己開的延遲交易(不排隊搶寫入鎖)都不收」

這份修正把憑證從純原始碼掃描(AST 找 `ExecutorTransaction(` 呼叫)升級成執行期檢查:`__init__` 驗證 `issuer is EXECUTOR_TRANSACTION_ISSUER`,`_conn()` 再驗證 `type(tx) is ExecutorTransaction and tx.is_open`。但 `is_open` 只看 `self._open and self.conn.in_transaction`(`src/rtb/executor/attempt_store.py:114-116`、`154-155`,皆為本次патch新增內容),完全不檢查這個交易是不是真的經由 `immediate_transaction()`(`BEGIN IMMEDIATE`,會排隊搶寫入鎖)開出來的——一般的延遲 `BEGIN` 一樣能讓 `conn.in_transaction` 變 `True`。獨立重現兩種寫法都成功:(a) 完全在專案外的腳本裡 `sqlite3.connect()` 自己開連線 + `BEGIN` + `from rtb.executor.attempt_store import EXECUTOR_TRANSACTION_ISSUER`(這個 token 沒有底線前綴,任何模組都 import 得到);(b) 更貼近「善意重構」的版本——不開新連線,直接借用 `InboxStore` 自己的 `_conn`,只是改用一般 `BEGIN` 而非 `immediate_transaction()`,一樣通過檢查並成功 `begin()` 寫入。兩者都完全不需要「刻意繞過」,只是圖方便省一次鎖等待就會中招,落在專案自己定義的「防忘記」範圍內。這正是 `governance/review-reports/code-phase3-attempts-rerecord/r1-x1.md`(標題「任意交易都能冒充收件口提供的交易入口」)與 `r2-sec.md` 第 2 條(標題「執行期檢查只驗型別與 `in_transaction`」)兩輪都判定 `blocking: 是` 的同一個洞;本次這份「第二次修正」只是把守門識別碼從無到有,並未真正驗證交易種類,獨立重測後確認**該 blocking 發現尚未解決**——一旦有並行寫入者取得這種延遲交易,`begin()` 內「查目前列 → 查廣告未結案 → 查全表未結案 → 寫入」不再保證在單一寫入鎖之內完成,先前兩輪報告已示範這會讓部分呼叫路徑拿到未預期的 `sqlite3.OperationalError` 直接穿出,單一寫入者行程可能整個崩潰。
