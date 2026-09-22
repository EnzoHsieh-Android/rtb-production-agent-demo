severity: major

### 1. 憑證模組放在共用層,但共用層的定位是「怎麼做」不是「業務聲明」
severity: major
blocking: 是 這決定憑證模組往後放哪支檔、誰能改它,增量 3 的執行行程要立刻依賴這個位置,等發現不對再搬會牽動 DSP 與執行行程兩邊的匯入路徑。
引句:「放在共用的 `rtb` 頂層模組(比照 [[Systems/共用行程基礎]] 的做法」
說明:`Systems/共用行程基礎` 的 responsibility 明寫「不負責任何業務規則、路由或資料表結構」,程式碼也對得上——httpkit.py/sqlitekit.py/httpclient.py 三支檔通篇只有伺服器基礎、資料庫連線交易、HTTP 用戶端這類「怎麼做」的機制,沒有一行業務判斷。憑證的聲明欄位卻是「這一刻,哪個租戶、哪個廣告、哪個動作、多少預算上限、用哪把冪等鍵」——這是業務聲明,不是基礎設施。設計文字只比照了「DSP 與執行行程不得互相匯入,共用的東西放共用層」這條匯入規則,沒有回應「共用層不放業務規則」這條定位規則,兩條規則同時適用時只挑了一條。
file: `docs/rtb-production-agent-demo-knowledge/Systems/共用行程基礎.md:6`

### 2. 憑證聲明裡的「動作」「廣告編號」格式,跟領域層、DSP 各自的定義要不要對齊沒交代
severity: major
blocking: 是 執行行程增量 3 的簽發函式要從領域層的提案物件(ActionType、campaign_id)組出聲明,DSP 端要拿路徑參數比對聲明——這條格式對齊關係一旦沒講清楚,增量 3 一開工就要回頭補。
引句:「租戶、廣告編號、動作、預算上限(暫停動作為空)、冪等鍵、政策版本」
說明:專案裡「動作」與「廣告編號格式」目前已經有兩份獨立定義——領域層 `ActionType` 列舉(`update_budget`/`pause_campaign`)與 `ID_PATTERN` 正則,DSP 自己另外用字面字串比對(`op.action == "update_budget"`)且完全不驗 campaign_id 格式;`_checks.py` 的說明白紙黑字寫「DSP 刻意不依賴這裡,所以它自己另有一份」,是有意識的既有偏離。憑證模組放在領域層與 DSP 都會用到的共用層,聲明裡又原樣放了「動作」「廣告編號」,設計完全沒說這兩個欄位驗證要沿用哪一份定義、或是不是要再造第三份;而 Mock-DSP 圖譜自己就記著「動作增加到第三種時要抽成動作表」的 REVISIT,屆時領域層、DSP、憑證三處都要同步改,設計此刻卻沒有交代同步機制。
file: `src/rtb/domain/_checks.py:5`
file: `src/rtb/dsp/store.py:121`
file: `docs/rtb-production-agent-demo-knowledge/Systems/Mock-DSP.md:53`

### 3. DSP 讀環境變數、執行行程讀設定檔,都不是專案既有的參數傳遞方式
severity: major
blocking: 是 這是啟動流程的介面決定,兩個行程的 main() 與測試的啟動方式都要跟著改,晚發現代價更高。
引句:「DSP 啟動時從環境變數讀金鑰;沒有金鑰就拒收所有寫入」
說明:目前 `src/rtb/dsp/server.py` 與 `src/rtb/executor/inbox_server.py` 的 `main()` 清一色用 `argparse` 收命令列參數(`--db`、`--fault-injection`、`--busy-timeout-seconds` 等),我對整個 `src/rtb` 搜了 `os.environ`/`getenv`、`configparser`/`tomllib`/`yaml`,一個命中都沒有——專案裡完全沒有讀環境變數或設定檔的先例。設計卻讓 DSP 改讀環境變數讀金鑰、執行行程改讀設定檔讀租戶與上限,一次引入兩種原本都不存在的參數傳遞方式,而且沒有像文中其他決定那樣附 PRIOR-ART 或說明為什麼不沿用既有的命令列參數做法(例如 `--capability-key` 這類參數)。
file: `src/rtb/dsp/server.py:184`
file: `src/rtb/executor/inbox_server.py:136`

### 4. 拒收代碼沒交代是否沿用 DSP 既有的型別化錯誤對照表
severity: major
blocking: 是 這決定憑證驗證要不要走 DSP 現有的例外分派路徑,若各寫一套,handle_request 裡就會同時存在兩種「例外轉 (狀態碼, 代碼, 可否重試)」的做法。
引句:「拒收代碼(封閉列舉,不回顯聲明內容):缺憑證 401、簽章或格式不對 401」
說明:DSP 現有的錯誤處理只有兩條路,都繞著「型別化例外 + retryable 旗標」轉:一是 `DspError` 子類別經 `ERROR_TABLE`/`error_entry()` 查表(`StoreBusy`→503 可重試、`VersionConflict`→409 不可重試…),二是協定層直接 `raise RequestRejected(status, code, retryable=...)`(例如缺 Idempotency-Key 回 400)。`map_exception` 統一吃這兩種。憑證這四個拒收代碼(401/401/401/403)完全沒提要走哪一條——更關鍵的是隻字未提 `retryable`,而這個旗標是這個專案錯誤模型裡最核心的一維(每一筆既有錯誤對照表項目都帶它,呼叫端要靠它決定能不能同鍵重試)。增量 1 的十題問答第 7 題才剛定調「DSP 明確回可重試錯誤也不當場重送、一律走查證」,憑證拒收是否可重試、算不算這條規則管的範圍,設計完全沒有回答。
file: `src/rtb/dsp/server.py:49`
file: `src/rtb/httpkit.py:29`

### 5. 廣告表加租戶欄位,沒沿用既有的「補欄位」遷移做法
severity: major
blocking: 是 沒處理這件事,既有(增量 2 之前建立)的 DSP 資料庫檔案會在補欄位後仍缺租戶欄位,寫入端點一上線就可能對舊資料庫出錯,而且這個坑專案自己已經踩過一次、寫成了範式解法。
引句:「廣告建檔時帶租戶(沒指定就用固定的預設租戶),廣告表多一個租戶欄位」
說明:`src/rtb/dsp/store.py` 的 schema 用 `CREATE TABLE IF NOT EXISTS campaigns (...)`,對已經存在的資料庫檔案不會補欄位——這正是 `src/rtb/analyzer/task_store.py` 的 `_migrate_evidence_payload_column` 已經記錄過的坑(它的 docstring 直接寫「`CREATE TABLE IF NOT EXISTS` 不會幫既有表補欄位」),既有解法是每次連線時用 `PRAGMA table_info` 檢查欄位存不存在,缺就在交易內 `ALTER TABLE ... ADD COLUMN ... DEFAULT`,舊列補一個誠實的預設值。設計裡「廣告表多一個租戶欄位」這句話完全沒提遷移機制,也沒提舊資料庫、舊測試 fixture 怎麼補這個欄位,是專案裡已有現成解法卻沒沿用、也沒交代不沿用理由的落差。
file: `src/rtb/analyzer/task_store.py:141`
file: `src/rtb/dsp/store.py:30`
