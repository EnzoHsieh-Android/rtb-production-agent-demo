severity: major

## F1 `awaiting_count` 用字串替換給共用條件常數加表別名,跟模組既有寫法不同

severity: major
blocking: 是 — 這是模組裡第一次出現「對共用 WHERE 片段常數做字串替換來加表別名」這種做法;`AWAITING` 這個常數在檔案裡另外三處(701、762、826 行左右)都是直接內嵌在無別名的單表查詢裡用。`awaiting_count` 為了在 `proposals p LEFT JOIN write_stops w` 裡幫 `AWAITING` 的兩個子句都補上 `p.` 前綴,選擇對字串做 `.replace(' AND ', ' AND p.')` 再手動補一個開頭的 `p.`,而不是像同一個函式(以及同一個 diff 裡的 `approval_use_count`)那樣直接把別名寫死在字面 SQL 條件裡(`u.tenant = ?`、`w.campaign_id = ?`)。這技巧目前算出來的結果是對的(`AWAITING = "state = 'pending' AND disposition = 'awaiting_approval'"`,替換後變成 `"p.state = 'pending' AND p.disposition = 'awaiting_approval'"`),但它的正確性完全依賴 `AWAITING` 字串裡「剛好只有一個 ` AND `、且緊接在欄位名前沒有多餘空白或括號」這個隱性假設;`AWAITING` 同時被其他三處無別名查詢共用,日後只要有人為了那三處而動這個常數(例如加第三個子句、改成 `IN (...)`、調整括號),這裡的 `.replace()` 會悄悄產生語法錯誤或、更糟、語意錯誤但仍可執行的 SQL,而不是在改動當下就被看見——沒有測試會因為改了 `AWAITING` 的措辭就失敗,只有跑到 `awaiting_count`/`approval_counts` 時才會出錯或算錯。這是模組裡沒有先例的第二種做法(既跟 `stop_count`/`stops` 不同,也跟同一個 diff 裡另一支新方法 `approval_use_count` 不同),屬於會在未來悄悄做出錯的行為的架構分歧,不是風格偏好。

引句:「where, params = [f"p.{AWAITING.replace(' AND ', ' AND p.')}"], []」

file: `src/rtb/executor/inbox_store.py:936`(對照無別名用法 `src/rtb/executor/inbox_store.py:826`)

## F2 `ApprovalCounts` 用 `NamedTuple`,可觀測查詢模組其他回傳型別都用 `dataclass`

severity: minor
blocking: 否 — `observability.py` 既有的四個回傳型別 `Utilization`、`Stopped`、`Entry`、`AggregateAudit` 全部是 `@dataclass(frozen=True)`;這次新增的 `ApprovalCounts` 改用 `typing.NamedTuple`。行為上兩者都不可變、都可用屬性存取(`counts.awaiting`),測試裡也同時用了屬性存取與 tuple 相等比較(`read(store, counts) == (4, 1, 3)`)——這點只有 `NamedTuple` 天生支援、`frozen dataclass` 不直接支援(需要額外定義 `__eq__`/轉 tuple),所以挑 `NamedTuple` 可能是刻意要讓測試能直接跟純 tuple 比較。但這讓同一支檔案內回傳型別的宣告方式不統一,屬於同層級的寫法分歧,不影響正確性,不構成 major。

引句:「class ApprovalCounts(NamedTuple):」

file: `src/rtb/executor/observability.py:182`

---

以下是確認過、跟既有寫法一致的觀察,不算發現。

兩支新方法開頭都先呼叫 `self._own(tx)` 再組 SQL、執行,順序跟 `stop_count`、`stops` 一致(`inbox_store.py:977`、`988` 附近);測試也照既有模式(`test_stop_queries_only_accept_a_transaction_opened_by_the_same_inbox`)幫兩支新方法各補了一段「別的收件表開的交易讀不到」斷言,擴充方式跟原本涵蓋 `stop_count`/`stops` 的寫法一致。`approval_use_count` 組動態 `WHERE` 子句用的「逐一走訪 `(clause, value)` tuple、`value is not None` 才加進 `where`/`params`」寫法,直接沿用 `stop_count_query`(`inbox_store.py:1027` 起)已有的慣用法,是這個模組既有的模式,不是新引入的技術。

查詢三(`approval_counts`)確實放在 `Systems/可觀測查詢` 管的 `src/rtb/executor/observability.py`,不是核可模組(`inbox_store.py` 裡沒有可觀測查詢的聚合函式,只有它依賴的兩個只讀方法);這跟 patch 裡計劃筆記與 `可觀測查詢.md` 自己記的「主線同意偏離」一致,程式碼的實際落點也對得上這個已核准的偏離,不是新的分歧。
