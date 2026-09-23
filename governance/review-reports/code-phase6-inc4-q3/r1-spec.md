severity: minor

比對範圍:凍結 patch(governance/review-reports/code-phase6-inc4-q3/r1-snapshot.patch)逐句對照計劃增量 4 節「查詢三」描述與既有合約 [S362],並核對 `src/rtb/executor/inbox_store.py` 的 `awaiting_count`/`approval_use_count`、`src/rtb/executor/observability.py` 的 `approval_counts`、`tests/executor/test_observability.py::test_approval_counts_cover_waiting_and_applied`,以及 `Systems/可觀測查詢.md`、`Systems/提案收件口.md`、計劃狀態行。已在 `/Users/enzo/rtb-3b` 用 `.venv/bin/python -m pytest tests/executor/test_observability.py -p no:cacheprovider -q` 全綠(24 passed)。

逐項核對結果(規格 5 點,皆兌現):
- 按提案去重:`awaiting_count` 的 LEFT JOIN 條件是 `w.task_id/revision/content_hash/kind = p.block_code`,配合 `write_stops` 的 `UNIQUE (kind, task_id, revision, content_hash)`,同一份提案只會接到「目前停在哪一關」那一列,不會因為提案先前在別關也停過而重複計入(測試 `stop(store, RATIO, task="w1", ...)` 專門驗證此情境)。
- 依租戶、廣告篩:`awaiting_count`/`approval_use_count` 都用 `tenant`/`campaign_id` 參數轉成 `w.tenant`/`w.campaign_id`(或 `u.tenant`)條件,行為與測試矩陣(`read(store, counts, tenant=...)`、`campaign_id=...`)一致。
- 接不到的列進未知租戶份數:`awaiting_count` 的第二條查詢回報「AWAITING 且 w.id IS NULL」的總數,對應 `ApprovalCounts.awaiting_unknown_tenant`,且不套用篩選條件——這點正好對應計劃「最小設計」原文「接不到停下紀錄的待核可提案只算進不篩的總數、篩選時不算」,是照設計故意不篩,不是漏篩。
- 已核可放行吃時間範圍:`approval_use_count` 的 `since`/`until` 只作用在 `u.at`,`awaiting_count` 不吃時間參數;`approval_counts()` 組裝時把 `since/until` 只轉給 `approval_use_count`,符合 `ApprovalCounts` docstring「待核可是當下快照,不吃時間範圍;已核可放行吃範圍」。
- 只用核可使用表四欄:`approval_use_count` 只讀 `u.tenant/u.task_id/u.revision/u.content_hash`(加 `u.stage` 對應停下紀錄種類做廣告篩),不讀 `approvals` 表,跟計劃「最小設計」與 [S362] 一致。

追查一個潛在疑點後排除:`approval_use_count` 在有 `campaign_id` 時用 INNER JOIN 接 `write_stops`,若某筆 `approval_uses` 列接不到對應 `kind` 的停下紀錄,篩選時會被整列排除且不回報(不像 `awaiting_count` 有「未知租戶份數」)。追到 `src/rtb/executor/execution.py` 的 `_gate`/`_audit`(增量 3 既有邏輯,本次 diff 未動):只有 `guardrails.increase_too_large` 為真時 RATIO 才留在 `held`,若這一輪才發現超比例且沒有現成核可,會先進 `_await`(寫停下紀錄)才擋下,不會同一輪立刻寫 `approval_uses`;AGGREGATE 同理靠 `begun.over_limit is not None` 把關。也就是說在真實流程裡,能寫進 `approval_uses` 的 (task, revision, hash, stage) 組合一定先有對應的 `write_stops` 列。`tests/executor/test_observability.py` 裡 `_approval_use(store, applied, RATIO, ...)` 之所以能在沒有 RATIO 停下紀錄的情況下寫入,是測試輔助函式直接呼叫底層 `record_approval_use` 略過 `_gate`/`_await`,屬於人工建構的邊界情境,不是可達的生產路徑,因此判定非缺陷。

## F1 [S362] 要求的測試檔位置與實作不符
severity: minor
blocking: 否 — 屬文件/落點一致性問題,不影響行為、合約或資料正確性,只是規格文字未跟落點同步更新

合約 [S362] 明寫「這條隨增量 3 實作交付,測試寫在增量 3 的核可測試檔」(`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md:408`,此行不在本次凍結 patch 改動範圍內,查證方式見下)。增量 3 的核可測試檔是 `tests/executor/test_approval.py`(內含 `test_an_approvable_block_waits_for_approval` 等 [S362] 鄰近合約引用的測試)。但本次凍結 patch 把新測試加在 `tests/executor/test_observability.py`,凍結 patch 原文:

引句:「# ---- [S362] 查詢三 人工核可 ----」

這行連同其後的 `test_approval_counts_cover_waiting_and_applied` 都是新增在 `test_observability.py`,不是 S362 文字指定的「增量 3 的核可測試檔」。計劃增量 4 節新增的「查詢三」小節(本次 diff 內)只交代了函式落點的偏離(「查詢三不放增量 3 的核可模組...放 Systems/可觀測查詢」),沒有一併提到「測試落點也偏離 [S362] 原文」這件事,也沒有更新或註記 S362 那行的「測試寫在增量 3 的核可測試檔」字樣使其與現況一致——這是計劃內部沒打架但跟既有合約文字打架的落點問題,不是程式行為錯誤。

file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md:408`
file: `tests/executor/test_approval.py`(S362 原文指定的落點,此檔未新增任何 approval_counts 相關測試)

其餘核對:`Systems/可觀測查詢.md`(引句:「含增量 3 合進 main 後補上的查詢三(人工核可:待核可數、接不到停下紀錄的待核可數、已核可放行數)」)與 `Systems/提案收件口.md`(引句:「查詢三的兩個只讀方法 `awaiting_count`、`approval_use_count`」)兩篇筆記對函式名稱、四欄範圍、去重與未知租戶份數的描述都跟程式一致,計劃狀態行「查詢三以外已實作」與「查詢三...已實作」的描述也跟現況一致,沒有發現其他對不上的地方。
