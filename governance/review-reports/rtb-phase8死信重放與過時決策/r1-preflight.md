# Phase 8 死信重放與過時決策計劃 — 設計審前機械前掃

材料：`/Users/enzo/rtb-3b/docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase8死信重放與過時決策_計劃.md`
背景：`/Users/enzo/Downloads/RTB_PRODUCTION_AGENT_DEMO_HANDOFF.md`（第 9、13、16-F6、17-Phase8 節）
程式：`/Users/enzo/rtb-3b/src/rtb/`（分支 phase8-design）

---

## ① 未定義的詞

**結論：找到 1 處真正沒解釋的術語，其餘用到的詞都能在計劃內或程式/既有筆記追到出處，不算未定義。**

| 命中 | 計劃原句 | 依據 | 建議改法 |
|---|---|---|---|
| 「簽發」從未解釋 | 合約 [S503]：「執行端應照一般流程重跑執行前檢查、**簽發**、總曝險與核可」；「待使用者裁定」節同樣沿用這個詞 | `src/rtb/executor/capability_signer.py`（簽發能力憑證）是這個詞的實作，但計劃全文沒有一句解釋「簽發」指的是簽發哪個東西、跟核可/總曝險的關卡順序關係 | 在「最小設計」或「現況」加一句：「簽發＝執行前檢查通過後由 `capability_signer` 簽出的能力憑證，含到期時間、寫入後版本」，或直接連到 `[[Systems/執行迴圈]]` 裡定義簽發的段落 |

其餘檢查過但**不算命中**（有出處或計劃內已解釋，供覆核）：
- 「死信信封」「死信操作稽核」：計劃 1、6 節自己下定義（欄位、只增不改）。
- 「冪等鍵」：計劃 1 節解釋「鍵由提案內容算出」，並在 `domain/attempt.py::operation_key` 有實作可查。
- 「租約」：雖未在計劃內重新解釋，但只出現在「放回待處理」那句的附帶說明，且是 `Systems/提案收件口` 既有詞彙，屬於「程式碼答得出來、筆記已經記過」的類別，不算未定義。
- 「追蹤關聯」：計劃自己給了操作型定義（「冪等鍵就是追蹤關聯」），但這個定義跟交接文件 §14.1 的 `trace_id` 語意有落差，這點記在③而非①（不是沒解釋，是解釋跟另一份文件對不上）。

---

## ② 壞引用

**結論：逐一核對計劃提到的檔案、常數、合約編號與 Phase 5 引用，全部存在且內容與計劃描述相符，沒有壞引用；S500–S510 跟全庫既有合約沒有撞號。**

| 命中 | 計劃原句 | 依據 (file:行號) | 判定 |
|---|---|---|---|
| — | `rtb.domain.proposal.POLICY_VERSION` | 任務背景已排除（增量 3 才會搬過去，現在在 `src/rtb/analyzer/policy.py:27`） | 依指示不算壞引用，已確認現況常數位置正確 |
| 合約撞號檢查 | 「合約 S500–S510 有沒有跟全庫既有合約撞號」 | `grep -rn "S50[0-9]\|S51[0-9]" docs/` 除本計劃檔外無其他命中 | 無撞號 |
| Phase 5 [S304] | 「分析端收到死信就把任務轉擋下、不重新規劃(Phase 5 [S304])」 | `docs/.../RTB_Phase5樂觀鎖與重新規劃_計劃.md:168`：「當提案因其他原因被擋下、進了死信、或收件口永久拒收，推進函式應把任務轉擋下並記下原因，不建接續任務」；程式 `src/rtb/analyzer/flow.py:287-339`（`_CLOSED_WITHOUT_REPLAN` 含 `dead_letter`，落到 `_closed`） | 內容與程式行為一致 |
| Phase 5 [S317] | 「收件表清掉後 DSP 查不到就重新規劃(Phase 5 [S317])」 | `docs/.../RTB_Phase5樂觀鎖與重新規劃_計劃.md:163`；程式 `src/rtb/analyzer/flow.py:305-306,342-354`（`_from_dsp` 對 `_PURGED_CODES` 走 `ReplanReason.AFTER_RETENTION`） | 內容與程式行為一致 |
| Verification 連結 | `[[Verification/事故F6_死信重放必須重新驗證]]`（最遲 2027-01-31） | `docs/.../Verification/事故F6_死信重放必須重新驗證.md` 存在，`due: 2027-01-31` 相符 | 存在且日期一致 |
| lands_in 三篇 Systems | `Systems/提案收件口`、`Systems/執行迴圈`、`Systems/分析行程流程與檢查點` | `docs/.../Systems/` 下三檔都存在 | 存在 |
| `MAX_DELIVERIES`、`_purge_finished_tasks` | 現況節 | `src/rtb/executor/inbox_store.py:47`、`:460-472` | 存在，行為與計劃描述相符（見④） |
| 增量 3「核可工具」命令列、政策版本共用常數 | 「比照增量 3 核可工具」「政策版本改共用常數」 | `docs/.../RTB_Phase6權限護欄與總曝險_計劃.md:286`（命令列管理工具寫法）、`:372`（「政策版本常數搬進提案領域模組」）、`:383`（增量 3 r3「政策版本改成兩邊共用的程式常數」） | 引用內容與 Phase 6 計劃一致 |
| `resolve`（轉人工） | 「轉人工的嘗試留在人工那條路(resolve)，不進死信」 | `src/rtb/executor/attempt_store.py:496 def resolve(...)` | 函式存在 |

