severity: clean

本次只判架構對齊:是否照既有模組邊界與做法走、有沒有引入專案裡原本沒有的第二種做法。逐點核對如下,均未發現 major(引入第二套機制或跨層直呼)。

## 重放管理指令 對照 增量 3 核可工具

引句:「一支管理指令(比照增量 3 核可工具,命令列,不是執行迴圈的一部分),輸入任務、修訂、操作人」

查證:`src/rtb/executor/approve.py` 是獨立 CLI 模組(argparse、`--db`/`--tenant-config`/…),只呼叫 `InboxStore` 的方法(`find_proposal`、`add_approval`)與 `rtb.executor.approval`/`capability_signer`,不匯入 `execution.py`(`approval.py` 檔頭註解明講「這支模組不匯入執行迴圈模組…管理工具不必拖進整個執行迴圈」)。快照的重放指令描述同一種形狀:命令列入口、輸入任務/修訂/操作人、寫進收件口既有表。依賴方向一致(CLI → InboxStore,不反向),沒有引入 HTTP/RPC 或另一套介面層。

簽章這一項兩者刻意不同,但查證後判斷是同一份威脅模型下的合理分工,不算引入第二套安全機制:核可要簽章是因為核可憑證要「跨行程、跨時間」被執行迴圈(另一個行程)之後獨立驗證(`approval.py` 的 `issue`/`read`/`holds`),所以需要可攜的憑證格式。重放是操作工具直接對同一個資料庫檔案做一次條件寫入,結果立即在同一個交易裡判定,沒有下游要事後驗證的憑證,所以不需要簽章。查證:`src/rtb/executor/approval.py` 1-13 行檔頭註解、`src/rtb/executor/approve.py` 全檔。

## 放回待處理 對照 既有 settle_awaiting(RELEASED) 與 release

引句:「同一個交易裡把那一列放回待處理(處置清空、投遞次數歸零、租約清空),寫一列稽核」

查證:`src/rtb/executor/inbox_store.py:757-758` 的 `AwaitingOutcome.RELEASED` 分支正是 `"disposition = NULL, block_code = NULL, deliveries = 0, lease_until = NULL, lease_owner = NULL"`,字面上就是快照描述的「處置清空、投遞次數歸零、租約清空」。快照沒有明說重放要不要直接重用 `settle_awaiting`/`AwaitingOutcome`,但這兩者的觸發條件(`AWAITING` 巨集,即 `disposition = 'awaiting_approval'`)跟死信重放要的條件(`disposition = 'dead_letter'`)不同,不能直接套同一個方法簽名;比照風格應是在 `InboxStore` 新開一支同構的方法(條件內嵌在 `UPDATE ... WHERE` 裡、回 rowcount 判定的單列有條件寫入),這正是 `release`/`ack_blocked`/`await_approval`/`settle_awaiting`/`ack_expired` 這一整組方法共用的既有慣用法,不是另立一套。快照第 3 節同時要求待處理上限滿了「照收件口既有規則拒絕」,對應到 `_check_capacity`(inbox_store.py:513-519),屬同一類別內的私有方法呼叫,不是跨層直呼。

原子性上,重放的「條件檢查 + 放回待處理 + 寫稽核」要求在同一個交易裡完成(S502),比 `approve.py` 現有的「`find_proposal` 與 `add_approval` 各自一個 `immediate_transaction`」更嚴格;`InboxStore.transaction()`(inbox_store.py:434-448)本來就是給收件口之外的呼叫端開單一寫入交易用的既有入口(執行迴圈已在用),重放指令用它來包住整段條件寫入,沒有另開新的交易機制。

## 信封表、稽核表 對照 既有 write_stops(停下紀錄表)、approval_uses(核可使用表)

引句:「收件口模組新增一張只增不改、不被清理的「死信信封」表」

