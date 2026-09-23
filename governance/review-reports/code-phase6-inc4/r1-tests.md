severity: minor

方法:在 `/private/tmp/.../scratchpad/work/rtb-3b`(複製自唯讀的 `/Users/enzo/rtb-3b`,`phase6-inc4` 分支 HEAD 8de1054)裡跑
`/Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest -p no:cacheprovider tests/executor/test_observability.py`,
基準 19 passed。逐一在臨時副本改壞 `src/rtb/executor/observability.py`、`src/rtb/executor/attempt_store.py`、
`src/rtb/executor/inbox_store.py` 後重跑,每次改完立刻還原,不影響唯讀材料。

## 每支測試對應合約與殺傷力

逐一改壞對應實作,全部真的翻紅(附上有代表性的幾組):

- `test_aggregate_stop_count_filters_by_tenant_campaign_and_time`[S360]:拿掉 tenant 篩選條件、把時間邊界從「含起點不含終點」翻成「不含起點含終點」都翻紅(後者精準抓到 `since=NOW+HOUR` 那組從 2 變 1)。
- `test_table_full_deferrals_count_proposals_not_events`[S361]:同上查詢共用同一支 `_count`,同一批 mutation 一起翻紅。
- `test_utilization_matches_the_reservation_ledger`[S363]:把 `increases_allowed` 的判準從 `limit > 0` 改成 `limit >= 0`,`limit=0` 兩組(`(50,0,...)`、`(0,0,...)`)立刻翻紅,精準對到「門檻 0 不准加預算」那條合約。
- `test_observability_queries_write_nothing`[S364]:比對「執行前後每張表全部內容」的快照,不是只看筆數,殺傷力沒問題(結構上就不容易假綠)。
- `test_the_aggregate_audit_lists_what_passed_and_what_was_stopped` / `test_the_audit_reconciles_with_used_across_every_kind_of_row`[S367/S368]:拿掉 `aggregate_audit` 裡 `if amount <= 0: continue` 這段,減預算、暫停的列混進「通過」清單,兩支測試都翻紅且精準指出多出 `down`、`pause` 兩筆;把 `aggregate_holdings` 的「還沒結案」分支整個關掉,連帶炸掉 5 支測試(`630`→`0`)。
- `test_observability_queries_use_their_indexes`[S369]:把 schema 裡 `write_stops_by_time` 整條索引拿掉,不只自己翻紅,連 S366/S380 兩支「補索引」測試也一起翻紅,交叉驗證紮實。
- `test_an_old_attempts_table_gains_the_tenant_index_after_the_column`[S380] 的 `column_but_no_index` 那組:把 `inbox_store._migrate_columns` 還原成沒有 `tenant_index_missing` 判斷的舊版(只看欄位缺不缺),精準翻紅,證明這支測試不是空湊數,是真的在測「欄位已補齊、只缺索引」這個第 3 輪才加的分支([S380] 合約原文)。

引句(凍結 patch 內,對應上面翻紅位置的合約行):「補欄位流程進交易的條件加上「按租戶的索引不存在」,增量 1 開過的資料庫才補得到,[S380] 涵蓋兩種舊庫」(`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md` r3 段落)。

## 查詢計畫測試靠索引名是否可靠

實測 `EXPLAIN QUERY PLAN`:`write_stops` 有一條因 `UNIQUE (kind, task_id, revision, content_hash)` 自動生成的索引,前綴同樣是 `kind`,理論上跟 `write_stops_by_time` 互相搶;實跑下 SQLite 在只有 `kind = ?` 條件時選的是 `write_stops_by_time`(覆蓋索引,代價較低),不是那條自動索引。所以測試檔開頭那段註解——「只看『有沒有用索引』會假綠,停下紀錄表的唯一限制以種類開頭」——講的風險是真的存在(改成斷言索引名之前的寫法確實會假綠,已用 M1 重現:去掉 tenant 篩選條件的 mutation 下,`plan` 顯示用的是 `write_stops_by_time` 而不是預期的 `write_stops_by_tenant`,若只斷言「有用到索引」會漏掉這個 bug);改成斷言指定索引名之後這個漏洞補上了。`passed_query` 的 `UNION ALL` 兩段各自都验证確實用上 `attempts_first_rows_by_tenant`(`plan.count(...) == 2`)。這個做法耦合 SQLite 查詢規劃器的版本行為(沒有跑 `ANALYZE`,靠預設啟發式),跨 SQLite 版本理論上可能選別的等價索引而讓測試假警報(非假綠,是可能誤報),但這不是本次審查範圍要擋的問題(風格/可攜性層級),不影響「有沒有真的測到」的結論。