---

## ③ 範圍自相矛盾

**結論：計劃內部與「不做」小節之間、跟使用者裁定之間沒有矛盾；但跟交接文件 Phase 8 完成條件有一處沒被計劃明講的落差（trace linkage），建議在計劃裡補一句說明而不是留給審查猜。**

| 命中 | 計劃原句 | 依據 | 建議改法 |
|---|---|---|---|
| Trace linkage 沒被計劃提及 | 計劃「這份計劃在解決什麼」節自述完成條件是「重放保留原本的追蹤關聯並建立新的嘗試」；全文只用「冪等鍵」接信封與新嘗試 | 交接文件第 17 節 Phase 8 Definition of Done：「replay 保留原 trace linkage 並建立新 attempt／span」（`RTB_PRODUCTION_AGENT_DEMO_HANDOFF.md:680`）；交接文件 §14.1 的 `trace_id` 是獨立於 idempotency_key 的 correlation field（`:394`）；程式庫目前完全沒有 `trace_id`（`grep -rl trace_id src/rtb/` 零命中，Phase 9 可觀測性還沒做） | 計劃已經用「冪等鍵＝追蹤關聯」這個等價，但沒有寫出「為什麼可以用冪等鍵頂交接文件講的 trace/span」——這是程式碼答不出來、只有計劃作者知道的取捨。建議在計劃裡加一句：「Phase 9 之前系統沒有獨立的 trace_id/span，交接文件第 17 節講的『trace linkage』在本階段就是冪等鍵這條線；等 Phase 9 補上 trace 之後兩者要能對得起來」，把這個代換明講出來，不要讓審查誤以為漏掉了 DoD 的一項 |

其餘檢查過、**沒有命中**矛盾（供覆核，說明比對過的範圍）：
- 題 2 選 a（轉人工不進死信）跟「不做」節第一條「不讓轉人工的嘗試進死信，也不做它的重放」一致，沒有打架。
- 題 1 選 a（同一份提案重跑完整檢查）跟合約 [S503] 「一關都不略過」、跟「回退」節「重放沒有任何捷徑」一致。
- 「不做」節「不做自動重放、排程重放、批次重放」跟「最小設計」第 3 節「一支管理指令...輸入任務、修訂、操作人」（逐筆）一致，沒有暗自做成批次。
- 政策版本裁定「實作等增量 3 合進 main 之後才開始」跟落點節、合約 [S504] 的先後關係沒有矛盾——計劃本身沒有宣稱現在就能實作 S504。
- 「不做死信的告警(Phase 9)」跟第 6 節「死信操作稽核」是兩件事（稽核記錄 vs. 告警通知），沒有互相取消。
- 交接文件 DoD「permanent 與 transient failure 分類有測試」對應計劃 [S501]；「DLQ 操作有 audit」對應 [S508]；「stale decision 被 BLOCK 或 re-plan，不能直接寫入」對應 [S504][S505][S507]——三項都有對應合約，沒有漏接。

