severity: minor

### 1. approval.py 匯入範圍超出設計凍結的三項,悄悄多依賴收件口模組

severity: minor
blocking: 否 純結構性依賴偏移,不影響行為、不破壞合約,也沒有實際循環匯入
引句:「from rtb.executor.inbox_store import APPROVABLE, BlockCode」

`src/rtb/executor/approval.py`(核可模組)是本增量新開的檔,設計第 4 版明寫這支模組「只匯入護欄模組、提案領域模組與簽發器的租戶型別」,同一句話也原封不動抄進 `寫入能力憑證.md` 知識節點,兩處都把匯入清單釘死在三項。但實作除了 `guardrails`、`domain.proposal`、`capability_signer.Tenant` 之外,還多匯入了 `inbox_store` 的 `APPROVABLE`、`BlockCode`(給 `Approval.stage` 欄位與 `holds()` 判斷用)。這條依賴目前沒有反向匯入、不構成循環匯入(`inbox_store.py` 沒有匯入 `approval`/`guardrails`/`capability_signer`),所以不影響現在的行為;但它讓「寫入能力憑證」這個系統節點多背了一條指向「提案收件口」系統節點的依賴,跟凍結設計、跟知識圖譜兩處都寫的清單對不上,屬於實作偷偷擴大了經過三輪架構對齊審查釘住的邊界。
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md:282`
file: `docs/rtb-production-agent-demo-knowledge/Systems/寫入能力憑證.md`(「這支模組只匯入護欄常數、提案領域與簽發器的租戶型別」一句)
file: `src/rtb/executor/approval.py:24`

### 2. 「這份提案的廣告屬於哪個租戶」同一段邏輯被獨立寫了三次

severity: minor
blocking: 否 三處邏輯目前完全一致,沒有造成行為分岔;是維護期會走偏的重複,不是現在就錯的行為
引句:「tenant = next((t for t in tenants if proposal.campaign_id in t.campaigns), None)」

`rtb.executor.capability_signer.CapabilitySigner._tenant_of` 早就有這段「依廣告在哪個租戶的清單裡找租戶」的查找。設計文字只明講管理工具(`approve.py`)可以「讀同一份設定檔找到租戶再算」(不必依賴簽發器),但這次一併把一模一樣的一行邏輯又寫進了 `execution.py` 新增的 `_settle_awaiting`。三個檔各自一份完全相同的查找運算式(`capability_signer.py:162`、`execution.py:519`、`approve.py:73`),沒有共用成一支公開函式;將來若租戶查找規則改變(計劃裡本來就留著「按租戶隔離命名空間」的回頭條件),三處要同步改,容易漏改一處而三邊語意悄悄分裂。
file: `src/rtb/executor/capability_signer.py:162`(既有,未在本次 diff 改動,僅供比對)
file: `src/rtb/executor/execution.py:519`
file: `src/rtb/executor/approve.py:73`

### 3. `find_proposal` 沒有照同一節「各自一個交易」的慣例開交易、也不轉譯忙碌例外

severity: minor
blocking: 否 SQLite WAL 模式下單純 SELECT 不會被寫入鎖擋住,目前沒有觀察到的實際故障路徑
引句:「管理工具要簽核可的那一份提案(不論處置);讀不回來或沒有回 None。」

`inbox_store.py` 整支模組的既有慣例是:所有讀寫要嘛透過 `transaction()` 拿到的 `tx` 並呼叫 `self._own(tx)`(取件、對帳等共用路徑),要嘛像 `receive()`、以及本次新增的 `add_approval` 一樣自己用 `with immediate_transaction(self._conn):` 包一層、把 `DatabaseBusy` 轉成 `InboxBusy` 讓呼叫端重試。本次新增、緊接在 `find_proposal` 正下方、同樣標在「# ---- 核可管理工具用:各自一個交易 ----」這個小節底下的 `add_approval` 就是照這個慣例寫的;但 `find_proposal` 本身既不接 `tx`、也不開 `immediate_transaction`,是這支檔案裡唯一一支完全裸讀、不遵守模組自己宣告的兩種交易慣例之一的方法,跟同一小節、同一段落標題的說法對不上。
file: `src/rtb/executor/inbox_store.py:798`(`find_proposal`,裸讀)
file: `src/rtb/executor/inbox_store.py:805`(`add_approval`,同小節但正確包了 `immediate_transaction`)
