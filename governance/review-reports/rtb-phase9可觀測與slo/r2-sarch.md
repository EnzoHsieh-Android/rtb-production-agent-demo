severity: major

### 1. DSP 用戶端「例外帶失敗類別」機制只補了讀取路徑,寫入與作廢呼叫沒有對應機制,S603 對這兩類呼叫要求的結果類別區分做不到
severity: major
blocking: 是 這正是審查指定要判的「DSP 用戶端例外帶失敗類別」項目;S603 明文要求「寫入、作廢、查操作紀錄、讀廣告」四種呼叫都要能記出逾時/連線失敗/5xx/回應讀不懂等不同結果類別,但現有寫入/作廢路徑的既有架構會把這幾種失敗摺成同一個值,設計沒有處理這個衝突,照字面實作會系統性記錯
引句:「執行端 DSP 用戶端讀取失敗時丟的例外帶一個封閉列舉的失敗類別(逾時、連線失敗、5xx、回應讀不懂)」
說明:最小設計明講嘗試紀錄的結果類別要能涵蓋「這次呼叫的類別(寫入、作廢、查操作紀錄、讀廣告)」全部四種,而 [S603] 合約字面寫「逾時、連線失敗、5xx、回應讀不懂應記成不同的結果類別」,沒有限定只適用讀取。但設計唯一交代的機制——例外帶封閉列舉失敗類別——只講「讀取失敗」。查證 `src/rtb/executor/dsp_client.py` 的既有架構:`write()`(63-66 行)與 `void()`(109-112 行)都是 `except (OSError, ValueError): return WriteAnswer(None)` / `VoidAnswer(None)`,模組文件第 5 行本身就寫明「寫入:不丟例外;逾時、斷線、回應讀不懂一律回『沒拿到回應』」;`execution.py` 的 `WriteAnswer`/`VoidAnswer`(74-75、94-95 行)也是「status 是 None 代表沒拿到回應(逾時、斷線、回應讀不懂)」——三種原因在回傳值裡是同一個 `None`,呼叫端(要寫嘗試紀錄新欄位的程式)完全沒有依據分辨。
舉例:輸入——執行迴圈呼叫 `dsp_client.write(proposal, key, token)`,這次 DSP 逾時(`TimeoutError`);另一次同一支呼叫因連線被拒(`ConnectionRefusedError`)。兩者都落進 `except (OSError, ValueError)`,回傳都是 `WriteAnswer(status=None)`,完全無法區分。預期(按 S603 字面)——這一列嘗試紀錄應該記「逾時」或「連線失敗」兩種不同的結果類別;實際——沒有任何管道能供出這個差異,只能兩次都記成同一個桶,或實作者被迫另外幫 write()/void() 新增例外細節傳遞,但設計完全沒提這件事、也沒有對應合約守。5xx 對寫入/作廢而言不受影響(`request_json` 把非 2xx 的合法 JSON 回應當正常回傳值,狀態碼仍在 `WriteAnswer.status` 裡),但逾時、連線失敗、回應讀不懂這三種在寫入/作廢路徑上是結構性遺失,不是實作疏忽。
file: `src/rtb/executor/dsp_client.py:5`
file: `src/rtb/executor/dsp_client.py:63-66`
file: `src/rtb/executor/dsp_client.py:109-112`
file: `src/rtb/executor/execution.py:74-75`
file: `src/rtb/executor/execution.py:94-95`

### 2. 收件口唯讀開法若沿用既有初始化/補欄位邏輯,對還沒被任何寫入端遷移過的資料庫會在資料庫層直接炸掉,而維運套件被規定不准呼叫任何寫入函式,沒有自救管道
severity: major
blocking: 是 屬於判準明列的「資料庫開不起來」情形;且維運套件的邊界規則(唯讀開法、不准呼叫寫入函式)讓這個情境在套件內部無法自行修復,必須在設計層先講清楚,不能留給實作者現場發現
引句:「收件口模組新增一個唯讀開法:開出來的物件只有唯讀連線,沒有寫入交易入口」
說明:現有 `InboxStore.__init__`(`src/rtb/executor/inbox_store.py:358-379`)開連線時把完整 `SCHEMA`(含事件表等)一路傳給 `connect()`,`connect()`(`src/rtb/sqlitekit.py:23-40`)會 `conn.executescript(schema)`——這一步在資料庫還沒有某張表時是真的 DDL 寫入;開完連線又會呼叫 `_migrate_columns()`(`inbox_store.py:401-419`),缺欄位或允許值限制不齊時用 `immediate_transaction` 補欄位、整表重建。這整套「開啟就順手補齊 schema」的既有做法,正是 [S607] 要求寫入路徑「開啟時應補上,不丟例外」的機制來源。但設計對「唯讀開法」只說它「只有唯讀連線,沒有寫入交易入口」,完全沒交代開啟時要不要跑、或怎麼跳過這段補 schema 的邏輯;而 [S607] 的合約與測試(`test_an_old_executor_database_gains_the_lifecycle_table_and_columns`)只覆蓋既有的寫入路徑開啟,沒有覆蓋這支新的唯讀開法。
我在暫用目錄用專案 `.venv` 對這個情境做了實測:對一個已存在但缺某張表的 SQLite 檔,拿 `sqlite3.connect(f"file:{path}?mode=ro", uri=True, ...)` 開出的唯讀連線執行 `CREATE TABLE IF NOT EXISTS new_table (...)`(對照 `_migrate_columns`/`connect()` 補 schema 時會做的動作),結果是 `sqlite3.OperationalError: attempt to write a readonly database`(`sqlite_errorcode` 主要碼是 8,不是 `SQLITE_BUSY`/`SQLITE_LOCKED`);對照組——若該表已存在,同一句 `CREATE TABLE IF NOT EXISTS` 在唯讀連線上會成功(no-op)。也就是說,只有「資料庫真的還缺這次 Phase 9 要加的表/欄位」時,唯讀開法才會踩雷——這正是這個增量上線初期,任何還沒被寫入端(執行迴圈)碰過一次的執行端資料庫的必然狀態。因為 `sqlitekit.py:18-20` 的 `_is_lock_contention` 只把 `SQLITE_BUSY`/`SQLITE_LOCKED` 歸類成可重試的 `DatabaseBusy`,這個 `SQLITE_READONLY`(8)例外會原樣往外炸,不會被既有例外處理接住。
舉例:輸入——一個 Phase 9 增量 1 合進之前建立、目前還沒有任何寫入端(執行迴圈)重新啟動過的舊執行端資料庫,直接用維運套件的追蹤檢視命令列入口去讀它。預期——比照 S606/S613 那樣優雅地「缺的段標明缺」或至少乾淨地開得起來;實際——若唯讀開法比照既有 `InboxStore.__init__`/`_migrate_columns` 邏輯(這是設計唯一交代過的「開啟就補 schema」做法),會在補表這一步直接丟出未分類的 `sqlite3.OperationalError`,追蹤檢視整個開不起來,而維運套件自己被規定「不准呼叫任何寫入函式」,無法在套件內部補救,只能等別的寫入端先開過一次——但設計裡沒有任何地方保證這個先後順序。
file: `src/rtb/executor/inbox_store.py:358-379`
file: `src/rtb/executor/inbox_store.py:401-419`
file: `src/rtb/sqlitekit.py:23-40`
