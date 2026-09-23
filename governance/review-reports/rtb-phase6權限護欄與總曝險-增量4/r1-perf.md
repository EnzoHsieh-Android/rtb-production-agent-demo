severity: major

## 審查範圍與方法

只審「## 增量 4 設計:可觀測」節。鏡頭:效能、索引與鎖。程式以 ff06a03 為準(`inbox_store.py`、
`attempt_store.py`、`sqlitekit.py`);在 `/private/tmp/.../scratchpad/perf_test/test.db` 用實際的
`write_stops`/`attempts` 建表語句(含既有索引)建庫,灌 `write_stops` 20 萬列、`attempts` 100 萬列
(seq=1、20 個租戶、500 個廣告,寫入時間分散),`ANALYZE` 後用 `EXPLAIN QUERY PLAN` 與實際計時核對。

## F1 新索引(租戶、種類、時間)沒給租戶時的廣告篩會退化成全表掃描

severity: major
blocking: 是 — 設計明確承諾查詢一「可依租戶、廣告篩」,但沒給租戶只給廣告篩時新索引用不上,行為與設計字面承諾不符,而且這張表「只增不改、不清理」,掃描成本會隨表變大無限增加(正是被指派要查的「有沒有查詢隨只增不改的表無限變慢」)。

引句:「停下紀錄表裡種類是總曝險已滿的列數。可依租戶、廣告篩;可給時間範圍(包含起點、不包含終點,比對停下紀錄的時間)。」

實測(20 萬列 `write_stops`,索引 `(tenant, kind, at)`):

- 有給租戶(`tenant=? AND kind=? AND campaign_id=? AND at 範圍`):`SEARCH write_stops USING INDEX write_stops_tenant_kind_at (tenant=? AND kind=? AND at>? AND at<?)`,約 2ms。
- **沒給租戶、只給廣告(`kind=? AND campaign_id=?`)**:`SCAN write_stops`,約 15ms(20 萬列);這條路徑索引完全用不上,因為新索引把 `tenant` 放在最前面,SQLite 的查詢規劃器不會為了不等值的 `tenant` 做 skip-scan 去對齊後面的 `kind`/`campaign_id`。
- 沒給租戶、只給 `kind`+時間範圍:能用到索引(covering,`ANY(tenant)`),因為 `kind` 是索引第二欄且有等值條件時規劃器願意展開所有租戶分支;但一旦再加上 `campaign_id`(索引沒收的欄位)就退化為全表掃描,如上一條。

設計文字裡「索引」一節只說明索引服務時間篩選與租戶篩選,沒有提到「不給租戶、給廣告」這個明確承諾要支援的組合其實掃全表;隨 `write_stops` 只增不改地變大,這條路徑的延遲會沒有上限地變差,而且它跟查詢一(總曝險停下次數)、查詢二(表滿延後次數)共用同一張表同一個索引缺口,兩支查詢都受影響。

建議轉給實作:索引若要真的服務「不給租戶、給廣告」這個組合,需要另外一支以 `campaign_id` 領頭(或 `campaign_id, kind, at`)的索引,或者把「廣告篩」在文件裡限定成「必須先給租戶」;兩者選一,不要讓文字承諾跟索引實際涵蓋範圍不一致。

## F2 查詢五「通過的」那段在 attempts 表上沒有可用索引,掃描成本隨表無限成長且發生在全域寫入鎖內

severity: major
blocking: 是 — 這是被指派要查的第二個問題(嘗試紀錄第一列按租戶與開始時間篩有沒有可用索引),實測沒有;而且這段查詢跟其餘四支查詢一樣收在 `transaction()`(`BEGIN IMMEDIATE`)裡跑,握著全域寫入鎖,執行迴圈的寫入要排隊等它。`attempts` 表沒有任何清理機制(只增不改,程式裡找不到對它的 DELETE),所以這個鎖定時間會隨系統運行時間單調增加,沒有上限。

引句:「嘗試紀錄裡這個租戶、第一列開始時間在範圍內、帶預留金額的每一把鍵(冪等鍵、廣告、預留金額、開始時間、目前狀態),依時間排序。」

實測(`attempts` 表,既有索引為 `attempts_first_rows(campaign_id) WHERE seq=1`、`attempts_terminal_rows`、`attempts_one_terminal_per_key`、`attempts_verified_by_time(written_at) WHERE state='verified'`,均不含 `tenant`):

