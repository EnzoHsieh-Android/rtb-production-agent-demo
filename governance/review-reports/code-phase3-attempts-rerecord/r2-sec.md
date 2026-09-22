severity: major

### 1. 重啟恢復沒有逐鍵隔離錯誤,單一列毀損會讓整批鍵永久卡在嘗試中(每次重啟都重演,無法自癒)
severity: major
blocking: 是 已用可重現的 PoC 證實:一把鍵的歷史列只要有任何欄位(不只是狀態)讀不回來,`recover_in_flight` 就會整批回滾,連健康的鍵都留在嘗試中,而且下一次重啟會踩到同一顆地雷、無限重演——這正是判準裡的「鎖死」。
引句:「一把鍵撞上限若讓整批回滾,同一次重啟裡健康的鍵也會留在嘗試中」

這句正是 r2 patch 自己在 `recover_in_flight` 的新版 docstring 裡點名的風險,但補的藥只治了「歷史列撞 50 列上限」這一種病因(靠 `_append(tx, row, AttemptState.UNKNOWN, now, capped=False)` 豁免),沒有把同一個迴圈裡任何一鍵的例外都隔離開。`recover_in_flight` 在同一個交易、同一個迴圈裡對每把「目前是嘗試中」的鍵呼叫 `latest(tx, key)`,只要有一把鍵的 `written_at`(或 `code`)欄位讀不回來,`_row()` 就丟 `CorruptedAttemptRow`,直接炸穿整個函式,交易整批回滾,包含排在它之前已經「邏輯上該恢復」但還沒 commit 的其他健康鍵。因為重啟恢復是唯一能把嘗試中轉出去的入口,而且每次重啟都用同一份資料重跑同一個迴圈,一旦某把鍵沾上這種毀損,執行行程從此每次重啟都會在同一點失敗,所有嘗試中的鍵(不只毀損那把)永久卡住,直到有人手動修資料庫。

實測(PoC,`/private/tmp/claude-501/-Users-enzo/36c33b75-fd17-4bff-aa49-20f97a550e58/scratchpad/poc5_recover_stuck2.py`):建兩把鍵(healthy-camp、victim-camp)都是嘗試中;只把 victim 那列的 `written_at` 改成不合法字串(模擬未來某個寫入路徑的欄位錯誤或位元損壞,不動 `state` 欄位,所以它仍會被 SQL 篩進待恢復清單);呼叫 `recover_in_flight` 兩次(模擬連續兩次重啟):兩次都是
```
recover_in_flight FAILED with: CorruptedAttemptRow('...第 1 列讀不回來:ValueError("Invalid isoformat string: ...")')
```
且 healthy 鍵的狀態在第一次失敗後仍是 `in_flight`(完全沒被恢復),第二次重啟原封不動地再炸一次。

建議:`recover_in_flight` 的迴圈要逐鍵包 try/except,把單一 `CorruptedAttemptRow`(或任何非預期例外)記下來、跳過那一把鍵,讓其餘健康的鍵照常恢復並提交,壞掉那把改走「轉人工」或至少獨立回報,不要讓它拖垮整批。

### 2. `ExecutorTransaction` 的執行期檢查只驗型別與 `in_transaction`,不驗證是否真的由收件口的 `BEGIN IMMEDIATE` 入口開出,安全網只剩一支可被平常寫法繞過的 AST 測試
severity: major
blocking: 是 文件明講這個型別存在就是為了擋「自己開的延遲交易」,但執行期真正擋這件事的程式碼其實沒做到,只剩一支容易失手的靜態測試頂著,屬於「不經意就會失守」而非需要刻意攻擊。
引句:「自己開的連線、自己開的延遲交易(不排隊搶寫入鎖)」

