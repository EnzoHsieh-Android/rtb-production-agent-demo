severity: major

### 1. 範圍指紋要跟管理工具共用,但材料來源(比例上限三常數)只定義在 execution.py,跟既有 CLI 工具的輕量進口慣例分家
severity: major
blocking: 是(這段程式該放哪裡完全沒交代,字面實作會被迫在「管理工具拖進整個執行迴圈模組」與「比例常數各算一套」之間二選一,後者直接打壞這個設計自己要求的「兩邊不能各算一套」與核可範圍改變即失效的證據)
引句:「管理工具簽核可時用同一段程式算範圍指紋(讀同一份設定檔),不讓兩邊各算一套」

範圍指紋的雜湊材料包含「比例上限的三個常數」,而這三個常數(`MAX_INCREASE_NUMERATOR`、`MAX_INCREASE_DENOMINATOR`、`MIN_INCREASE_STEP`)是 execution.py 模組層變數(已在 main,屬增量 2)。
file: `/Users/enzo/rtb-production-agent-demo/src/rtb/executor/execution.py:288-292`

execution.py 是很重的執行迴圈模組,匯入 `DspPort`、`Signer` 協定、唯一一份 DSP 回應對照表 `RESPONSE_TABLE`/`VOID_TABLE`、`Executor` 主體等一整套跟 DSP 溝通的狀態機。
file: `/Users/enzo/rtb-production-agent-demo/src/rtb/executor/execution.py:1,95-256`

既有三個命令列入口沒有一個互相匯入彼此的核心模組:`inbox_server.py` 只匯入 `domain.proposal`、`executor.inbox_store`、`httpkit`、`sqlitekit`,不碰 `execution.py`。
file: `/Users/enzo/rtb-production-agent-demo/src/rtb/executor/inbox_server.py:9-42`

`dsp/server.py`(模擬 DSP 的啟動程式)同樣不匯入 `execution.py` 或 `capability_signer.py`,走自己的 `rtb.dsp.capability` 模組。
file: `/Users/enzo/rtb-production-agent-demo/src/rtb/dsp/server.py:10-33`

唯一匯入 `execution.py` 的命令列入口是 `runner.py`,而它就是執行迴圈本身,匯入是必要的;它同時也示範「命令列工具只拿自己需要的東西」這個既有慣例的邊界在哪。
file: `/Users/enzo/rtb-production-agent-demo/src/rtb/executor/runner.py:30-35`

輸入 → 預期:實作者要寫「管理工具算範圍指紋」這段程式時,若不想讓一支純命令列的核可簽發工具拖進整個 DSP 讀寫狀態機,唯一的替代路是把三個比例常數搬出 execution.py 給管理工具與執行端共用匯入——但這正是要動到已合併進 main 的增量 2 程式碼落點,設計裡完全沒提;若實作者圖省事在管理工具裡另外寫死 1/2/1 三個常數(不共用),則管理工具與執行端「各算一套」,跟這節本身明講的要求相反,且會讓「比例上限一改,舊核可就該失效」這件事在兩邊算出不同指紋而永遠測不出改壞(F7 交接文件要求的「核可範圍改變時失效」證據因此不成立)。

### 2. 設定檔新增頂層「目前政策版本」欄位,沒有比照既有逐欄「缺值 vs 值不合法」雙軌驗證慣例,也沒有交代解析函式怎麼把它傳出來
severity: major
blocking: 是(漏掉該有的驗證合約,字面實作下一個型別錯誤或格式錯誤的頂層欄位值會被解析函式直接忽略而不是照專案慣例整份停機)
引句:「設定檔頂層新欄位,分析端部署新政策時要同步改」

現有 `_parse_tenants` 只讀 `raw.get("tenants")`,對這個字典以外的任何頂層鍵完全沒有讀取或驗證邏輯;新增的「目前政策版本」頂層欄位若照字面塞進同一份設定檔,現有解析函式會原樣忽略它(不管值合不合法),不會像其他欄位一樣被驗證。
file: `/Users/enzo/rtb-production-agent-demo/src/rtb/executor/capability_signer.py:93-95`

對照既有慣例:租戶層級的 `aggregate_limit` 欄位明確區分「缺欄當 0」與「值不合法(非整數、負數、超過上限)就整份設定檔不合法、停機」兩種情況,並各自有處理分支。
file: `/Users/enzo/rtb-production-agent-demo/src/rtb/executor/capability_signer.py:104-106`

`load_tenants` 的回傳型別是 `tuple[Tenant, ...]`,沒有任何管道可以把一個跟租戶無關的頂層值(政策版本)傳給呼叫端(執行端要組指紋、要比對 `S375` 的「跟提案政策版本是否相同」),設計完全沒說這個新回傳值要加在哪個函式、哪個型別上。
file: `/Users/enzo/rtb-production-agent-demo/src/rtb/executor/capability_signer.py:114-115`

輸入 → 預期:設定檔頂層寫 `"policy_version": 123`(整數而非字串)或漏寫、寫成空字串——設計只交代「沒宣告」這一種情況(核可不算數),沒有交代「值型別不對」比照既有慣例應該讓整份設定檔不合法、停機;字面實作極可能直接把非字串值原樣拿去跟 `Proposal.policy_version`(字串)比較,恆為不相等,而不是照全專案「打錯字跟沒設不一樣,不猜意思」的一貫作法在簽發時就攔下來停機。

### 3. 簽發器回傳的「租戶設定摘要」跟 Grant 既有的 tenant/aggregate_limit 兩個欄位重複,沒有交代單一事實來源
severity: minor
blocking: 否(不會造成字面上的錯誤行為,是跟既有「只補齊剛好需要的扁平欄位」慣例分家,屬架構乾淨度問題)
引句:「廣告清單、單一廣告上限、總額上限)——簽發器的回傳多帶一個」

`Tenant` dataclass 現有欄位已經是「名稱 + 三欄(campaigns、max_budget、aggregate_limit)」,而 `Grant` 在增量 1 就已經把其中兩項(tenant 名稱、aggregate_limit)攤平帶出。
file: `/Users/enzo/rtb-production-agent-demo/src/rtb/executor/capability_signer.py:38-53`

`grant()` 目前回傳 `Grant(token, tenant.name, tenant.aggregate_limit)`,丟掉了 `campaigns`/`max_budget`。
file: `/Users/enzo/rtb-production-agent-demo/src/rtb/executor/capability_signer.py:140-142`

增量 3 要求 Grant 再多帶一個「租戶設定摘要」(名稱與三欄),等於同一個 Grant 裡會同時有攤平的 `tenant`/`aggregate_limit` 與巢狀摘要裡重複的同名資料,卻沒有講清楚兩者是否保證來自同一次讀檔、要不要乾脆用既有的 `Tenant` 物件取代這兩個攤平欄位——跟增量 1 當初「比照憑證到期時間、寫入後版本的補欄位做法」建立的扁平化慣例不一致。
