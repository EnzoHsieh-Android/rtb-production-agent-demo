severity: clean

已針對「人工核可憑證」全份 `r2-src.patch`(含第 1 輪修正,對照 `r2-delta.patch` 逐項複核折入內容)站在攻擊者角度重新推導,不是照抄第 1 輪資安席的結論:

- **偽造/重放/挪用**:核可與能力憑證雖共用 `capabilitykit.encode/decode`(HMAC-SHA256、`hmac.compare_digest` 常數時間比對、`MIN_KEY_BYTES=32`),但金鑰分離(`RTB_CAPABILITY_KEY` 與 `RTB_APPROVAL_KEY` 不同環境變數);兩種憑證聲明欄位集合不同(核可多 `revision`/`max_increase`/`fingerprint` 等),就算兩把金鑰誤設成同一把,`read()` 的欄位型別檢查也會讓另一種憑證解不出來,不構成跨用途混淆。`holds()` 同時綁 `task_id`、`revision`、`content_hash`(涵蓋 `campaign_id`、`requested_change`、`campaign_version_observed`、`decision_expires_at`、`policy_version` 全欄位,見 `Proposal.to_primitives()`)、`stage`、`scope_fingerprint`、`policy_version`、到期、金額上限,核可無法跨提案、跨版本、跨關卡或超額重放。
- **多核可/並行工作者讓超額寫入進 DSP**:`_gate`/`_approvals` 對是否需要核可只是「預判」(在自己的短交易內讀),真正放行仍在 `attempt_store.begin()` 的交易裡對 `aggregate_used` 重新現算;`reservation.approved` 只有在 `_take` 交易當下 `held`(且未過期)裡確實含該關卡的核可才為真。手動推演兩個工作者同時處理同租戶兩筆提案:預判階段都可能各自判定「用不到核可」,但 SQLite 的 `immediate_transaction` 序列化寫入,後寫入的一筆在自己的 `begin()` 交易裡會讀到前一筆已提交的用量,沒有核可時仍會正確丟 `AggregateLimitReached` 轉待核可,不會放行超額寫入。
- **憑證活得比核可久**:`_gate`/`_resign` 一律用 `min(held 核可 expires_at)` 當 `not_after` 封頂重簽,簽出的 `exp` 是固定值,不會因為之後 `_take` 讀到更新的時鐘而延長;比例核可在取件後過期會在 `_take` 內明確查驗並退回待核可,總曝險核可過期則讓 `reservation.approved` 自然變假、由 `begin()` 的例外路徑轉待核可。
- **反覆讓提案空轉**:`_superseded`(比對 `approval_id`)只有在核可表真的多寫入一列新核可(需要 `RTB_APPROVAL_KEY`)時才會觸發放租約重判;本輪新增的 `add_approval` 去重(`INSERT OR IGNORE`)正是防止管理工具重跑同一組參數產生新 `seq` 而誤觸發這條路徑的手段,經 `test_running_the_approval_tool_twice_in_the_same_second_is_harmless` 驗證。沒有金鑰的一方無法觸發這個放租約分支。
- 另檢查 `approve.py` 的參數面(`--stage` 受 `argparse choices`、`--approver` 經 `is_id` 驗證、`--task-id`/`--revision` 一律走參數化查詢),沒有注入面。

本機重跑 `tests/executor/test_approval.py`(52 項)全綠,未修改 repo 任何檔案,實驗僅在唯讀分析與既有測試上進行。
