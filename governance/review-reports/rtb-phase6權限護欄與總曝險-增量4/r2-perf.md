severity: minor

# 鏡頭:索引與查詢計畫(增量 4,第 2 輪)

方法:在臨時目錄用 `/Users/enzo/rtb-production-agent-demo/.venv/bin/python` 建三份 SQLite 資料庫,照 `src/rtb/executor/inbox_store.py`(`write_stops` 表結構、`file: src/rtb/executor/inbox_store.py:153-158`)與 `src/rtb/executor/attempt_store.py`(`attempts` 表結構、既有四個索引、`file: src/rtb/executor/attempt_store.py:45-60`,`aggregate_used` 實際 SQL、`file: src/rtb/executor/attempt_store.py:355-380`)照抄欄位,額外建凍結快照第 338–340 行描述的四個新索引(停下紀錄表三個、嘗試紀錄第一列按租戶與開始時間的部分索引各一),灌 30 萬與 100 萬列合成資料(50 租戶、3000 廣告,時間隨機分布一個月),對五支查詢描述的各種篩法跑 `EXPLAIN QUERY PLAN` 與實際計時。腳本與資料庫留在 `/private/tmp/claude-501/-Users-enzo-rtb-production-agent-demo/5d833242-be96-4298-aba3-6f1eb8999a86/scratchpad/perf/`(`build.py`、`build_realistic.py`、`explain.py`、`explain2.py`、`explain3.py`、`explain4.py`)。

## 逐項結論

**四個新索引對查詢一、二、五「被停下的」與「通過的」有效**:依租戶、只依廣告、只依時間(帶固定的種類)查 `write_stops`,三種篩法在 `EXPLAIN QUERY PLAN` 都得到 `SEARCH write_stops USING COVERING INDEX`,不是 `SCAN`;30 萬列時每種篩法都在 1 毫秒以下。依租戶、開始時間查 `attempts` 第一列(對應「通過的」的查法:第一列開始時間在範圍內、依租戶篩)得到 `SEARCH attempts USING INDEX attempts_first_rows_by_tenant_time`,30 萬列時約 1000 列命中僅需 3 毫秒。這部分四個索引確實讓查詢計畫按篩選欄位收斂,不看歷史總量。

**查詢四的「已用」與查詢五的「目前佔額度的」沒有被這四個索引覆蓋,而且驗證是貨真價實的全表掃描**:這兩支都是直接沿用 `aggregate_used` 的既有 SQL(`file: src/rtb/executor/attempt_store.py:366-379`)。其中「未結案」分支的條件是 `f.seq = 1 AND (f.tenant = ? OR f.tenant IS NULL) AND NOT EXISTS (...)`。實測:
- 把 `tenant = ? OR tenant IS NULL` 換成單純 `tenant = ?`,規劃器會用新索引(`SEARCH f USING INDEX attempts_first_rows_by_tenant_time`);但只要保留 `OR tenant IS NULL`(這是必要的,因為要保守計入 Phase 6 之前沒有租戶的舊列),規劃器一律回報裸 `SCAN f`——不是 `SEARCH`,新索引完全用不上。
- 用貼近production 的資料量(1,000,000 列 `attempts`、刻意只留 20 筆未結案,對齊 `MAX_UNRESOLVED = 20` 這個系統上限)實測,這支「未結案」查詢仍要 `SCAN` 過近百萬列,約 99 毫秒才找到 2 列命中;整支 `aggregate_used`(含已驗證分支)在 100 萬列時對單一租戶要約 900 毫秒。30 萬列時整支約 250–260 毫秒,接近凍結快照自己引用的「30 萬列約 0.3 秒」(`file: src/rtb/executor/attempt_store.py:365` 註解引用增量 1 代碼審第 2 輪的量測),數字對得上。

已驗證分支本身雖然是 `SEARCH v USING INDEX attempts_verified_by_time (written_at>?)`(技術上不算全表掃描),但這個索引只按時間縮小、不按租戶縮小——命中的是「全系統 24 小時內已驗證」的列,再逐列跟第一列 JOIN 之後才在記憶體裡篩租戶,所以成本仍隨系統整體驗證量成長,不是這個租戶的範圍。

## F1 「查詢五整體 O(範圍筆數)」與「目前佔額度的 O(aggregate_used) 成本」兩種說法混在同一句話裡,容易被誤讀成 [S369] 涵蓋了這條路徑

severity: minor
blocking: 否 — 屬於文件精度問題,行為本身沒有錯,風險本身也已經用「未排除:守衛面」與 REVISIT 揭露過

引句:「查詢一、二、五的成本跟「範圍內的筆數」成正比,不跟歷史總量成正比」

同一句話緊接著又說「查詢四與『目前佔額度的』跟開始一筆時的額度查詢同一個成本」(`file: governance/review-reports/rtb-phase6權限護欄與總曝險-增量4/r2-snapshot.md:342`)——但「目前佔額度的」本來就是查詢五底下的一項,前半句卻把「查詢五」整體歸進「跟範圍內筆數成正比」那一組。「未排除:守衛面」段落再講一次同樣的並列:

引句:「有了本節的索引,查詢一、二、五的成本跟範圍內筆數成正比」

緊接著同一句又寫「[S369] 釘住查詢計畫不退回全表掃描;查詢四與『目前佔額度的』跟開始一筆時的額度查詢同成本」(`file: governance/review-reports/rtb-phase6權限護欄與總曝險-增量4/r2-snapshot.md:373`)。實測確認「目前佔額度的」用的正是 `aggregate_used` 的「未結案」分支,`EXPLAIN QUERY PLAN` 回報裸 `SCAN`(見上方逐項結論),不是「不含全表掃描」意義下的安全路徑,而且 [S369] 的字面測法——

引句:「依租戶、只依廣告、只依時間查停下紀錄,與依租戶和時間查嘗試紀錄第一列」

——完全沒有提到「目前佔額度的」或查詢四的已用要測什麼查詢計畫。這樣寫的結果是:一個只照上面兩句話字面理解、沒有另外核對 `aggregate_used` 原始 SQL 的人,容易把「[S369] 釘住查詢計畫不退回全表掃描」誤讀成連「目前佔額度的」也在保護範圍內,進而誤以為它不是全表掃描——但它就是。建議把「鎖」與「未排除:守衛面」這兩句拆開寫:先講查詢一、二、五「被停下的」與「通過的」是 O(範圍筆數)且被 [S369] 釘住;另起一句明講「查詢四的已用與查詢五的『目前佔額度的』不在 [S369] 覆蓋範圍內,沿用 `aggregate_used` 既有的全表等級成本,是已知且已有回頭條件的例外」,不要把「查詢五」當成一個整體去對比。

## 關於「[S369] 照字面怎麼測、EXPLAIN QUERY PLAN 可不可靠」

`EXPLAIN QUERY PLAN` 的 `detail` 欄位在同一支 SQLite 版本內,對「用了索引查」與「掃了全表」的措辭是可穩定區分的:有索引時一律是 `SEARCH <table> USING (COVERING )?INDEX ...`,沒有索引可用時是裸 `SCAN <table>`(不帶 `USING INDEX`)。本次五種篩法的實測都吻合這個規律,拿字串比對「這一列的 detail 是否以 `SCAN ` 開頭且不含 `USING INDEX`」來斷言「不含全表掃描」,在這個 repo 用的 sqlite3(Python 內建)上是可靠、可重跑的判準。但要注意兩點,建議寫測試時處理:(1) SQLite 官方文件本身沒有承諾 `EXPLAIN QUERY PLAN` 的輸出文字格式在版本之間穩定,拿字串比對等於綁定目前這支 sqlite3 版本,不是穩固的跨版本契約,只是這個專案沒有多版本 SQLite 並存的問題,實務上可以接受;(2) 一個索引也可能被「整個掃過」(例如 `SEARCH ... USING INDEX ... (ANY(kind) AND at>? AND at<?)` 這種不帶等式條件、只靠範圍收斂的情形),文字上仍是 `SEARCH` 不是 `SCAN`,測試不該只認「有沒有出現 SCAN 字樣」,還是要連著看有沒有 `USING INDEX`,單看關鍵字「SCAN」會漏掉「裸表全掃但被誤判成安全」的情形——不過這次五支查詢的四種索引篩法實測都乾淨拿到 `SEARCH ... USING (COVERING )?INDEX`,沒有踩到這個陷阱。

## 關於「『目前佔額度的』跟既有 `aggregate_used` 同成本」是否屬實

屬實。設計把「目前佔額度的」清單做法明講是把 `aggregate_used` 拆成「列出清單」與「加總」兩步(`file: governance/review-reports/rtb-phase6權限護欄與總曝險-增量4/r2-snapshot.md:336`,引句:「不分時間範圍,現在構成已用額度的每一把鍵」),兩支查詢字面上就是同一段 SQL,不是另外重寫一份近似的邏輯,量出來的耗時也確實一致(30 萬列時兩者都落在 250–260 毫秒左右,貼近凍結快照引用的「30 萬列約 0.3 秒」)。這一點沒有問題,只是如 F1 所述,「同成本」不等於「跟範圍內筆數成正比」,兩者不該放在同一句話的同一個歸類裡讓人混淆。

## 沒有發現問題的部分

停下紀錄表三個索引(租戶+種類+時間、廣告+種類+時間、種類+時間)與嘗試紀錄第一列按租戶+開始時間的部分索引,四個都在 `CREATE INDEX IF NOT EXISTS` 語意下可以安全補到舊資料庫(結構上跟既有 `attempts_first_rows`「不存在才建」的部分索引做法一致);寫入端只多一列停下紀錄或一列嘗試,四個索引對寫入成本的影響在合成測試裡量不出顯著差異,跟設計「多兩個索引的寫入成本可忽略」的說法一致。查詢一、二(依租戶、廣告、時間篩停下紀錄)與查詢五「被停下的」在各種篩法組合下都乾淨用上對應索引,沒有退化成全表掃描,量測結果隨篩選後的列數成正比、不隨歷史總量成正比,符合設計的核心承諾。