---

## ④ 機械宣稱驗語意

逐句對照程式碼實際行為，「屬實」表示計劃描述與程式行為一致（含數值、順序、觸發條件）；「部分」表示大方向對但有需要澄清的細節；「不屬實」表示與程式不符。

| 計劃宣稱（逐字節錄） | 核對結果 | 依據 (file:行號) |
|---|---|---|
| 「死信只有一種:收件口模組取件時,沒有嘗試紀錄的提案投遞次數滿 MAX_DELIVERIES(5,暫用)就寫處置「死信」、死信原因「投遞次數用完」」 | 屬實 | `src/rtb/executor/inbox_store.py:47`（`MAX_DELIVERIES = 5  # 暫用`）、`:62-66`（`DeadLetterReason` 只有 `DELIVERY_LIMIT` 一個成員）、`:517-527`（`receive()`：先查 `existing = attempt_store.latest(...)`，`existing is None` 才判 `deliveries >= MAX_DELIVERIES` 寫死信） |
| 「另記最後一次卡在哪一步(讀不到 DSP、全表未結案已滿、工作者沒回報)」 | 屬實 | `src/rtb/executor/inbox_store.py:68-76`：`LastFailure` 列舉恰好三個成員 `DSP_UNAVAILABLE`（讀不到 DSP 現況）、`TABLE_FULL`（全表未結案已滿）、`NO_REPORT`（租約過期被回收：工作者當機或沒回報），順序與計劃描述一致 |
| 「死信是終點,沒有任何重放路徑」 | 屬實 | 全庫 `grep -rn "replay\|重放" src/rtb/` 對 `inbox_store.py`/`execution.py`/`flow.py` 無命中（僅計劃文件與交接文件提到「重放」），`Disposition.DEAD_LETTER` 註解「不再交出去」(`:59`)，`receive()` 邏輯裡死信列直接 `continue`，沒有任何把死信放回待處理的路徑 |
| 「收件口模組 `_purge_finished_tasks` 會把「沒有待處理也沒有處理中、最後一次收件超過保留期(2 小時)」的任務整個清掉,死信也在其中」 | 屬實 | `src/rtb/executor/inbox_store.py:43`（`RETENTION = timedelta(hours=2)`）、`:460-472`（`_purge_finished_tasks`：`DELETE ... GROUP BY task_id HAVING SUM({OPEN}) = 0 AND MAX(received_at) < ?`；`OPEN` 只涵蓋 `PENDING`／`IN_PROGRESS`，`Disposition.DEAD_LETTER` 不在 `OPEN` 定義內，所以死信任務只要超過保留期就會被整批清掉，含信封所在的那一列） |
| 「永久與暫時失敗沒有寫明的分類:擋下(業務上不成立)實際上就是永久失敗,死信(暫時失敗但次數用完)實際上就是暫時失敗,但程式與測試沒有這層分類」 | 屬實 | `src/rtb/executor/inbox_store.py:50-94`：`Disposition`、`BlockCode`、`LastFailure` 三個列舉都只有值本身的註解，沒有任何欄位、函式或測試把它們標成「永久/暫時」二元分類；`grep -rn "permanent\|transient\|永久\|暫時" src/rtb/executor/` 除計劃引用的中文註解外沒有分類邏輯 |
| 「執行端不驗政策版本(提案有 policy_version,執行端只記在能力憑證上給稽核)」 | 屬實 | `src/rtb/executor/capability_signer.py:168`：`"policy_version": proposal.policy_version` 只出現在簽出的憑證欄位裡；`grep -n "policy_version" src/rtb/executor/execution.py` 零命中，執行前檢查（`execution.py`）沒有任何比對 `policy_version` 的邏輯 |
| 「也不驗證據新鮮度(證據在分析端;執行端只看得到決策建立時間與到期時間)」 | 屬實 | `src/rtb/executor/execution.py:396,402,456`：執行端只用 `proposal.decision_expires_at` 判斷是否過期，`Proposal` 的欄位裡沒有證據年齡，執行端邏輯裡也沒有讀取或計算 evidence age |
| 「分析端規則:證據超過 15 分鐘就重新蒐證(MAX_EVIDENCE_AGE)」 | 屬實 | `src/rtb/analyzer/policy.py:32`：`MAX_EVIDENCE_AGE = timedelta(minutes=15)`，並在 `_all_fresh()`（`:45-53`）用於 `check_freshness` 的 `max_age_seconds` |
| 「決策有效期 30 分鐘(DECISION_LIFETIME)」 | 屬實 | `src/rtb/analyzer/policy.py:31`：`DECISION_LIFETIME = timedelta(minutes=30)`，用於 `decision_expires_at=now + DECISION_LIFETIME`（`:89`） |
| 「領域層上限:決策從建立到到期最多 1 小時(MAX_DECISION_LIFETIME)」 | 屬實 | `src/rtb/domain/proposal.py:32`：`MAX_DECISION_LIFETIME = timedelta(hours=1)`，並在 `:250` 的驗證裡 `if expires - created > MAX_DECISION_LIFETIME` 擋下 |
| 「分析端收到死信就把任務轉擋下、不重新規劃(Phase 5 [S304])」 | 屬實 | `src/rtb/analyzer/flow.py:287`（`_CLOSED_WITHOUT_REPLAN = frozenset({"blocked", "dead_letter"})`）、`:328-339`（`_from_inbox_answer`：`dead_letter` 不落入任何 `_replan` 分支，落到最後一行 `return _closed(...)`） |
| 「收件表清掉後 DSP 查不到就重新規劃(Phase 5 [S317])」 | 屬實 | `src/rtb/analyzer/flow.py:304-306`（`SubmitStale` 且原因屬於 `_PURGED_CODES` 時呼叫 `_from_dsp`）、`:342-354`（`_from_dsp`：DSP 查不到操作時 `return _replan(ReplanReason.AFTER_RETENTION, ...)`） |
| 「冪等鍵就是追蹤關聯:重放後開的嘗試用同一把鍵(鍵由提案內容算出)」 | 部分 | `src/rtb/domain/attempt.py:20-34`：`operation_key()` 確實由 `task_id`、`campaign_id`、`action_type`、`requested_change`、`campaign_version_observed` 五樣算出雜湊鍵，屬於「提案內容」的一個子集（不含 `reason_codes`、`evidence_refs`、`policy_version`、`decision_created/expires_at` 等其餘欄位）。計劃說「鍵由提案內容算出」在方向上正確，但把「提案內容」講得比實際涵蓋的五個欄位更寬，容易讓人以為改了 policy_version 之類欄位鍵也會變——實際上不會，這點在計劃裡沒有說明，建議把「提案內容」改成點名這五個欄位，或註明「不含政策版本與證據引用」 |
| 「進死信的提案一定還沒有嘗試紀錄(使用者裁定題 2 選 a)」 | 屬實 | `src/rtb/executor/inbox_store.py:517-527`：`receive()` 裡先 `existing = attempt_store.latest(tx, operation_key(proposal))`，只有 `existing is not None` 才走 `_settle_existing`（確認或放掉租約，`:534-545`，不進死信）；死信判斷（`:521-527`）只在 `existing is None` 的分支才會走到，所以死信的提案在該次判斷當下必然沒有嘗試紀錄 |

---

## 摘要

四項機械前掃跑完：程式碼引用與合約編號全部對得上、沒有撞號；找到 1 個沒解釋的詞「簽發」；有 1 處計劃沒明講但值得補一句的落差——計劃拿冪等鍵頂交接文件講的「trace linkage」，没解釋為什麼可以這樣代換；11 句機械宣稱逐句對照程式碼後 10 句完全屬實，1 句（冪等鍵＝提案內容算出）方向正確但「提案內容」範圍講得比實際涵蓋的五個欄位寬,建議設計審前補這三處。
