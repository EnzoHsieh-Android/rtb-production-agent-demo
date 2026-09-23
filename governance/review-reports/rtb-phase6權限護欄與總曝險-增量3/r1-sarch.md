severity: major

### 1. 待核可提案沒有到期掃描機制,還會被既有兩小時保留期清除誤刪,S352 與交接文件「到期即失效」的完成條件落空

severity: major
blocking: 是,S352 沒有觸發路徑,且既有清除機制會直接刪掉待核可列,兩者合起來讓「到期即失效」這條完成條件在設計裡沒有證據能成立,屬於漏掉該有的合約、也會資料損壞(稽核紀錄被清空)
引句:「提案到期(最多 1 小時)還沒核可:轉成已擋下,擋下原因就是原本那一種,結案。」

說明:設計把「待核可」的到期轉擋下(S352)寫成被動語態,沒有講是哪一段程式去掃描、什麼時候跑。核對現有三條可能觸發時機都碰不到待核可列:
1. 「取件」(`receive()`)的 SQL 只選 `PENDING`(`disposition IS NULL`)或租約過期的 `IN_PROGRESS`,待核可(`disposition='awaiting_approval'`)兩者都不符,而且設計本身要求「取件不取它,除非已有有效核可」,所以 receive() 本來就不會碰到它。
2. `accept()` 每次收件都會「順手把到期的待處理標為已過期」,但那段 SQL 條件是 `{PENDING} AND expires_at <= ?`,`PENDING` 同樣要求 `disposition IS NULL`,待核可列不會被這段掃到。
3. `reconcile_all()` 只掃「未結案的嘗試」(`attempt_store.unresolved_keys`)與「處置是 IN_PROGRESS 但嘗試已到終點」兩種鍵;待核可提案照設計「不開嘗試」,所以既不在嘗試表裡,也不是 IN_PROGRESS,對帳掃不到它。

三條既有的「順手檢查」路徑都繞過了待核可,設計又沒有另外交代新的一步,S352 因此沒有實作依據。更嚴重的是:`_purge_finished_tasks` 用來判斷「這個任務是否還開著」的巨集 `OPEN` 只認 `PENDING` 或 `IN_PROGRESS`,待核可不算開著;若這份提案是該任務唯一一列,兩小時沒有新收件,`accept()` 的清除會直接把整個任務(含待核可那列)刪掉——不是「轉成已擋下」,是連紀錄一起消失。例:任務 t1 修訂 1 因總曝險已滿轉待核可,之後兩小時內都沒有人核可、也沒有 t1 的新修訂送進來,只要收件口收到「任何其他任務」的提案觸發一次 `accept()`,t1/1 就會被 `_purge_finished_tasks` 整列刪除,S352 要求的「轉成已擋下」從未發生,人工事後也查不到這份提案曾經待核可過(停下紀錄表雖然還在,但收件表那一列——包含它是否曾被核可——已經不見)。

file: `src/rtb/executor/inbox_store.py:135`(PENDING 定義)
file: `src/rtb/executor/inbox_store.py:138`(OPEN 定義只認 PENDING/IN_PROGRESS)
file: `src/rtb/executor/inbox_store.py:418`(順手標記已過期只掃 PENDING)
file: `src/rtb/executor/inbox_store.py:458`(_purge_finished_tasks 用 OPEN 判斷任務是否結束)
file: `src/rtb/executor/inbox_store.py:504`(receive() 的 SQL 只選 PENDING 或租約過期的 IN_PROGRESS)

### 2. 「取件時驗核可」沒有交代放在哪一層、誰持有核可金鑰、怎麼跟同一把寫入鎖交易接軌,跟既有「收件表不做授權」的分層規則衝突

severity: major
blocking: 是,設計沒有講清楚這一步落在哪個模組,若照字面把簽章驗證塞進收件表(InboxStore),會打壞現有「收件口只管安全地收、去重、記帳,不執行、不授權」的分層合約;若另外開一個步驟,設計完全沒描述它何時跑、算不算新的處理階段,也沒有回答並行下核可是否可能被兩個工作者同時消耗
引句:「的提案若有一張有效核可(簽章對、任務修訂與雜湊都對、沒過期),就照一般流程重新處理」

