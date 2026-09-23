severity: clean

這批修正(`src/rtb/executor/inbox_store.py` 的 `approval_use_count_query`)把「依廣告篩已核可放行數」從子查詢改成 JOIN,並把比對欄位從「任務、修訂」擴成再加「操作鍵」。從攻擊者鏡頭看,重點是三件事:SQL 是不是只拼固定條件、握寫入鎖期間各種篩選組合是否會全表掃描、JOIN 會不會重複計數。三件事都查過,沒有能被利用的洞。

## SQL 拼接與參數化

`approval_use_count_query` 裡固定拼進 SQL 字串的只有欄位比對句式與 JOIN 骨架本身(`f.key = u.key AND f.seq = 1 AND f.task_id = u.task_id AND f.revision = u.revision`),所有篩選值(`tenant`、`campaign_id`、`since`、`until`)一律用 `?` 佔位、走 `params` 綁定,`# noqa: S608` 的標註屬實。

引句:「JOIN attempts f ON f.key = u.key AND f.seq = 1 AND f.task_id = u.task_id」

沒有任何使用者可控字串被直接串進 SQL 文字(欄位名、表名都是常數),不構成注入面。呼叫鏈(`observability.approval_counts` → `store.approval_use_count` → 這支查詢函式)裡 `tenant`/`campaign_id` 全程當成參數值傳遞,沒有中途被拼接。

## 握寫入鎖期間的查詢計畫(各種篩選組合)

`InboxStore.transaction()` 用 `immediate_transaction`(`BEGIN IMMEDIATE`),整個交易期間持有寫入鎖;`approval_use_count` 在這個交易裡執行,所以查詢計畫是否全表掃描直接關係到寫入端會被卡多久。

在臨時目錄建庫(`/tmp/rtb_sec_test`)灌入 3–4 萬列 `attempts`/`approval_uses`(含一個刻意用一半資料量的「熱門廣告」skew case)並跑 `ANALYZE` 後,窮舉 `{tenant, campaign_id, since, until}` 的全部 16 種子集組合,用 `EXPLAIN QUERY PLAN` 逐一檢查,結果除了「完全不給篩選條件」(語意上就是要算全表筆數,新舊版本都一樣)之外,其餘全部組合都是 `SEARCH`,沒有一個是 `SCAN u`:

```
['campaign_id'] => SEARCH f USING INDEX attempts_first_rows (campaign_id=?) SEARCH u USING INDEX sqlite_autoindex_approval_uses_1 (task_id=? AND revision=?)
['campaign_id', 'tenant'] => SEARCH u USING INDEX approval_uses_by_tenant (tenant=?) SEARCH f USING INDEX sqlite_autoindex_attempts_1 (key=? AND seq=?)
['campaign_id', 'since', 'until'] => SEARCH f USING INDEX attempts_first_rows (campaign_id=?) SEARCH u USING INDEX sqlite_autoindex_approval_uses_1 (task_id=? AND revision=?)
['campaign_id', 'since', 'tenant', 'until'] => SEARCH u USING INDEX approval_uses_by_tenant (tenant=? AND at>? AND at<?) SEARCH f USING INDEX sqlite_autoindex_attempts_1 (key=? AND seq=?)
```

規劃器會依可用篩選挑「較窄的那一側」當驅動表(有 `tenant`/`since`/`until` 就走 `approval_uses_by_tenant`/`approval_uses_by_time`,只有 `campaign_id` 就走 `attempts_first_rows` 部分索引),另一側一律用主鍵或唯一限制的前綴做點查,不是掃全表。material 裡新加的測試(`test_applied_count_uses_its_index`)只覆蓋 4 組單一篩選,沒有覆蓋這些組合;補測過後結果是乾淨的,沒發現全表掃描,所以不成立可回報的發現(沒有 blocking 項)。

用「熱門廣告」skew 情境(4 萬列裡 2 萬列共用同一個 `campaign_id`)量測,依廣告篩仍然走 `attempts_first_rows` 索引,實測 5 次平均約 14ms 回應 2 萬列比對,量測方式:

```
python /tmp/rtb_sec_test/probe4.py
# SEARCH f USING INDEX attempts_first_rows (campaign_id=?) SEARCH u USING INDEX sqlite_autoindex_approval_uses_1 (task_id=? AND revision=?)
# result (20000,) time=0.01387
```

這個耗時跟「符合篩選條件的實際列數」成正比,不是跟全表大小成正比,屬於這類統計查詢本身該有的複雜度,不是這批修正引入的全表掃描。要不要防「刻意灌一個超熱門的廣告把寫入鎖卡住」這種故意繞過,材料明寫「防忘記,不防刻意繞過」的威脅模型排除在外,故只記錄不列為發現。

## JOIN 會不會重複計數

`attempts` 的主鍵是 `(key, seq)`,JOIN 條件固定 `f.seq = 1`,等於「給定一個 `key`,`attempts` 裡最多一列符合」;`approval_use_count_query` 的 JOIN 是 `f.key = u.key AND f.seq = 1 AND ...`,對每一列 `approval_uses u` 而言,右側最多配對到 0 或 1 列 `attempts`,不會出現一對多把同一列 `u` 灌水算成好幾筆的情況。核可使用表本身的 `UNIQUE (task_id, revision, content_hash, stage)` 也保證同一份提案同一關只有一列,兩層限制疊在一起,`count(*)` 不會重複計數。

引句:「PRIMARY KEY (key, seq));」(`attempts` 建表句,`src/rtb/executor/attempt_store.py:51`)

## 測試與回歸

在唯讀前提下於 `/Users/enzo/rtb-3b` 執行(不動工作目錄/暫存區,只跑既有測試):

```
/Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest -p no:cacheprovider \
  tests/executor/test_observability.py tests/executor/test_approval.py tests/executor/test_aggregate_limit.py -q
# 123 passed in 9.15s
```

材料裡新加的查詢計畫測試 `test_applied_count_uses_its_index` 除了斷言用到 `approval_uses_1`/索引前綴外,還多斷言 `not re.search(r"\bSCAN u\b", plan)`,這一句正是防「子查詢用了索引、外層照樣整張掃 `approval_uses`」的假綠模式,寫得對、能抓到回歸(材料註解裡自己也點出這點,查證屬實)。

看過的檔:
- `/Users/enzo/rtb-3b/governance/review-reports/code-phase6-inc4-q3/r3-snapshot.patch`
- `/Users/enzo/rtb-3b/src/rtb/executor/inbox_store.py`
- `/Users/enzo/rtb-3b/src/rtb/executor/attempt_store.py`(交叉查證 `attempts` 表結構與索引)
- `/Users/enzo/rtb-3b/src/rtb/executor/observability.py`(交叉查證呼叫鏈與參數來源)
- `/Users/enzo/rtb-3b/tests/executor/test_observability.py`
- `/Users/enzo/rtb-3b/docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md`
- `/Users/enzo/rtb-3b/docs/rtb-production-agent-demo-knowledge/Systems/提案收件口.md`