`_conn(tx)` 的檢查是 `isinstance(tx, ExecutorTransaction) and tx.conn.in_transaction`,而 `ExecutorTransaction` 只是 `@dataclass(frozen=True)` 包一個 `conn` 欄位,沒有任何 `__post_init__` 去驗證這個連線真的是從 `InboxStore.transaction()`(用 `BEGIN IMMEDIATE` 排隊搶寫入鎖)拿到的。任何程式碼只要拿得到 `ExecutorTransaction` 這個類別,自己開一條連線、自己下一句「延遲交易」(`BEGIN`,不是 `BEGIN IMMEDIATE`),包成 `ExecutorTransaction(conn)`,就能通過 `_conn()` 的檢查,直接呼叫 `begin`/`transition`/`resolve` 等函式——這正是這段 docstring 自己說「不收」的那個情境,但執行期完全沒有機械擋下來,唯一的防線是 `tests/executor/test_attempt_store.py` 裡那支掃 AST、找字面上叫做 `ExecutorTransaction(` 的呼叫的測試,而這支測試連換個 import 別名(`from ... import ExecutorTransaction as ET` 再 `ET(conn)`)都抓不到——不需要刻意規避,只是稀鬆平常的重構就會讓安全網悄悄失效,完全符合本專案「防忘記」該防的範圍。

實測(PoC,`poc2_race.py`、`poc6_stress_cap.py`):用上述繞過手法在同一個廣告上開兩個執行緒各自 `begin()` 不同鍵,兩邊都在各自的交易裡讀到 `unresolved_count = 0`(即兩邊都判定「這個廣告沒有未結案的鍵」,通過了 `CampaignLocked` 檢查——這一步本身就已經違反了 docstring 宣稱的「並行檢查也一定在寫入鎖之內」),之後才在寫入時互撞。實測結果:因為 SQLite 在 WAL 下對延遲交易升級寫入鎖有快照衝突偵測(這點與 Phase0 筆記裡既有的實驗結論一致),真正落地的永遠只有一筆,沒有造成兩把鍵同時鎖住同一廣告、也沒有讓全表上限(`MAX_UNRESOLVED=20`)被實際超額寫入(10 執行緒同時搶時,一致只有 1~2 筆真正 commit)。但輸家拿到的不是 `AttemptRejected`/`CampaignLocked`/`InboxBusy` 這種模組設計好、呼叫端預期會接住的例外,而是原始的 `sqlite3.OperationalError('database is locked')` 直接穿出 `attempt_store.begin()`。單一寫入者(執行行程)一旦有任何一段程式(哪怕只是善意重構出的別名匯入)不小心繞過收件口拿到自己開的交易物件,一次正常的並行搶鍵就可能讓沒接住這個例外型別的呼叫路徑整個崩潰——單一寫入者行程崩潰對這個系統而言就是全面停擺。

建議:讓 `ExecutorTransaction` 帶一個只有 `InboxStore.transaction()` 拿得到的不可偽造憑證(例如建構時要求傳入一個模組私有的 sentinel 物件並在 `__post_init__` 驗證),把安全網從「掃字面呼叫的測試」升級成執行期真的驗證得出來的東西。

---

另外兩題查了但沒找到能利用的洞:
- 未結案計數算法(第 1 列數 − 終點列數):`TERMINAL_STATES={VERIFIED, FAILED}` 在 `TRANSITIONS` 表裡出邊都是空集合,`resolve()` 又只認 `ESCALATED` 來源,所以公開 API 下沒有路徑能讓一把鍵寫出兩列終點列,也沒有路徑能讓一把鍵在沒有 `begin()` 的情況下被 `_append`(因為 `_append` 一律用 `latest()` 抓到的既有列當 `previous`)。逐條檢查 `_GENERAL` 轉換表與 `resolve`/`record_verification_timeout`/`recover_in_flight` 後確認算不出兩列終點或缺第 1 列的情形。
- `snapshot()` 的鍵核對(`operation_key(parsed.proposal) != key`):鍵只涵蓋 `task_id/campaign_id/action_type/requested_change/campaign_version_observed` 五個欄位是刻意設計(故意不含 `revision`、`decision_created_at/expires_at` 等),而快照與鍵永遠是同一次 `begin()` 呼叫、同一個 `proposal` 物件算出來的,公開 API 下沒有機會讓兩者分岔;這道檢查本質是防資料毀損(docstring 也明講「或快照已對不上它的鍵」),不是防竄改,在「防忘記、不防刻意繞過」的威脅模型下沒有發現可利用的落差。