說明:「核可什麼時候生效」寫著「取件時」要判斷有沒有有效核可,再「照一般流程重新處理」。但現有 `InboxStore.receive()` 是一句 SQL 挑出候選列、立刻在同一個交易裡發收據,整段不碰任何金鑰;`InboxStore` 這支模組的職責明寫「不負責執行提案、呼叫 DSP、政策或租戶檢查」,金鑰只由 `CapabilitySigner`/`capabilitykit` 處理,而且 `capabilitykit.py` 的規則是「金鑰環境變數名稱只在共用模組出現一次,讀取函式只給啟動程式呼叫」。核可簽章驗證(HMAC 解碼、比對任務/修訂/內容雜湊、比對到期)屬於跟能力憑證同等級的授權判斷,不是收件表現在做的事。設計沒有回答:
- 是要把驗證邏輯搬進 `InboxStore.receive()`(打破它不碰金鑰、不做授權的既有邊界),還是在 `Executor` 另開一步(那「取件時」這個措辭就不準確,而且要決定這一步在 `process_one()` 的哪個位置跑、要不要佔用同一個立即取得寫入鎖的交易)。
- 如果核可驗證與「重新處理」不是原子的同一個交易,兩個工作者是否可能同時判定同一份待核可提案「有效核可」而各自往下走(核可使用表雖然有唯一鍵可以擋重複記帳,但那是稽核紀錄,不是併發控制;S336 那種「算已用額度+寫入必須同一個立即寫入鎖交易」的既有並行合約在這裡沒有對應版本)。

file: `src/rtb/executor/inbox_store.py:492`(receive() 定義,整段只做 SQL 挑選與租約簽發,不涉及任何金鑰或簽章)
file: `docs/rtb-production-agent-demo-knowledge/Systems/提案收件口.md:17`(RULE:「收件口是不可信內容進入有寫入能力的行程的入口,所以只做安全地收、去重、記帳,不執行、不授權」)
file: `src/rtb/capabilitykit.py:10`(「讀取函式只給啟動程式呼叫」)

### 3. 「核可表」(管理工具寫入的那張表)由誰建表、管理工具怎麼開啟同一個資料庫完全沒交代,跟既有「收件口模組是執行行程資料庫唯一開連線建表的地方」規則接不上

severity: major
blocking: 是,若管理工具照字面獨立開發、自己開一條 SQLite 連線建表,會繞過現有 `InboxStore` 對這個資料庫的唯一建表/遷移責任,有 schema 各自漂移、忙碌重試邏輯不一致的風險,屬於架構分家也可能導致資料損壞
引句:「由一支獨立的管理工具簽發並寫進執行行程資料庫的核可表(只增不改)」

說明:設計只講清楚「核可使用表」(執行端寫的稽核列)是「由收件口模組統一建表」,但對管理工具要寫入的「核可表」本身(核可憑證的存放處)只字未提由誰建表、放在哪個模組。現有架構裡,`InboxStore.__init__` 是整個執行行程資料庫唯一呼叫 `connect()` 建表的地方(`SCHEMA + attempt_store.SCHEMA` 合併後一次建立),連 `attempt_store` 自己定義的表結構字串都要靠 `InboxStore` 執行;`Systems/提案收件口.md` 明寫這支模組「同時是整個執行行程資料庫唯一開連線、開交易的地方」。管理工具若不透過 `InboxStore`(或至少透過 `rtb.sqlitekit.connect()`)開這個資料庫,就要自己決定要不要開 WAL、要不要處理 `SQLITE_BUSY`(執行迴圈正握著寫入鎖時)、核可表的 `CREATE TABLE` 何時跑——這些現有程式裡全部由 `connect()`/`InboxStore._migrate_columns()` 統一處理,設計完全沒有指向管理工具要重用哪一段。同時,管理工具的位置與啟動方式(它是獨立可執行檔,還是 `src/rtb/executor/` 底下另一支模組)也沒有對照既有的三個命令列入口(`runner.py`、`inbox_server.py`、`src/rtb/dsp/server.py`)——這三支都遵循同一種模式:各自模組內用 `argparse`、收 `--db`/設定路徑、經 `capabilitykit.read_key(os.environ)` 只在啟動程式讀金鑰、`main(argv)` 搭配非零結束代碼。設計對管理工具完全沒有比照這個既有模式,實作者很容易寫出一支遊走在既有規則之外的工具。