- `SELECT ... FROM attempts WHERE tenant=? AND seq=1 AND written_at>=? AND written_at<?`:`EXPLAIN QUERY PLAN` 回 `SCAN attempts`——沒有一個既有索引能命中這個過濾組合(`attempts_first_rows` 只收 `campaign_id`,沒有 `tenant`;其餘三個索引也都不含 `tenant`)。
- 30 萬列時 24ms,追加到 100 萬列時 73ms(在同一台機器、同一次 `BEGIN IMMEDIATE` 交易內量測),隨列數近似線性成長,且這段時間全域寫入鎖是被握住的(執行迴圈的取件與開始一筆會等待)。

對照設計自己對 `aggregate_used` 的處理:同一節明確要求「成本跟 24 小時內的已驗證筆數成正比,不跟歷史總量成正比」,並為此特地加了 `attempts_verified_by_time` 這個部分索引,同時把沒有終點那段的成本鎖在 `MAX_UNRESOLVED=20` 之內——設計者顯然知道要避免「跟歷史總量成正比」的查詢,但查詢五的「通過的」段落沒有拿到同等待遇:它就是一支跟歷史總量成正比的查詢,而且强制要求的時間範圍(「範圍一律要給」)只限制了回傳筆數,不限制掃描成本(SQLite 沒有索引可用時,任何範圍窄的查詢都得先掃過整張表才能篩出符合的列)。

引句(「已排除/未排除」段落對鎖代價的既有承認,供對照):「查詢收寫入交易、握全域寫入鎖,查詢期間執行迴圈要等;已用額度那段在 30 萬列時約 0.3 秒。現在只有人工與測試會呼叫。」——這句只量測、只承認「已用額度」那一支的鎖代價,沒有涵蓋查詢五「通過的」段落(它的鎖代價比已用額度更差:已用額度有視窗與未結案上限兜底,查詢五完全沒有)。

回頭條件也沒接住這個風險:「回頭條件:Phase 9 要做定時輪詢或告警時(入口:Phase 9 計劃開檔的那一刻),先改成不握寫入鎖的讀法再接上去。」——這條回頭條件是綁「呼叫頻率變高」(定時輪詢)觸發,但查詢五即使頻率不變(只有人工在 F7 事故發生時呼叫),鎖定時間也會隨 `attempts` 表隨時間單調變長;而且「人工在 F7 事故發生時呼叫查詢五」正是這支查詢最該快的時刻(稽核要說明哪些通過哪些被擋,執行迴圈這時通常也最忙),卻是它掃描成本最差、還會一直變差的時候。建議轉給實作或設計下一版:查詢五的「通過的」段落需要一個以 `tenant`(或 `tenant, seq`)領頭、能配合 `written_at` 範圍的索引,不能只靠強制時間範圍。

## 其餘確認沒問題的觀察

- 停下紀錄表現況描述屬實:核對 `inbox_store.py` 的 `SCHEMA`,`write_stops` 建表語句裡確實沒有任何 `CREATE INDEX`,只有 `UNIQUE (kind, task_id, revision, content_hash)` 這個資料庫自動索引;設計文字「沒有任何索引」與程式相符。
- 查詢一/二/四/五都收在 `InboxStore.transaction()` 開的交易裡(`immediate_transaction` → `BEGIN IMMEDIATE`),確實握全域寫入鎖;設計文字「沿用既有做法收 `transaction()` 的交易(會握寫入鎖)」與程式相符,專案目前確實只有這一種交易入口(`sqlitekit.py` 只有 `immediate_transaction`,沒有唯讀交易的另一套)。
- 查詢四(額度使用率)沿用既有 `aggregate_used`,其兩段查詢分別受 `attempts_verified_by_time` 部分索引與 `MAX_UNRESOLVED=20` 上限保護,成本不隨歷史總量無限成長,設計描述與實測相符,沒有發現額外問題。
- 停下紀錄表的租戶欄實際上一定有值(核對 `execution.py` 的 `_take`/`_sign` 呼叫路徑與 `record_stop` 呼叫點,兩個寫入點都在簽發成功之後才寫停下紀錄),設計文字對這一點的描述與程式相符;不影響 F1、F2 的索引結論(F1、F2 是「有沒有可用索引」的問題,跟欄位是否可能為空無關)。
