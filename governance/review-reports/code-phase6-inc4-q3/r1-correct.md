severity: major

鏡頭:查詢語意與 SQL(awaiting_count / approval_use_count 的 LEFT/INNER JOIN、篩選、去重、時間範圍)。

## F1 approval_use_count 依廣告篩時,用 INNER JOIN 停下紀錄會漏掉「核可從沒被擋過就用掉」的真實放行列,違反 S362 的等式合約

severity: major
blocking: 是 — 破壞合約:S362 明寫「已核可放行數應等於核可使用表中符合篩選的列數」,依廣告篩時實測會少算,而且沒有像 `awaiting_count` 那樣把接不到的另外回報,是悄悄丟資料;這支查詢正是用來答「人工審核可觀測」與事故 F7「稽核可說明哪些通過」的完成條件,數字錯會直接誤導看核可用量的人。

引句:「JOIN write_stops w ON w.task_id = u.task_id AND w.revision = u.revision "
                "AND w.content_hash = u.content_hash AND w.kind = u.stage」

`src/rtb/executor/inbox_store.py:964-966`(`approval_use_count`)在收到 `campaign_id` 時,把 `approval_uses` INNER JOIN 到 `write_stops`,靠 `(task_id, revision, content_hash, kind=stage)` 才能得到廣告欄位(因為 `approval_uses` 本身不存廣告)。但 `execution.py` 的 `_gate`(`src/rtb/executor/execution.py:449-460`)在「比例過大」那一關,只要 `_approvals()` 查到的核可已經有效(`RATIO in held`)就直接放行、**不會呼叫 `_await`、也就不會寫 `write_stops`**;`approval.issue()`(`approval.py:72-`)簽核可時也完全不檢查這份提案當下是不是待核可或曾被擋過。也就是說,管理端可以對一份「還沒被擋過」的提案預先核可,執行迴圈用到它時仍會經 `_audit`(`execution.py:679-696`)寫一列 `approval_uses`,但這一關從未產生對應的 `write_stops` 列。這種列一旦被 `campaign_id` 篩選就會被 INNER JOIN 濾掉、從已核可放行數裡消失,而且不像 `awaiting_count` 有「接不到的另外回報」的未知桶,呼叫端拿到的就是一個偏低、沒有任何警示的數字。

凍結 patch 裡自己的測試已經印證了這個行為,只是把它當成預期值寫死,而不是把它當成缺陷處理:

引句:「只接得到總曝險那一關的停下紀錄」

`tests/executor/test_observability.py`(patch 內新增段落)裡,任務 `a1`(`campaign_id="c5"`)真的有兩列 `approval_uses`(AGG 與 RATIO 各一列,見 `_approval_use(store, applied, AGG, ...)` 與 `_approval_use(store, applied, RATIO, ...)`),但只有 AGG 那一關另外用 `stop(store, AGG, task="a1", campaign="c5")` 補了一列 `write_stops`,RATIO 那一關完全沒有停下紀錄。測試斷言 `read(store, counts, campaign_id="c5") == (0, 1, 1)`,已核可放行數只算到 1,漏掉了同一份提案、同一個廣告底下真實存在的第二列。

已在臨時目錄用 `/Users/enzo/rtb-3b` 的程式碼(唯讀,複製到 scratchpad 後操作)重現,不靠測試檔既有的 fixture,直接組一份最小情境:同一個任務 `a1`/廣告 `c5` 下真實有兩列 `approval_uses`,只補一列對應的 `write_stops`:

```
$ /Users/enzo/rtb-production-agent-demo/.venv/bin/python /private/tmp/.../scratchpad/repro_approval_undercount.py
approval_uses rows really belonging to task a1/campaign c5: 2
approval_use_count(tenant only)          = 2
approval_use_count(tenant + campaign_id) = 1
```

不篩廣告時正確算出 2;一加上 `campaign_id` 篩選就變成 1,跟 `approval_uses` 表裡真實屬於那個廣告的列數對不上。

`docs/rtb-production-agent-demo-knowledge/Systems/提案收件口.md`(patch 內)的說明句「已核可放行數只讀核可使用表的租戶、任務、修訂、內容雜湊與關卡,不讀核可表」也只列了這五欄,沒有提到依廣告篩要另外接 `write_stops`,這條實作分支(campaign 篩選時的 INNER JOIN)不在那句描述涵蓋範圍內,筆記需要一併補上這個限制,而不只是程式碼要修。

---

以下是確認過沒問題的部分,供對照:

`awaiting_count`(`inbox_store.py:926-947`)把租戶、廣告條件放進 WHERE,對 LEFT JOIN 到的 `write_stops` 確實會退化成內連接(NULL 值過不了 `w.tenant = ?`/`w.campaign_id = ?`),但這是刻意設計並且有測試釘住:接不到停下紀錄的份數不套用篩選、永遠回全體未知數(`unknown` 那條查詢完全不帶 tenant/campaign 參數),對應 `ApprovalCounts.awaiting_unknown_tenant` 的欄位名稱與 `Systems/提案收件口.md` 的「接不到的另外回報」一致,`test_approval_counts_cover_waiting_and_applied` 對多種篩選組合都驗過(`tenant=TENANT`→`(2,1,2)`、`campaign_id="c2"`→`(1,1,0)`、`tenant="nobody"`→`(0,1,0)` 都保留 `unknown=1`)。`write_stops` 對 `(kind, task_id, revision, content_hash)` 有 UNIQUE 限制,JOIN 條件又鎖了 `w.kind = p.block_code`,所以同一份提案先前在別的關卡停過(測試裡 `w1` 先在 AGG 待核可、又額外補一列 RATIO 的停下紀錄)不會被兩次計入,只接目前這一關的那一列。

`approval_use_count` 的時間範圍用 `u.at >= ?` 與 `u.at < ?`,含起點不含終點,跟設計「時間範圍一律包含起點、不包含終點」一致,測試 `since=NOW+HOUR`/`until=NOW+HOUR` 兩個方向都對得上。`approval_uses` 對 `(task_id, revision, content_hash, stage)` 也有 UNIQUE 限制,`campaign_id` 篩選時的 JOIN 不會因為多列停下紀錄而讓同一列 `approval_uses` 被算兩次(kind 對應到唯一一列)。