## S365(小規模 F7)是否真的測到「哪些通過、哪些被擋」

30 個廣告各加 10、門檻 105 → 10 筆通過、20 筆被擋。追過 `execution.py` 的 `_take`:`attempt_store.begin` 在總曝險超額時是在**寫入 attempts 列之前**就丟 `AggregateLimitReached`,所以被擋的提案永遠不會出現在 `attempts` 表。測試裡 `started = {row[0] for row in h.query("SELECT key FROM attempts WHERE seq = 1")}` 因此結構上就等於「通過的 10 筆」,不會混進被擋的 20 筆。

- `{entry.key for entry in audit.passed} == started` 是紮實的檢查:`started` 直接讀 `attempts` 表(嘗試紀錄模組寫的),`audit.passed` 是被審查的 `observability.aggregate_audit` 算出來的,兩邊不同源。
- `len(audit.stopped) == 20 and not {e.key for e in audit.stopped} & started` 這行的後半段(交集為空)在目前架構下是重言式——被擋的提案結構上就不可能進 `attempts` 表,所以不管 `audit.stopped` 查詢對不對,只要它沒把 `passed` 的鍵也塞進去,這個交集檢查幾乎必過;真正做事的是前半段 `len(...) == 20`(數量)與同一支測試裡 `aggregate_stop_count` 那行的 `20`。不是假綠(不會讓錯的實作通過),但這行本身加的殺傷力有限,細粒度的「哪些鍵被擋」已經由單元測試 `test_the_aggregate_audit_lists_what_passed_and_what_was_stopped`[S367] 覆蓋(直接斷言 `audit.stopped` 的 key/task_id),S365 更像是端到端合流驗證,不是漏測。

## F1 S363 有一行同源值互比的多餘斷言

severity: minor
blocking: 否 — 風格/多餘斷言,不會做出錯的行為、不破壞合約、不是測試假綠(同一測試前一行已用具體期望值把真正的檢查做了)

`test_utilization_matches_the_reservation_ledger`[S363] 最後一行:

引句:「assert got.used == read(store, attempt_store.aggregate_used, TENANT, NOW)」

`observability.utilization` 的實作就是 `_utilization(attempt_store.aggregate_used(tx, tenant, now), limit)`,所以這行等於拿 `aggregate_used(...)` 的結果跟它自己再呼叫一次的結果比,兩邊同一支函式、同樣輸入,恆真,不提供額外殺傷力(已用 mutation 驗證:把 `_utilization` 算錯,這行從不會單獨抓到任何東西,真正抓到 bug 的都是同一測試裡前一行跟 `expected` 參數化元組的比較)。這正是材料裡 S368 測試自己註解要避開的模式(「不拿兩支查詢互相比」,`test_the_audit_reconciles_with_used_across_every_kind_of_row` 改成直接拿情境手算的 `630` 比對)。

## F2 `Holding.legacy` 沒有生產程式碼消費、也沒有測試斷言

severity: minor
blocking: 否 — 目前沒有呼叫端讀這個欄位,不構成合約被違反,只是欄位缺測試覆蓋、屬於將來若被消費才會現形的靜默風險

`attempt_store.Holding.legacy`(逐筆佔額度明細裡標記「是不是 Phase 6 之前的舊列」)這個欄位在 `src/` 裡完全沒有任何生產程式碼讀取它(`grep -rn "\.legacy\b" src/` 只在 `Passed.legacy` 有用到,`Holding.legacy` 未被消費),測試裡 `audit.holding` 也只加總 `.amount`,從沒斷言過 `.legacy`。實測把它寫死成 `False`(mutation M8),19 支測試全過。

## 結論

`tests/executor/test_observability.py` 的殺傷力整體紮實:對 S360、S361、S363、S364、S367、S368、S369、S380 各條合約的核心邏輯(租戶/時間篩選、門檻 0 特例、稽核明細計入規則、索引存在與被實際用到、舊庫補索引的兩種分支)都做過對應的壞實作驗證,全部真的翻紅,交叉驗證也存在(拿掉一個索引會連帶炸掉多支測試)。找到的兩個問題(F1、F2)都是 minor、不阻擋。S365 的「交集為空」半句斷言在目前架構下是重言式,但不是漏測,細粒度的通過/擋下判斷已由別的單元測試覆蓋,不列 finding。
