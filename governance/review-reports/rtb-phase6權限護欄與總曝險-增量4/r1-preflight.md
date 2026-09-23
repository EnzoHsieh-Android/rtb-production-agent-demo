# Phase 6 增量 4(可觀測)設計前掃

被掃材料:`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md` 的「## 增量 4 設計:可觀測」一節(含其下的實務隱患、落點、使用者裁定與相依小節),行號約 298–376(對照 repo `/Users/enzo/rtb-3b`)。

---

## ① 未定義的詞

結論:**沒有命中本節/上下文都沒解釋的術語**。本節用到的「嘗試紀錄」「收件表」「停下紀錄表」「冪等鍵」「已用額度」「封頂」「全域寫入鎖」等,在同一份計劃的增量 1/2/3 節都先定義過。

- 唯一的邊界案例:「Phase 5」(302、320、325 行等處反覆提到「比照 Phase 5 的最小指標」)在本文件裡從未展開解釋是什麼,只當成既有慣例引用。
  - 原句(逐字,302 行):「PRIOR-ART(增量 4): 最小指標沿用 Phase 5 的做法——執行側的嘗試紀錄模組有 `version_conflict_count`……」
  - 依據:全文 grep 不到本文件內有對「Phase 5」的定義句;但 `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase5樂觀鎖與重新規劃_計劃.md` 確實存在,是專案既有階段代稱,非本節杜撰詞。
  - 建議改法:不算必改的缺陷(專案全篇都用「Phase N」當階段代稱,增量 2 節也一樣用「Phase 7 留下的待辦」而不展開),若要更嚴謹可在第一次出現處補一個 `[[Projects/RTB_Phase5樂觀鎖與重新規劃_計劃]]` 連結。

---

## ② 壞引用

結論:**本節提到的檔案、函式、表、合約編號、筆記連結全部存在,合約編號 S360–S367 未跟全庫撞號**。

- 表:`write_stops`(318 行)——存在,`src/rtb/executor/inbox_store.py:153-158`。
- 函式:`aggregate_used`(319、332、352 行)——存在,`src/rtb/executor/attempt_store.py:355`。
- 函式:`load_tenants`(319 行)——存在,`src/rtb/executor/capability_signer.py:114`。
- 欄位:`aggregate_limit`(319 行)——存在,`src/rtb/executor/capability_signer.py:44`(`Tenant.aggregate_limit`,預設 0)。
- 函式:`version_conflict_count`(302 行)——存在,`src/rtb/executor/attempt_store.py:275`。
- 函式:`replan_counts`(302 行)——存在,`src/rtb/analyzer/task_store.py:539`,回傳型別 `ReplanCounts`(具名結果),與敘述「回一個小型具名結果」相符。
- `transaction()`(319、339 行)——存在,`InboxStore.transaction`,`src/rtb/executor/inbox_store.py:372`,內部走 `immediate_transaction`(`BEGIN IMMEDIATE`),`src/rtb/sqlitekit.py:53-62`。
- 提交雜湊:`b7a67bd`(300 行,「以增量 3 設計第 1 版 b7a67bd 為參照」)、`ff06a03`(316 行,「程式以 ff06a03 為準」)——皆存在於 git log,且 `ff06a03` 正好是增量 1 合併之後、增量 3 之前的那個提交(`chore(lumos): 記錄代碼審通過`),跟本節「查詢三要等增量 3 實作」的時序敘述一致。
- Systems 連結:`[[Systems/提案收件口]]`、`[[Systems/外部寫入嘗試紀錄]]`(371 行落點小節)——兩篇筆記檔都存在於 `docs/rtb-production-agent-demo-knowledge/Systems/`。
- 合約編號撞號檢查:`grep -rn "\[S36[0-7]\]" docs/` 除本文件外**沒有任何命中**;在本文件內 S360–S367 各只出現一次,無重複編號。

沒有建議改法(本項無命中)。

---

## ③ 範圍自相矛盾

**命中一條:「核可使用表」與「核可稽核表」是同一件事的兩個名字,在本節內部互相打架。**

- 計劃原句(逐字):
  - 321 行:「……核可生效時寫一列核可稽核。主線已說第 2 版會:停下紀錄加「比例過大」種類、**核可使用表**帶租戶欄……」
  - 331 行:「已核可放行數:**核可使用表**(增量 3 第 2 版,帶租戶)的列數,可給時間範圍與租戶、廣告篩。」
  - 351 行([S362]):「已核可放行數應等於**核可稽核表**中符合篩選的列數(等增量 3 實作)。」
  - 376 行:「相依增量 3 第 2 版(主線已答應折入):停下紀錄加「比例過大」種類、**核可使用表**帶租戶欄……」
