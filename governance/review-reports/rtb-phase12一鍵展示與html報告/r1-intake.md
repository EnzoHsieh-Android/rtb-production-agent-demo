preflight-4: ran

# r1 前掃留痕(2026-09-24)

前掃員:sonnet 一席(唯讀,四類固定清單);編排者另對驅動程式、流程圖、現況三段的機械宣稱逐句開檔重驗。
被掃版本:計劃第 2 版(提交 cdc63e5)。refcheck:0 個壞引用。壞引用②、範圍矛盾③:0 條。

## 語意類命中(修真檔,修改前 → 後)

| id | 類 | 查證 | 修改前 | 修改後 |
|---|---|---|---|---|
| p1 | ④ | src/rtb/executor/observability.py:128 aggregate_audit 是總曝險稽核明細(通過、被擋、剩餘),:109 approval_counts 是核可次數;都是彙總,沒有逐筆擋下紀錄 | 〈驅動程式〉「稽核查詢(擋下與核可)」;〈現況〉「稽核查詢(可觀測查詢)」 | 「稽核查詢(總曝險稽核明細與核可次數,F7 用)」;逐筆處置與擋下原因改寫成從追蹤檢視拿。〈現況〉同步寫明是彙總 |
| p2 | ④ | src/rtb/executor/inbox_store.py:131 LifecycleKind 六個成員(收件、取代、過期、取件、接手、放租約)沒有「終點」這個分類;程式裡沒有叫終點事件的列舉 | 〈流程圖〉與 [S1017]「生命週期的終點事件種類」 | 列出實際的封閉列舉:執行端 Disposition、BlockCode、DeadLetterReason、StopKind、LifecycleKind、ReplayOutcome,分析端 TaskState、ReplanReason;每個成員對到一條邊或一個節點(原本「邊或終點」) |
| p3 | ④ | tools/verify_claims.py 在 phase12-design(f183cd8 起)不存在,只在 Phase 11 的 phase11-inc1、phase11-inc2 分支 | 〈現況〉只寫「Phase 11 的驗證器」,〈拆增量〉增量 3 沒寫相依 | 〈現況〉補「只在 Phase 11 分支、還沒合進主線」;增量 3 標「Phase 11 驗證器合進主線之後」 |

## 未定義詞(直接修,不算 finding)

| id | 類 | 查證 | 修改前 | 修改後 |
|---|---|---|---|---|
| p4 | ① | 「Skill Contract」在圖譜與程式裡只出現在本計劃;交接文件不在 repo | 〈現況〉「有無 Skill Contract 的前後比較」 | 補括號說明:指 Phase 11 的證據清單加驗證器,照使用者裁定第 6 項讀成「有沒有機械驗證」 |

## 查過沒命中(前掃員與編排者)

- 九支命令列入口、分析端沒有正式啟動程式(src/rtb/analyzer/ 無 main;流程推進函式 src/rtb/analyzer/flow.py:156 advance)、租約(task_store.py task_leases)、正式 DSP 與收件口用戶端。
- 共用行程基礎伺服器外殼(src/rtb/httpkit.py:41-64 只綁回送、:177 主機標頭檢查、:235 只回 JSON)。
- F1–F7 組法與各端到端測試檔頭一致;F7 現為 3000 個廣告、8 個工作者;五份證據清單的證據涵蓋 F1–F7 端到端測試檔。
- 金鑰環境變數 RTB_CAPABILITY_KEY、RTB_APPROVAL_KEY、RTB_DSP_AUDIT_KEY(src/rtb/capabilitykit.py);核可命令列用 approval.issue 簽。
- 分析端證據新鮮度判斷存在(src/rtb/analyzer/policy.py:54)。
- Phase 11B 計劃的模型入口、即時三條件、1 美元與 20 美元上限、花費帳唯讀開法(modelledger_view)。
- 造假示範五種與 Phase 11 計劃一致;合約編號 S1000–S1025 不跟其他筆記衝突。

## 動到〈使用者裁定〉的

無。〈使用者裁定〉的 DAG 那條只寫「判斷點、分支條件」,「終點事件種類」出自設計與合約段,p2 只改那兩處。

## 收貨機械重現(編排者 Claude 查證的部分;協調者核對的另補)

| id | 指令或讀檔 | 輸出摘要 | 判定 |
|---|---|---|---|
| x4 | grep -n "class AttemptState\|class OutcomeCode" src/rtb/domain/attempt.py;grep -n "class Result" src/rtb/executor/execution.py;grep VoidOutcome | AttemptState 在 attempt.py:44、OutcomeCode 在 attempt.py:76、執行迴圈 Result 在 execution.py:310、VoidOutcome 在 execution.py:264;第 1 版 S1017 的清單都沒列 | HIT |
| f1 | sed -n 28,55p src/rtb/domain/task_state.py | _FLOW 裡 ANALYZING → COLLECTING_EVIDENCE、PROPOSED → COLLECTING_EVIDENCE 兩條往回走的轉換;第 1 版只提死信重放與接續任務 | HIT |
| a1 | grep -n "def reply\|application/json\|def _dispatch" src/rtb/httpkit.py | reply(:235)寫死 Content-Type application/json(:239);請求分派在 _dispatch(:149);沒有 HTML 出口 | HIT |
| k1 | grep -n "def issue" -A8 src/rtb/executor/approval.py | issue(key, proposal, stage, tenant, *, approver, max_increase, issued_at, expires_at);approver 要過 is_id(src/rtb/domain/_checks.py:49) | HIT |
| k6 | grep -n "MIN_KEY_BYTES" src/rtb/capabilitykit.py | MIN_KEY_BYTES = 32(:34) | HIT |
| q1 | Phase 11B 增量 1(rtb-11b-rev a5eeedb)src/rtb/modelclient.py:321 settings_from_env、:337 _live_refusal | 即時條件除即時開關、展示編號、claude 外,還有價目表期限、管理政策、實測紀錄([S942])、真家目錄隔離時的記憶內容 | HIT |
| q2 | 同上 settings_from_env 讀 RTB_MODEL_LIVE、RTB_MODEL、RTB_MODEL_RECORD | 白名單只給 PATH、HOME、LANG、USER 時三個變數都讀不到,入口一律判成錄製 | HIT |
| q3 | modelclient.py:826 _open(建目錄、第一次建表);src/rtb/eval/record.py:160 --ledger「只在錄製模式能用」 | 重播也記一筆 0 元帳;預設帳在 ~/.rtb/model-ledger.sqlite | HIT |
| q4 | src/rtb/modelledger_view.py:147 ModelLedgerView 只有 calls_between(:167);modelclient.py:869 used_so_far 按展示編號加總但走讀寫開法 | 唯讀開法沒有按展示編號查的讀法 | HIT |

## 協調者補充核對(2026-09-24)

| id | 重現 | 結果 |
|---|---|---|
| x5 | 外家-codex 引句「每個情境的判斷紀錄從追蹤檢視、生命週期事件與稽核組出來」機械錨不到;對照快照原句「判斷路徑:每個情境的判斷紀錄(節點、走的分支、判定結果、白話原因、時間)從追蹤檢視、生命週期事件與稽核組出來」,只省略括號內容,語意一致 | HIT 採信 |
| s3 | grep src/rtb/httpkit.py:41 `class KitServer(ThreadingHTTPServer)`,每個請求各自執行緒 | HIT 採信 |
| k6 | src/rtb/capabilitykit.py:34 MIN_KEY_BYTES = 32 | HIT 採信 |