查證:`write_stops`(inbox_store.py:167-175)本身就是「只增不改」的停下紀錄表,`approvals`/`approval_uses`(176-187)是增量 3 沿用同一張資料庫檔案、用 `CREATE TABLE IF NOT EXISTS` 在既有 `SCHEMA` 字串裡新增的表,舊資料庫開啟時會自動補上(同一機制,不需要另外的遷移框架)。快照的信封表、稽核表落在同一份 `SCHEMA`、同一個 `InboxStore` 類別(落點段:「信封表、稽核表…寫進 [[Systems/提案收件口]]」),是既有「同檔案內只增不改的稽核/紀錄表」模式的第三、四張,不是另開一個資料庫或另一套持久層。[S510] 要求舊收件表開啟時補上信封表、稽核表與新擋下原因,對應到既有 `_proposals_outdated`/`_migrate_columns`/`_rebuild_proposals` 那一套「開啟時比對允許值清單、缺就重建」的既有遷移路徑(inbox_store.py:385-430),寫法上是同一條路,不是新流程。

## 新擋下原因 對照 既有 BlockCode 的加法與分析端對應

引句:「政策版本:提案的政策版本不等於現行版本…就擋下,原因「政策已變」」「決策新鮮度:現在減決策建立時間超過 15 分鐘…就擋下」

查證:`BlockCode`(inbox_store.py:85-99)檔頭註解明講「加成員之後,舊收件表會在開啟時照允許值清單逐一比對…成員一旦用過就不能拿掉」,這正是既有加法路徑,快照兩個新原因（`policy_version_changed`、`decision_stale`）照這條路徑加,行為一致。

分析端對應:`src/rtb/analyzer/flow.py:291-340` 已有「`_BLOCK_CODES` 允許清單 + `_from_inbox_answer` 逐一分派」這一套(白名單防注入,見 293 行註解:「不在這份清單的代碼當成回應讀不懂,不寫進只增不改的歷史表」),`VERSION_CHANGED`/`EXPIRED` 都是照這個模式各自對應一個 `ReplanReason` 成員(`src/rtb/analyzer/task_store.py:93-97`)。快照第 5 節「新原因…→ 另開接續任務重新規劃(同 Phase 5 的版本已變、過期)」與「死信:改 Phase 5 [S304] 的死信那一半」都是在同一個 `_from_inbox_answer` 分派函式與同一個 `ReplanReason` 封閉列舉裡加分支/加成員,沒有另立第二套「決定要不要重新規劃」的邏輯或另一張對照表。硬規則檢查的位置(「排在版本已變之後…再簽發、再判比例上限」)也查證吻合 `execution.py:404`(`signed = precheck(proposal, view) or self._sign(proposal)  # 硬規則一律先判`)與 `_gate`(443 行起,先 `_approvals` 再判比例)的既有順序,兩項新檢查是往 `precheck()` 這一個既有函式裡加規則,不是另開一條檢查路徑。

## 失敗分類(暫時/永久)

「暫時失敗」對應既有 `LastFailure` 封閉列舉、「永久失敗」對應既有 `BlockCode` 封閉列舉的擋下原因,快照要求的「逐一列舉兩個列舉每個成員都有分類」是「封閉列舉 + 覆蓋測試」這個既有慣用法(`APPROVABLE = frozenset({...})`、`analyzer/flow.py` 的 `_BLOCK_CODES`)的同構延伸,不是新分類機制。

## 重放管理指令若另開一支模組的落點與依賴方向

快照落點段本身寫明「重放管理指令若另開一支模組,另開一篇 Systems 家」,把模組歸屬留成待決,但沒有因此含糊依賴方向:第 3 節已明講「比照增量 3 核可工具」,即命令列工具依賴 `InboxStore`(與視需要的 `capability_signer`/`guardrails` 供顯示用途),不依賴 `execution.py`;`execution.py` 也不會反向依賴這支重放工具。只要照這句話落地(比照 `approve.py` 放進 `rtb.executor` 套件、只呼叫收件口的方法),依賴方向與既有核可工具一致。這一點是文件歸屬未定,不是架構邊界或依賴方向本身有衝突。

## 未發現的 major

沒有發現「引入專案裡原本沒有的第二種做法」或「跨層直呼」:重放指令沿用核可工具的 CLI-over-InboxStore 形狀;放回待處理沿用收件口既有的條件式單列寫入慣用法;信封表、稽核表沿用停下紀錄表/核可使用表的「同檔案只增不改表」慣用法與既有 schema 遷移路徑;新擋下原因沿用 BlockCode 加法路徑與分析端既有白名單分派函式,均落在原模組邊界內。