- 依據:同一份文件內「核可使用表」出現 3 次(321、331、376),「核可稽核表」只在合約 [S362](351 行)出現 1 次;而增量 3 節本身([S355],289 行)寫的是「一列核可稽核」,並未把它稱作「表」,也沒有定過表名叫「核可使用表」或「核可稽核表」。三種寫法(核可稽核 / 核可使用表 / 核可稽核表)指同一張表,名字卻對不上。
- 建議改法:在增量 4 節內統一成一個名字(建議跟增量 3 的用語對齊,例如「核可稽核表」或明確定義「核可使用表 = 增量 3 [S355] 寫的核可稽核紀錄」),並把 [S362] 的用詞改成跟 321/331/376 行一致;否則實作 [S362] 的人不知道要接哪張表。

其餘檢查(均未發現矛盾,列出以說明已核對過):
- 與「增量拆法」(37 行,只提「總曝險擋下次數、待人工核可數、額度使用率」三支查詢)相比,增量 4 節實際定義了五支查詢(多了查詢二「表滿延後次數」與查詢五「總曝險稽核明細」)。但這兩支的來源在本節內都有交代且有出處(查詢二引增量 1 設計審第 3 輪、使用者 2026-09-23 確認;查詢五引使用者 2026-09-23 裁定、且對應到「這份計劃在解決什麼」一節裡 F7 的原始完成條件「稽核要說得出哪些通過、哪些被擋」),不算矛盾,只是「增量拆法」那條摘要沒有同步更新成五支——影響很小,不列為命中,僅提醒。
- 與「不做的事」小節(343-345 行)比對:「不做告警、儀表板、定時輪詢(Phase 9)」「不另存計數器,不改任何寫入路徑(只讀)」「不做跨租戶的彙總排行」,跟最小設計裡「每支都是模組層函式、收交易、只讀不寫」「查詢一/二/四/五都可依租戶篩、查詢五本身就是單一租戶」一致,沒有打架。
- 與「使用者裁定」小節比對:326-336 行(查詢一到五)跟 375 行「使用者 2026-09-23 裁定:加查詢五」一致;376 行「相依增量 3 第 2 版」跟現況小節 321 行的轉達內容一致(只是上面提到的表名不一致)。
- 前提句「前提:增量 1 已合併;查詢三要等增量 3 實作」(300 行)跟現況小節「增量 3(設計第 1 版,還沒實作)」(321 行)一致,沒有把還沒做的東西當已完成用。

---

## ④ 機械宣稱驗語意

逐句核對本節(含現況、最小設計、實務隱患小節)裡描述既有程式行為的句子,不只驗存在、驗語意是否相符。

