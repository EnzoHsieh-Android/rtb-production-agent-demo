severity: clean

比例上限放進執行前檢查(`precheck`),不放進簽發器,跟現況的分工吻合:`precheck(proposal, view)` 本來就是唯一看得到 DSP 現況(`CampaignView`)的地方,簽發器只看提案快照與租戶設定。`src/rtb/executor/capability_signer.py:9` 的模組說明寫死「憑證只證明『這筆寫入的確切值在授權範圍內』,不證明這是好的業務決策(那是執行前檢查)」,新規則是業務規則(相對現況的比例),放進 `precheck` 不需要改簽發器的簽章,呼叫端既有一行 `precheck(proposal, view) or self._sign(proposal)`(`src/rtb/executor/execution.py:362`)原封不動,新增判斷可以直接插進 `precheck` 函式本體,三條路徑(處理一筆、憑證過期後重讀、對帳重跑)全部共用同一支 `precheck`,不必在其他呼叫點各補一次。這跟快照裡的說法一致:

引句:「選執行前檢查。比例是「相對於現況」的業務規則,跟簽發器「租戶給的絕對上限」性質不同。」

常數放法也跟既有慣例一致:專案既有模組層級常數如 `LIFETIME_SECONDS = 120`(`src/rtb/executor/capability_signer.py:24`)、`VISIBILITY_TIMEOUT`(`inbox_store.py`),快照把比例與最小加額放「執行前檢查的模組常數,不分租戶」同一種放法,沒有另開設定檔或第二套參數來源。

新擋下原因與回應合併的做法,直接是既有模式的延伸,不是第二套機制。`BlockCode` 是封閉列舉(`src/rtb/executor/inbox_store.py:73`),新增 `budget_increase_too_large` 就是照現有方式加一個成員;回應合併已經有 `_PERMISSION_BLOCKS = frozenset({BlockCode.OVER_BUDGET_CAP.value, BlockCode.CAMPAIGN_NOT_ALLOWED.value})`(`src/rtb/executor/inbox_server.py:132-134`)這唯一一處泛稱合併點,快照只是把新代碼加進這個既有集合,沒有另開第二個合併路徑:

引句:「收件口把權限類擋下原因合併成泛稱的那一份清單,加上新代碼;分析端的值域清單不用改」

規則表當測試資料的寫法,是既有 `BLOCK_TRIGGERS` 表格化測試(`tests/executor/test_execution.py:152-167`,含 `assert set(BLOCK_TRIGGERS) == set(BlockCode)` 的完整性斷言)的直接擴展:快照把它擴成邊界表(剛好通過/差1/剛好超過各一列),並保留同樣的「列舉完整性」斷言精神(`test_every_guardrail_holds_at_its_boundaries`、`test_the_guardrail_table_covers_every_block_code`)。快照自己也明講程式端沒有多一條檢查路徑:

引句:「這張表不是第三套檢查:程式照舊只有執行前檢查與簽發器兩處,表只是把兩處的規則與順序寫在一起,並當成測試資料。」

程式碼核對:規則表列出的六種擋下原因與 `precheck`(`campaign_not_found`→`campaign_not_active`→`version_changed`)、簽發器(`campaign_not_allowed`→`over_budget_cap`,`src/rtb/executor/capability_signer.py:112-119`,`_tenant_of`/`sign` 兩段)的實際檢查順序完全對得上,規則表沒有新增第三個檢查點。

分析行程寫入呼叫掃描(S408)延續 `tests/analyzer/test_boundaries.py` 既有的 AST 掃描寫法:該檔已有兩支用 `ast.walk` 解析分析行程原始碼的測試(`test_the_analyzer_reaches_the_network_only_through_the_shared_client`、`test_the_analyzer_can_neither_read_the_signing_key_nor_import_the_signer`,同檔第 100-150 行),都是走「解析 import/名稱節點」的模式;S408 把掃描粒度從「有沒有 import 某模組」延伸到「呼叫共用 HTTP 用戶端時的參數字面值(方法、目標路徑、標頭)」,仍是同一支測試檔、同一種 AST 靜態掃描手法,沒有引入第二套機制(例如 lint 外掛或執行期攔截)。實際查證:分析行程目前呼叫 `request_json` 的三處(`src/rtb/analyzer/inbox_client.py:61`、`src/rtb/analyzer/dsp_client.py:118`、`dsp_client.py:193`)方法都已經是字面常數 `"GET"`/(POST 見 `inbox_client.py`),跟快照描述的現況一致，掃描設計可行不必改呼叫端寫法。快照自己也承認這套掃描的界線,跟既有 S30/S53 掃描的「防忘記不防繞過」定位一致,沒有誇大成機械防線:

引句:「防的是忘記,不防刻意繞過:故意把方法藏進變數以外的寫法(例如動態組字串)這支掃不到,那一層仍靠 DSP 端的憑證拒收兜底。」

沒有發現要求標記的架構越界:比例上限沒有跨到分析端或 DSP 端做決策,擋下合併沒有新開第二套「權限回應」邏輯,規則表沒有變成第三個檢查點,原始碼掃描沒有引入這個專案原本沒有的檢測手法。增量 2 設計在這五個對照點上都落在既有模組邊界內。
