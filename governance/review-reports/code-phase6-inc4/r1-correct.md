severity: clean

範圍:`src/rtb/executor/observability.py` 四支唯讀查詢(`stop_count_query`/`aggregate_stop_count`/
`table_full_deferral_count`、`utilization`、`passed_query`/`aggregate_audit`),連帶
`attempt_store.py` 的 `aggregate_holdings`/`counted_amount`/`latest`/`iso`/`connection`/
`TENANT_INDEX`/`tenant_index_missing`,以及 `inbox_store.py` 補索引那段。逐項核對材料要求的六個
點,並在 `/tmp/rtb3b-copy`(`/Users/enzo/rtb-3b` 的唯讀複本)實際造資料重跑,沒有發現違反合約或造
成錯誤行為的問題。

## 逐點核對

**篩選條件組法**(`stop_count_query`):`kind = ?` 固定帶,`tenant`/`campaign_id`/`since`/`until`
四個都是「有給值才加條件」,沒給就是「全部」,跟 [S360] 「沒給篩選條件就算全部」一致。用
`stop_count_query(AGG, tenant=TENANT)` 等組合實測,計數與手算一致。

**時間範圍含起點不含終點**:`since` 用 `at >= ?`、`until` 用 `at < ?`(`stop_count_query`),
`written_at >= ? AND written_at < ?`(`passed_query`、`aggregate_audit` 的 stopped 查詢)全部一致。
實測:同一個 `NOW` 開一筆停下紀錄,`since=NOW` 算進去、`until=NOW` 不算進去,跟 [S360] 註解「包含
起點」「不包含終點」的測試斷言相符。

**`passed_query` 的 UNION ALL 會不會重複或漏列**:兩段分別是 `tenant = ?` 與 `tenant IS NULL`,
一列的 `tenant` 欄位只會是其中一種,兩個條件互斥,`UNION ALL` 不會因此重複,也不會漏列(沒有第三
種值)。用兩筆 `written_at` 完全相同的攤留造資料驗證過,兩筆都各出現一次、排序穩定。

**通過清單的逐列計入與 `counted_now`**:`aggregate_audit` 用 `attempt_store.counted_amount` 對
`passed_query` 撈出的每一列計算金額,跟 `aggregate_holdings`/`aggregate_used` 用的是同一支
`_counted`(`counted_amount` 只是它的公開別名),`amount <= 0` 就跳過(減預算、暫停、別租戶的舊
列),符合 [S367]「同一條逐列計入規則」的要求。`counted_now` 用 `key in {holding 的 key}` 判斷,
跟「目前佔額度」是同一份清單來源,不會對不上。

**目前狀態取法(`latest` 對每把鍵各查一次)**:`latest()` 是 `ORDER BY key = ? ... ORDER BY seq
DESC LIMIT 1` 的主鍵點查,在同一個交易、同一把全域寫入鎖之內對每個 `passed` 列各查一次;因為交易
期間沒有其他寫入者能插隊,不會有讀到不一致狀態的競態。是 N+1 次點查(不是全表掃描),沒有違反
[S369] 只釘住的那幾條查詢計畫,合約也沒說這段要走索引成本分析;沒發現正確性問題。

**`aggregate_holdings` 已驗證窗口段與未結案段會不會同一把鍵出現兩次**:已驗證段篩
`v.state = VERIFIED`,未結案段篩「這把鍵沒有任何一列的狀態落在 `TERMINAL_LIST`」;`VERIFIED` 本身
就在 `TERMINAL_LIST` 裡,而且 `attempt_store.py` 的模組註解明講「終點沒有出路,資料庫的唯一限制
不准同一把鍵寫第二列終點列」,所以一把鍵一旦出現在已驗證段,必定不會通過未結案段的 `NOT EXISTS`
篩選,兩段互斥。實測:一筆走到 `VERIFIED`、一筆停在 `IN_FLIGHT`(未結案),`aggregate_holdings`
回兩筆、不多不少,`sum(amount) == aggregate_used(...)`。

**排序是否穩定**:`stopped` 用 `ORDER BY at, id`(`id` 是 `AUTOINCREMENT` 主鍵,唯一遞增);
`passed` 用 `passed.sort(key=lambda e: (e.started_at, e.key))`,`key` 是操作鍵、同一批資料裡唯一。
兩邊在時間相同時都有唯一值當次要鍵,排序結果決定性、不受資料庫回傳順序影響。實測兩筆
`started_at` 完全相同的 `passed` 列,排序結果穩定等於依 `key` 字串排序。

## 一般觀察(非發現)

- `_iso()` 的固定格式字串 `"%Y-%m-%dT%H:%M:%S.%fZ"` 在 `attempt_store.py` 與 `inbox_store.py`
  各自獨立定義一份(不是共用同一個函式)。目前兩邊逐字相同,字串比較能正確對應時間先後;但這是
  維持正確性的隱性前提(兩邊若之後各自改動格式會悄悄破壞 `write_stops.at` 與 `attempts.written_at`
  跨表字串比較),不是這次改動引入的新問題,材料裡也沒有要求動這塊,所以不算發現,提出來備查。
- `passed`/`stopped` 的旗標與金額計算(`counted_amount`、`amount <= 0` 跳過的規則)跟
  `aggregate_holdings` 共用同一份邏輯,沒有另外重寫一份計入規則,符合
  `Systems/可觀測查詢.md` 的 `RULE:`(逐列計入規則只能有一份)。

## 重跑指令(可重現)

```
cd /tmp/rtb3b-copy   # /Users/enzo/rtb-3b 的唯讀複本
/Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest -p no:cacheprovider \
  tests/executor/test_observability.py tests/executor/test_aggregate_limit.py -q
# 49 passed
/Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest -p no:cacheprovider \
  tests/executor -q
# 387 passed
```

另外自己寫的兩支探針腳本(驗證 UNION ALL 互斥、驗證兩段不重複、驗證時間邊界)跑起來跟預期一致,
沒有另外存檔在 repo 裡(在 /tmp 底下,不影響材料)。

## 結論

四支查詢在篩選條件組法、時間範圍半開區間、UNION ALL 互斥、通過清單計入規則共用、`latest` 點查、
`aggregate_holdings` 兩段互斥、排序穩定性七個面向都沒有發現違反合約或會做出錯行為的問題;沒有
major/blocking 發現。
