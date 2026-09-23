severity: minor

## F1 稽核明細只擋「沒給範圍」,不擋「給極端寬的範圍」,握寫入鎖的全表等級掃描仍能被叫出來

severity: minor
blocking: 否 — 目前 `src/` 裡沒有任何呼叫端把這四支查詢接到分析行程或任何外部可觸發的入口(只有人工與測試呼叫,設計文件也載明這件事,Phase 9 才會接輪詢/告警),所以現在還沒有真正能送出這個範圍值的攻擊者,列 minor 供之後接上呼叫端前處理。

前提:`aggregate_audit` 收的是執行行程資料庫交易入口開的交易(寫入交易,握全域寫入鎖,跟 Phase 5 的 `version_conflict_count` 同一種做法,`Systems/可觀測查詢.md` 自己也這樣記)。它對 `since`/`until` 唯一的檢查是非 None:

引句:「if since is None or until is None:」

動作:呼叫端只要给出非 None 的極端值(例如 `since=datetime.min`、`until=datetime.max`,或乾脆 `since=now-timedelta(days=3650)`),檢查就會通過,接著 `passed_query` 與 `write_stops` 的停下清單查詢會在整個歷史範圍內掃描並回傳,而這整段時間都握著寫入交易的鎖。

可觀察效果:同一顆執行行程資料庫上,`begin`/`transition`/收件口的補欄位、對帳等所有要拿寫入鎖的操作都要排在這次查詢後面,查詢愈久(範圍愈寬、歷史列愈多)執行迴圈卡愈久;跟系統筆記自己描述的「還沒結案那段掃全表約 0.9 秒/100 萬列,握寫入鎖」是同一種鎖,但這裡沒有任何上限,呼叫端可以自由把時間再放大。

為什麼要緊:設計與程式碼都刻意加了「一定要給範圍」這道檢查,理由寫得很明白(「不給範圍會整張撈出來」),但只驗證了「有沒有給」,沒有驗證「給的範圍多寬」,等於防呆的意圖被實作打了折扣——只要以後任何一個呼叫端（例如 Phase 9 的告警或一個核可管理工具)讓使用者自己填時間範圍,防呆檢查完全擋不住有人(不管是失誤還是刻意)填出「等於沒給範圍」的寬度,回到同一個全表鎖表風險。建議在這支函式裡對範圍寬度或列數設一個上限(或至少讓呼叫端知道要自己把關),而不是只做非 None 檢查。

---

以下三個攻擊面照著派工單逐一查過,沒找到可利用的洞:

**跨租戶資料外洩(舊列算進每個租戶、稽核明細欄位)**:`aggregate_audit`、`aggregate_stop_count`(帶 `tenant=` 時)、`table_full_deferral_count`(帶 `tenant=` 時)的 SQL 都是用參數化的 `tenant = ?` 精準比對,沒有用 `LIKE`/字串拼接;`passed_query` 額外用 `tenant IS NULL` 撈進 Phase 6 之前的舊列,這個「舊列算進每一個租戶」是設計審三輪定案、`外部寫入嘗試紀錄.md` 的 `RULE:` 明寫、也有測試(`test_the_audit_reconciles_with_used_across_every_kind_of_row`)覆蓋的既有行為,不是這次新開的洞;真正屬於別的租戶(`tenant` 欄有值但不等於查詢租戶)的列,`_counted`/`counted_amount` 一律回 0,不會混進 `passed`。`aggregate_stop_count`/`table_full_deferral_count` 在不帶 `tenant=` 時可以用 `campaign_id=` 單獨查出跨租戶的停下次數,但這兩支只回一個整數(計數),不回任何列細節,而且模組文件與威脅模型都沒宣稱在這一層做租戶授權(比照 `attempt_store` 自己講的「防忘記、不防刻意繞過」,授權層本來就該在更外層做),不算這次 diff 新增的漏洞。

**SQL 注入(篩選值、表名拼接)**:`observability.py` 四支查詢的可變部分都是走 `?` 參數化——`stop_count_query` 的 `where`/`params` 是從固定的 `(clause, value)` 元組表挑出來的,子句字串本身是常數,只有值走參數;`passed_query`、`aggregate_audit` 內的 SQL 字串同理,表名、欄名都是模組內常數(`_STOP_COLUMNS`、`_FIRST_ROW_COLUMNS`),沒有任何一處把外部傳入的字串直接接進 SQL 語句本體。標了 `# noqa: S608` 的三處都經檢查是「只拼接固定條件/固定欄名」,跟註解相符。

**新公開函式 `connection` 是否繞過交易核對**:`attempt_store.connection(tx)` 直接呼叫既有的 `_conn(tx)`,兩者做一樣的檢查(`type(tx) is not ExecutorTransaction or not tx.is_open` 就丟 `NotInTransaction`),沒有少檢查任何一步,只是把回傳的 `sqlite3.Connection` 讓 `observability.py` 這個新模組也能拿到。這確實擴大了「誰能直接下原生 SQL」的可見範圍(從只有 `attempt_store.py` 內部,變成任何拿得到合法 `ExecutorTransaction` 的模組),但沒有弱化原本的守門檢查本身,而且屬於同一份 `attempt_store.py` 自己宣告的「防忘記、不防刻意繞過」威脅模型內——同進程、已經通過交易檢查的呼叫方本來就被視為信任範圍內。
