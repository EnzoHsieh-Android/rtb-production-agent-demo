severity: minor

## F1 依賴方向的說法跟實際 import 對不上(文件精確度,非行為錯誤)

severity: minor
blocking: 否 — 不影響行為、不破壞合約與測試,只是新寫的知識節點自己的一句話跟程式的 import 對不上

引句:「也讓依賴方向只有「這裡 → 嘗試紀錄模組」(主線同意的偏離,設計原本寫收件口模組)」

`src/rtb/executor/observability.py` 實際上 `from rtb.executor.inbox_store import StopKind`(查詢一/二靠這個列舉篩 `write_stops.kind`),所以新模組真正依賴的是「嘗試紀錄模組」+「收件口模組(StopKind、write_stops 表結構)」兩邊,不是文件講的只有一邊。這不算違反「不新增反向依賴」這條真正的架構規則(方向仍是新模組 → 收件口,收件口沒有反過來依賴新模組),`lands_in` 的分工(索引寫回各自的家)也做對了,純粹是剛寫進 `docs/.../Systems/可觀測查詢.md` 的 WHY 那句話少講了一半依賴對象,之後有人照這句話判斷「改收件口模組不會動到這裡」時可能被誤導。

## 逐條對合約與最小設計核對(未發現偏離)

- S360/S361:`observability.stop_count_query` 用 `kind = ?` 加可選的 tenant/campaign_id/since(含起點)/until(不含終點),沒給篩選就是全部;`test_aggregate_stop_count_filters_by_tenant_campaign_and_time`、`test_table_full_deferrals_count_proposals_not_events` 兩條逐項對上,含「同一份提案反覆延後只算一次」。
- S362:確認不在這次範圍,程式與測試都沒有實作待核可/已核可查詢,符合「隨增量 3 交付」的裁定,沒有誤報「沒做」。
- S363:`utilization()` 直接呼叫 `attempt_store.aggregate_used(tx, tenant, now)` 當已用,`remaining = max(0, limit - used)`,`increases_allowed = limit > 0`;門檻 0、舊列佔滿等邊界都有參數化測試覆蓋,不丟例外。
- S364:`_snapshot()` 用 `SELECT name FROM sqlite_master WHERE type='table'` 動態取表清單(不寫死),四支查詢跑完逐表比對,`test_observability_queries_write_nothing` 覆蓋四支。
- S365:`test_f7_is_explainable_with_the_observability_queries` 實際跑 F7 情境並核對查詢一、四、五的數字,已用 `.venv` 實跑通過。
- S366/S380:`InboxStore._migrate_columns` 補上「這個索引不存在」的判斷(`attempt_store.tenant_index_missing`),涵蓋「沒有租戶欄」與「欄位已補齊但缺索引」兩種舊庫,`test_an_old_attempts_table_gains_the_tenant_index_after_the_column`(兩個參數化案例)與 `test_an_old_database_gains_the_observability_indexes` 都核對索引補齊且舊列不變。停下紀錄的三個索引直接寫進 `SCHEMA`(建表就有,參照的欄位不是後補的),嘗試紀錄的部分索引照踩過的坑放進補欄位交易、補完欄位之後才建,跟計劃「### 現況」與「### 最小設計」段落描述的做法一致。
- S367:`aggregate_audit` 沒給 `since`/`until` 任一個就丟 `ValueError`;stopped 只列 `AGGREGATE_LIMIT_REACHED`(表滿延後不列);passed 用 `passed_query` 的 UNION(`tenant = ?` 與 `tenant IS NULL` 兩段,都吃時間範圍)取「通過的」,不看 24 小時窗口、`counted_now` 用 `aggregate_holdings` 的 key 集合判斷;holding(目前佔額度的)不吃範圍;`used = sum(holding.amount)` 直接餵 `_utilization` 算剩餘;四樣輸出只呼叫一次 `aggregate_holdings(tx, tenant, now)`,同一個 `now` 貫穿。`test_the_aggregate_audit_lists_what_passed_and_what_was_stopped`、`test_the_audit_uses_one_now_for_the_window_edge` 都對上。
  - 附帶一提:計劃第 339 行「目前佔額度的...(同上的欄位)」字面上要求 holding 也帶任務、修訂等欄位,但這句話比後面 r1 審計修正紀錄(第 386 行「停下與通過兩組都帶冪等鍵與任務修訂」)舊,r1 已把這個要求收斂成只有停下、通過兩組要帶,holding 只回鍵與金額;程式(`Holding` 只有 `key/amount/legacy`)跟 S367 合約原文、跟 r1 之後的決議一致,是計劃內部新舊打架、程式沒有問題,不算一條發現。
- S368:`test_the_audit_reconciles_with_used_across_every_kind_of_row` 情境跟計劃例子(加 100、舊改成 30、舊從 900 減到 500 → 630)逐項對上:有租戶的減預算/暫停/表滿延後不進通過或停下清單,舊的減預算以新預算全額列且標 legacy,已出窗口的寫入進通過清單並標 `counted_now=False`。
- S369:測試按計劃裡「PITFALL」講的坑改寫成斷言用到指定索引名(`write_stops_by_tenant`/`write_stops_by_campaign`/`write_stops_by_time`/`attempts_first_rows_by_tenant`),而不是只看「有沒有用索引」;`aggregate_used` 沿用的「還沒結案」那段明確排除在 S369 之外,合約與計劃「查詢四與查詢五的『目前佔額度的』不在其中」的敘述一致。
- S380:已併入上面 S366 的驗證。

## 實作偏離設計的放置與已核准事項

`observability.py` 新模組放查詢一、二、五(原設計寫在收件口模組),收件口模組只多動三個索引與補欄位判斷——跟 `docs/.../Systems/可觀測查詢.md` 的 WHY 段落、計劃第 300 行「狀態」欄的敘述一致,已由使用者/主線核准,不算偏離。

## 其他檢查

- 「不做」清單(告警/儀表板/定時輪詢、不另存計數器、不改寫入路徑、不做跨租戶彙總排行、不做查詢三)在程式與測試裡都沒有出現對應實作,符合。
- 實跑 `tests/executor/test_observability.py`、`tests/executor/test_aggregate_limit.py`(49 個)與整個 `tests/executor`(387 個)全綠,沒有發現假綠或迴歸。
- 每支改到/新增的檔都有家:`observability.py` → 新開的 `Systems/可觀測查詢.md`;`attempt_store.py`、`inbox_store.py` 的家(`外部寫入嘗試紀錄.md`、`提案收件口.md`)都在同一個提交裡補上對應段落,內容跟程式行為對得上。
- `Systems/可觀測查詢.md` 的兩條 RULE 都帶齊 `[since:]` `[retire:]`,PITFALL 帶測試出處,格式符合 CLAUDE.md 對筆記分類的要求。