file: `src/rtb/executor/inbox_store.py:296`(`InboxStore.__init__` 用共用的 `connect()` 一次建立 `SCHEMA + attempt_store.SCHEMA`)
file: `docs/rtb-production-agent-demo-knowledge/Systems/提案收件口.md:26`(RULE:「收件口資料庫模組同時是整個執行行程資料庫唯一開連線、開交易的地方」)
file: `src/rtb/executor/runner.py:48`(既有命令列入口的 argparse 模式)
file: `src/rtb/executor/inbox_server.py:176`、`src/rtb/dsp/server.py:260`(另外兩個既有命令列入口,同一種模式)

### 4. 核可金鑰的讀取方式沒有交代如何跟 capabilitykit 現有「單一金鑰常數+分析端原始碼掃描」的保護模式接軌

severity: major
blocking: 是,若新金鑰名稱沒有比照既有模式接進分析端的原始碼掃描測試,S357「分析行程應讀不到核可金鑰」這條合約字面上可以用一支跟既有防護脫鉤的弱測試打勾,實質保護會比現有的簽發金鑰弱,屬於漏掉該有的合約
引句:「用一把跟簽發金鑰分開的核可金鑰簽(環境變數另一個名字);分析行程的邊界測試同樣禁止讀這把金鑰。」

說明:現有 `capabilitykit.py` 對「金鑰怎麼讀」只支援一把鑰匙的模式:`KEY_ENV = "RTB_CAPABILITY_KEY"` 是模組層級唯一常數(註解明寫「程式裡唯一出現這個名稱的地方(有測試擋)」),`read_key(environ)` 內部寫死讀這一個名字,而真正擋住分析行程的測試(`tests/analyzer/test_boundaries.py::test_the_analyzer_can_neither_read_the_signing_key_nor_import_the_signer`,對應 S30)是直接 `from rtb.capabilitykit import KEY_ENV` 之後用 AST 掃描分析端原始碼裡有沒有出現這個字串常數,不是靠某個執行期檢查。核可金鑰要用「環境變數另一個名字」,設計沒有講:這個新常數要定義在哪裡(比照放進 `capabilitykit.py` 跟 `KEY_ENV` 並列?還是散落在別處,例如管理工具或 `capability_signer.py` 裡臨時字串)、`read_key` 要不要改成可指定金鑰名稱的通用函式、以及最關鍵的——既有 S30 測試要不要跟著擴充成同時掃描兩個常數。如果實作者沒有意識到要去動 `test_boundaries.py` 裡那支既有測試,S357 完全可能用一支全新、獨立、覆蓋面比 S30 弱的測試滿足(例如只斷言某個函式呼叫不出現,而不是掃描整個分析端套件的原始碼與相對匯入),核可金鑰因此得到的保護會明顯弱於簽發金鑰現在享有的保護。

file: `src/rtb/capabilitykit.py:22`(`KEY_ENV` 常數與「程式裡唯一出現這個名稱的地方」的註解)
file: `src/rtb/capabilitykit.py:47`(`read_key` 寫死讀 `KEY_ENV` 這一個名字)
file: `tests/analyzer/test_boundaries.py:127`(S30 測試:`from rtb.capabilitykit import KEY_ENV` 後對分析端原始碼做 AST 掃描)
