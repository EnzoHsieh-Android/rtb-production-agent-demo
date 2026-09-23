severity: clean

## 鏡頭:相容、重建前提與既有測試

審的是凍結快照「## 增量 2 設計:護欄表格」一節。逐一核對程式碼(`src/rtb/executor/execution.py`
`inbox_store.py` `inbox_server.py` `capability_signer.py` `src/rtb/analyzer/dsp_client.py`
`flow.py` `src/rtb/httpclient.py`)與既有測試(`tests/executor/test_execution.py` 的
BLOCK_TRIGGERS、`test_version_conflict.py`、`test_inbox_server.py`、`test_queue.py`、
`tests/analyzer/test_boundaries.py`)後,沒有找到會讓照字面實作做錯或漏合約的問題;以下是核對過、
確認沒事的點,以及一個值得主線知道但不算增量 2 本節缺陷的觀察。

`precheck(proposal, view)` 目前只有三項(`campaign_not_found`→`campaign_not_active`→
`version_changed`),回傳值直接是 `BlockCode | None`(`src/rtb/executor/execution.py:280-288`);
比例上限插在版本已變之後、當第四項,不用改函式簽章(比例是模組常數,不是參數),三個既有呼叫點
(`_process`、`_after_expiry`、`_reconcile_not_found`)都是 `precheck(proposal, view)` 兩個位置參數,
插隊不影響呼叫端。

對 `_version_changed_or_none`(S310,`execution.py:291-296`)與對帳作廢路徑沒有影響,而且設計裡的推論
站得住:重跑檢查兩條路徑(`_after_expiry`、`_reconcile_not_found`)只有在 `precheck` 回 `None` 時才會
往下簽發送出,回任何 BlockCode(含新的比例代碼)都會先落到 `_version_changed_or_none`,這支函式只在
`checked is BlockCode.VERSION_CHANGED` 時才換代碼,其他一律回 `None`(照嘗試結果代碼確認,即
`operation_previously_failed`)。而比例檢查不可能在重跑時「新冒出」:DSP 每次成功寫入都把
`version + 1`(`src/rtb/dsp/store.py:209`),版本沒變就代表 `view.budget` 沒變,而比例檢查的兩個輸入
只有 `view.budget` 與提案裡固定的 `new_budget`——版本不變則輸入不變,結果不變。版本若變了,`precheck`
會在比例檢查之前就先回 `VERSION_CHANGED`(順序排在比例前面),不會讓比例檢查有機會跑到不同的結果。
所以確實不需要另外改 `_version_changed_or_none`,跟設計裡「這兩條路徑不需要比例擋下的專屬確認代碼」
的說法一致。

簽發器(`capability_signer.py`)未受影響:`over_budget_cap`(檔案第 117 行)排在 `campaign_not_allowed`
(第 134 行,`_tenant_of`)之後才判,跟護欄表順序 5、6 一致,增量 2 不動這支檔案。

`test_execution.py` 的 `BLOCK_TRIGGERS`(第 152-179 行)目前用 `assert set(BLOCK_TRIGGERS) ==
set(BlockCode)` 鎖住「鍵等於整個列舉」,設計裡明講這條斷言會被 [S404] 的三份清單完整性斷言取代、
`OPERATION_PREVIOUSLY_FAILED` 的觸發搬成獨立測試——這正好對上程式現狀,增量 2 落地後這支測試需要照
設計改寫,沒有漏項。

`_PERMISSION_BLOCKS`(`inbox_server.py:133-134`)是就地寫死兩個值的 `frozenset`,加入
`BlockCode.BUDGET_INCREASE_TOO_LARGE.value` 只是多一行,不影響 `_answered_block_code` 的邏輯;分析端
`flow.py` 的 `_BLOCK_CODES`(第 293-294 行)本來就只認合併後的 `"not_permitted"` 字串,不含
`over_budget_cap`/`campaign_not_allowed` 這些細分代碼,所以新代碼一經合併,分析端值域清單確實不用改,
跟設計「4. 回給分析行程合併成 not_permitted」一節的說法一致。

`_proposals_outdated`(`inbox_store.py:289-296`)目前的重建判斷,原文核對後跟現況段落描述一致:只檢查
CHECK 約束字串裡有沒有 `Disposition.DEAD_LETTER` 這個死信「處置」值,完全沒有逐一核對 `BlockCode`
列舉本身——現況段落寫「只看死信這個處置在不在限制裡,不看擋下原因」是準的。往前查歷史,
`OVER_BUDGET_CAP`/`CAMPAIGN_NOT_ALLOWED`(commit b4a2077)比死信 `DEAD_LETTER`(commit d45e22c)先進
程式,所以死信這個哨兵目前「湊巧」對得上——但這只是時間先後的巧合,不是通用比對,跟設計要求增量 1
必須改成逐一核對每個 `BlockCode` 成員的說法吻合:如果增量 1 只是換一個新哨兵(比如查總曝險代碼在
不在),舊資料庫在總曝險代碼進場前建的、但已經含增量 1 之前所有既有代碼(含 `OVER_BUDGET_CAP`)的表,
一開啟就會被誤判成「不用重建」,增量 2 一寫比例代碼就撞 CHECK 限制——[S406] 用「增量 1 版列舉建舊庫」
正好會抓到這種只換哨兵的錯誤修法,設計這裡的因果鏈是對的。

`test_inbox_server.py` 的匯入白名單測試(第 349-377 行)管的是 `inbox_server`/`inbox_store`
兩支模組有沒有多匯入 `rtb.domain` 的欄位驗證邏輯,增量 2 不會讓這兩支模組多匯入任何東西(只是在既有
`frozenset` 常數裡加一個字串、在既有列舉裡加一個成員),跟這條白名單無關,不會被牽連。

`tests/analyzer/test_boundaries.py` 已有的動態匯入禁令與網路模組禁令(第 102-123 行)只掃
`src/rtb/analyzer` 目錄本身,不會進到共用的 `rtb.domain`/`rtb.httpclient`;[S408] 要求的靜態匯入閉包
掃描範圍比它廣(含被載入的每個本專案模組),兩者是互補而非取代關係,設計文字裡也沒有宣稱要拿掉這支
既有測試,不衝突。

## 觀察(不算增量 2 本節的缺陷,但值得主線注意)

`test_execution.py` 的 `BLOCK_TRIGGERS`/`set(BlockCode)` 完整性斷言是「照整個列舉走」的窮舉測試,
增量 2 自己的合約([S404])會把它換掉;但增量 2 的實作前提是「增量 1 已合併」,而增量 1 的合約清單
([S330]-[S340])沒有一條提到要同步改這支測試或補進 `BLOCK_TRIGGERS`。若增量 1 真的新增一個
`BlockCode`(例如「總曝險已滿」)卻沒有動 `BLOCK_TRIGGERS`,這支既有測試會在增量 1 自己合併的那一刻
就先紅,跟增量 2 有沒有動手無關。這不是「增量 2 護欄表格」這節寫錯或漏合約,而是增量 1 那半邊在銜接
既有測試時的空隙,寫在這裡給主線在排增量 1 的驗收清單時對一下。