| # | 計劃原句(逐字節錄) | 核對結果 | 依據(file:行號) |
|---|---|---|---|
| 1 | 「停下紀錄表(`write_stops`,收件口模組建表):種類只有兩種——總曝險已滿、表滿延後」 | 屬實 | `src/rtb/executor/inbox_store.py:153-158`(CREATE TABLE write_stops);`inbox_store.py:94-98`(`StopKind` 只有 `AGGREGATE_LIMIT_REACHED`、`TABLE_FULL` 兩個成員) |
| 2 | 「欄位有任務、修訂、內容雜湊、冪等鍵、租戶、廣告、金額、當時已用額度、當時門檻、是否封頂、時間」 | 屬實 | `inbox_store.py:153-158`:`task_id, revision, content_hash, key, tenant, campaign_id, amount, used, cap, capped, at` 逐一對得上(id 為內部自增主鍵,計劃未提及,合理) |
| 3 | 「同一份提案(任務、修訂、內容雜湊)同一種類只記一列,只增不改、不被清理」 | 屬實 | `inbox_store.py:158`(`UNIQUE (kind, task_id, revision, content_hash)`);`inbox_store.py:618-633`(`record_stop` 用 `INSERT OR IGNORE`,全庫 grep `write_stops` 只有建表與這一處 INSERT,沒有 UPDATE/DELETE) |
| 4 | 「**沒有任何索引**」 | 屬實 | `inbox_store.py` 的 `SCHEMA` 常數裡 `write_stops` 沒有任何 `CREATE INDEX`;對照 `attempt_store.py:53-58` 對 `attempts` 表確實有建索引,兩者形成對比,佐證「沒有索引」是刻意指出的現況缺口 |
| 5 | 「租戶在「沒有簽發到租戶的舊路徑」才是空值」 | **不屬實** | `Stop.tenant` 型別雖標成 `str \| None`(`inbox_store.py:108`),但 `write_stops` 是 Phase 6 才新增的表,沒有 Phase 6 之前留下的舊資料;目前程式裡唯一產生 `Stop` 的地方是 `execution.py:490-492`(`_stop` 靜態方法),參數 `reservation.tenant` 一律來自 `execution.py:432` 的 `Reservation(signed.tenant, ...)`,而 `signed.tenant` 是**簽發成功之後**才拿得到的真實租戶名(`Reservation.tenant: str`,`attempt_store.py:99-104`,非 Optional)。兩個寫入呼叫點(`execution.py:448-450` 總曝險已滿、`execution.py:453-455` 表滿延後)都在簽發成功之後才會走到,目前程式碼裡沒有任何路徑會讓 `write_stops.tenant` 真的寫成 NULL。這句話疑似把「嘗試紀錄 `attempts` 表裡 Phase 6 之前的舊列沒有租戶」(增量 1 節 86-90 行講的東西,那是真的)誤搬到 `write_stops`(一張全新的表)上。 |
| 6 | 「`aggregate_used(交易, 租戶, 現在)`:算 24 小時滾動窗口內已驗證的預留加上所有未結案的預留,舊列保守計入」 | 屬實 | `attempt_store.py:355-380`:簽章 `aggregate_used(tx, tenant, now)`;已驗證分支用 `AGGREGATE_WINDOW`(=24 小時,`attempt_store.py:72`)過濾;未結案分支不管時間;`_counted`(383-395 行)對缺租戶/缺金額的舊列保守計入每個租戶 |
| 7 | 「門檻在租戶設定檔的 `aggregate_limit`(缺這欄當 0),由簽發模組的 `load_tenants` 讀」 | 屬實 | `capability_signer.py:44`(`Tenant.aggregate_limit: int = 0`);`capability_signer.py:104-107`(`spec.get("aggregate_limit", 0)`,缺欄回 0);`capability_signer.py:114-115`(`load_tenants`) |
| 8 | 「既有的 Phase 5 指標查詢收的交易是 `transaction()` 給的寫入交易(BEGIN IMMEDIATE),查詢期間握著全域寫入鎖」 | 屬實 | `sqlitekit.py:43-46`(`begin_immediate` 執行 `BEGIN IMMEDIATE`);`sqlitekit.py:53-62`(`immediate_transaction` 用它);`inbox_store.py:372-381`(`InboxStore.transaction` 走 `immediate_transaction`);`task_store.py:247,262,360,436,454` 也用同一支 `immediate_transaction` |
| 9 | 「已用額度那段在 30 萬列時實測一次約 0.3 秒(增量 1 代碼審第 2 輪)」 | 屬實(有可追溯出處) | 程式註解原句一字不差出現在 `attempt_store.py:365`(「……代碼審第 2 輪資安席實測 30 萬列時一次 0.3 秒」),且能在 `governance/review-reports/code-phase6-inc1/r3-snapshot.patch:161` 與 `r3-delta.patch:10` 追到同一句話的落地紀錄 |
| 10 | 「執行側的嘗試紀錄模組有 `version_conflict_count`(收交易、可依廣告篩、回一個整數)」 | 屬實 | `attempt_store.py:275-283`:簽章 `version_conflict_count(tx, campaign_id=None) -> int`,可選 `campaign_id` 篩選,回傳 `int` |
| 11 | 「分析側的任務表模組有 `replan_counts`(回一個小型具名結果)」 | 屬實 | `task_store.py:539`:簽章 `replan_counts(store, campaign_id=None) -> ReplanCounts`,`ReplanCounts` 是具名結果型別 |

---

## 摘要

四項機械檢查裡,①②沒有命中(唯一提示是「Phase 5」在本文未展開定義,但屬全篇慣例、非本節獨有問題,不算缺陷);③命中一條——「核可使用表」(321、331、376 行)跟合約 [S362] 用的「核可稽核表」(351 行)是同一張表卻撞出兩個名字,建議統一用詞;④逐句核對 11 句既有程式行為宣稱,10 句屬實、1 句不屬實——「租戶在『沒有簽發到租戶的舊路徑』才是空值」(318 行)誤把 Phase 6 之前 `attempts` 舊列缺租戶的情況套到全新的 `write_stops` 表上,現有程式碼裡 `write_stops.tenant` 唯一的寫入路徑都在簽發成功之後,不存在空值路徑。
