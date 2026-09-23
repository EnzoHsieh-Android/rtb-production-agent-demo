severity: major

### 1. 表滿路徑的「順手補算額度」在例外處理裡對整個系統做無界掃描,而且是在排他寫入鎖之內做,把原本 O(1) 的擋下回應變成隨系統吞吐量線性成長的鎖內工作,會議把正常的表滿背壓放大成鎖競爭 / DatabaseBusy 故障

severity: major
blocking: 是 這是可被高流量租戶(不需要特殊權限,只要能一直送提案)放大的資源耗盡面:表滿是設計上「正常觸發、會自己恢復」的背壓訊號,這次修正卻讓它每次都在同一把排他寫入鎖裡多付一筆隨全系統 24 小時吞吐量線性成長的查詢成本,而且沒有任何上限或逾時,直接命中本輪審查指名要查的「表滿路徑在例外處理裡多算一次額度會不會被利用成拖住寫入鎖或停機」

引句:「表滿延後也要記當時已用額度;這裡在例外處理裡呼叫,壞快照要自己轉成停機。」

說明:
`src/rtb/executor/execution.py:288-293` 新增的 `_aggregate_used()`,被接到 `_take()` 的 `TooManyUnresolved` 例外分支(`src/rtb/executor/execution.py:446-451`):

```
except attempt_store.TooManyUnresolved:  # 正常觸發:等未結案數降下來
    snapshot = attempt_store.AggregateLimitReached(
        _aggregate_used(tx, reservation.tenant, now), reservation.limit)
    self.store.record_stop(...)
    self.store.release(tx, receipt, now, LastFailure.TABLE_FULL)
    return Processed(Result.DEFERRED)
```

這段呼叫發生在 `self.store.transaction()`(`inbox_store.py` 的 `BEGIN IMMEDIATE`,見 `src/rtb/sqlitekit.py:54` `immediate_transaction`)已經拿到排他寫入鎖的交易裡面;而 `_aggregate_used` 轉呼叫的 `attempt_store.aggregate_used()`(`attempt_store.py:355-376`)的第一段 SQL

```
SELECT f.tenant, f.reserved_amount, f.action, f.proposal_json FROM attempts v
JOIN attempts f ON f.key = v.key AND f.seq = 1
WHERE v.state = ? AND v.written_at >= ?
```

**SQL 層完全沒有按 tenant 過濾**,篩選只在 Python 端的 `_counted()` 逐列做,所以這條查詢會把「過去 24 小時內全系統(所有租戶)已驗證的嘗試」整批抓進 Python 再篩,規模只跟系統整體吞吐量成正比,跟目前這筆提案、這個租戶、甚至跟「表滿」這件事本身完全無關。

在 r1 修正之前,`TooManyUnresolved` 分支只是原樣記一列 `(None, None)`,是 O(1) 的即時回應(見 `r2-delta.patch` 裡被拿掉的 `StopKind.TABLE_FULL, picked.proposal, reservation, None`)。這一輪把它換成「先算一次全系統額度」,而**表滿本身就是系統已經處於高並行壓力的訊號**——這正是最不該在排他鎖裡塞進無界工作的時間點。SQLite 的 `BEGIN IMMEDIATE` 在同一個資料庫檔案上只允許一個寫入者,其他工作者呼叫 `transaction()`(續租、確認、開始下一筆)都要排隊等這個鎖放掉,逾時(`BUSY_TIMEOUT_SECONDS = 5.0`,`src/rtb/sqlitekit.py:11`)還沒等到就整個失敗成 `DatabaseBusy`。

實驗(輸入 → 預期 → 實際,直接呼叫程式碼、資料庫建在暫存目錄,沒有動 repo):在暫存 sqlite 建 30 萬列「過去 1 小時內已驗證」的第 1 列(模擬一個中大型系統 24 小時窗口內的正常吞吐量,約 3.5 筆/秒),在同一個交易物件上呼叫 `attempt_store.aggregate_used()`:
- 單次呼叫:0.319 秒。
- 連續呼叫 20 次(模擬表滿持續期間陸續有 20 份提案被 `TooManyUnresolved` 擋下,每一份都各自開一個新交易、各自付一次這個成本):6.39 秒,平均每次 319.6ms。

也就是說,只要有辦法讓系統在 24 小時內累積起這個量級的已驗證嘗試(任何送提案的一方都能貢獻,不需要特權),之後每一次表滿背壓的擋下,都會讓那個工作者多握著全域寫入鎖 300ms 以上——而表滿期間本來就會有多個工作者、多份提案排隊碰壁,這些成本會逐次疊加。放大到更高吞吐量或更長窗口,單次擋下的鎖持有時間可以逼近甚至超過 5 秒的忙碌逾時,讓其他工作者的續租 / 確認 / 開始一筆直接以 `DatabaseBusy` 失敗——這已經不是「慢一點」,而是把一個設計上「等等會自己恢復」的背壓,放大成能拖垮整個執行行程並行寫入的可利用面。

file: `src/rtb/executor/execution.py:288-293`
file: `src/rtb/executor/execution.py:446-451`
file: `src/rtb/executor/attempt_store.py:363-376`
file: `src/rtb/sqlitekit.py:11,54`

另外兩個鏡頭指名的點,查過但沒有發現可被攻擊者利用的洞,附在這裡免得漏查:
- 「壞快照一律停機會不會被用來停擺整個執行行程」:`_counted()`(`attempt_store.py:379-391`)只對 `owner is None`(Phase 6 之前的舊列)才會解析快照 JSON;`begin()` 寫新列時 `tenant`/`reserved_amount` 一定跟著 `reservation` 寫入(唯一呼叫點在 `execution.py:443`,`reservation` 由 `capability_signer.grant()` 回傳、`tenant.name` 已經過 `is_id()` 驗證,不可能是 `None`),而寫入的 `proposal_json` 是 `json.dumps(proposal.to_primitives(), ...)` 產生,永遠是合法 JSON。也就是說,部署後任何走正常提案流程寫入的列都不可能踩進「舊快照壞掉」那個分支——能踩進去的只有部署 Phase 6 之前就已經存在、且真的毀損(不是格式舊,是 JSON 本身壞掉或欄位缺漏)的歷史資料,不是外部可以在系統上線後新造出來的攻擊面。這條路徑本身(`begin()` 一路 `except CorruptedAttemptRow` 轉 `ExecutorHalted`)也已經有專門測試(`test_an_unreadable_old_snapshot_halts_when_the_table_is_full_too` 等)覆蓋,是刻意設計、不是意外。
- 「窗口邊界改大於等於有沒有新的超額」:`attempt_store.py:367` 把 `v.written_at > cutoff` 改成 `>=`,效果是讓剛好卡在 24 小時整的那一列**多算進已用額度一個時間點**,方向是讓門檻算得更緊(更早擋下),不是更鬆;不會產生新的超額口子。
