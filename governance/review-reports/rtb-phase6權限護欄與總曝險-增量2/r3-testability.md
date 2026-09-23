severity: minor

本輪只審「## 增量 2 設計:護欄表格」節,鏡頭是可測性(S400–S408 每條測不測得出來、拿掉防護會不會真的翻紅、有無假綠風險)。對照過的程式:src/rtb/executor/execution.py(precheck/_process/_sign)、src/rtb/executor/inbox_store.py(BlockCode、_proposals_outdated/_rebuild_proposals)、src/rtb/executor/capability_signer.py、tests/executor/fakes.py(Harness/FakeDsp/write_config)、tests/executor/test_execution.py(BLOCK_TRIGGERS)、tests/analyzer/test_boundaries.py、src/rtb/analyzer/policy.py、tests/analyzer/test_policy.py、src/rtb/httpclient.py、src/rtb/analyzer/dsp_client.py、inbox_client.py。git log 確認增量 1、增量 2 目前都只有設計文件,程式碼都還沒動手。

整體結論:八條合約(S400–S408)逐條檢查下來,現有測試基礎設施(真的 `precheck` 純函式、Harness+FakeDsp+真簽發器與租戶設定、既有 ast 掃描先例 `test_the_analyzer_reaches_the_network_only_through_the_shared_client`/`test_the_analyzer_can_neither_read_the_signing_key_nor_import_the_signer`、`tests/executor/test_queue.py` 的 `PHASE3_SCHEMA` 舊庫先例)都已經有對應的做法,沒有發現會讓合約測不出來或必然假綠的硬傷。以下是逐項核對與一條 minor 觀察。

## 逐項核對

- [S400]/[S401] 比例上限的擋下/通過:`precheck(proposal, view)`(execution.py:257)目前只吃 `proposal` 與 `view: CampaignView | None`,不需要改函式簽章就能在裡面多判一條(新預算減 `view.budget`);純函式,可以直接 `precheck()` 呼叫測,不必經 Harness、也不必碰 DSP 或簽發器。可測。
- [S402] 分析端規則在 1–1000 全跑一遍不撞比例上限:`tests/analyzer/test_policy.py` 已有 `state_evidence(budget=)`/`metrics_evidence(spend=, impressions=, clicks=)` 兩個建構器,`policy.decide()` 的漲幅公式在 `src/rtb/analyzer/policy.py:81`(`min(max(round(budget*1.1), budget+1), MAX_INT)`)是純運算,不依賴外部狀態;spend 固定給低值即可讓每個 budget 都判定「配速偏低有投遞」。逐一呼叫 `precheck` 驗證 1..1000 完全可行,不需要新的替身或介面。
- [S403] 護欄表邊界(剛好過/差 1 過/剛好超過 1):設計要求「全部走真的處理一筆的路徑」,現有 `Harness`/`FakeDsp`/`write_config` 已支援任意 `CampaignView(budget=…)` 與 `max_budget=…` 組合,不需要新增替身能力。
- [S404] 三份清單聯集等於列舉、兩兩交集為空:純粹是 `frozenset` 集合運算,`BlockCode` 是封閉列舉(inbox_store.py:73-81),斷言寫得出來、也真的會在漏列或重複歸類時翻紅。現有 `test_each_failed_precheck_blocks_the_proposal_without_a_write` 的 `assert set(BLOCK_TRIGGERS) == set(BlockCode)`(test_execution.py:164)正是這條斷言的前身,替換路徑清楚。
- [S405] 多條同時成立回表上最前一條:`_process`(execution.py:290-296)裡 `precheck(...) or self._sign(...)` 的呼叫序本身就是「表的順序」的來源,測試只要在同一個提案上讓兩條規則都成立、斷言回的是靠前那個 `BlockCode`,可測。
- [S406] 用增量 1 版列舉建舊庫、開啟後應接受新代碼:`tests/executor/test_queue.py`(約 75-111 行)已有先例——用字面 SQL 常數 `PHASE3_SCHEMA` 建一個「舊」資料庫再驗遷移,不是靠反查任何歷史 enum 物件。S406 應照同一手法:把增量 1 落地後的 `BlockCode` 值當時的字面清單抄成常數建表,不依賴「回頭去查 git 歷史上的列舉」。前提(增量 1 已合併、[S338] 的重建判斷是逐一核對而非哨兵比對)在設計裡已經寫明,只要實作照做,這條測試會在有人把 [S338] 寫成哨兵比對時真的翻紅(這正是既有現況「只看死信在不在」的舊寫法,inbox_store.py:288-296 目前的 `_proposals_outdated` 就是用 `Disposition.DEAD_LETTER.value` 當哨兵,跟設計裡描述的現況吻合)。
- [S407] 比例上限擋下經重送回 not_permitted:設計已指定「加進既有參數化測試的參數清單」而不是另開同名測試,對應到 test_execution.py 裡以 `BlockCode` 為參數的既有測試結構,做法明確,不會重複造出兩套判準。
- [S408] 分析行程靜態匯入閉包只在兩支客戶端檔案直接呼叫 `request_json`:現有 `test_the_analyzer_reaches_the_network_only_through_the_shared_client`(tests/analyzer/test_boundaries.py 約 100-120 行)已經是同一手法的先例(純 `ast.walk`,不執行程式),只是掃描範�圍目前只到 `analyzer.rglob("*.py")`,還沒做「本專案模組」的遞迴匯入閉包;把範圍換成遞迴解析 import 目標模組對應的檔案路徑,在本專案(全部用絕對匯入 `rtb.xxx`,沒有相對匯入,`grep` 確認過)下是可行的機械改法,不需要新工具。核對 `request_json` 的實際用法(`src/rtb/analyzer/dsp_client.py:118`、`:193`、`inbox_client.py:61`)確認方法參數都已經是字面常數字串,符合設計描述的現況。

## 一條觀察(minor,不擋)

[S408] 的敘述「這個函式名每一次出現都必須是直接呼叫」沒有講清楚要把 `from rtb.httpclient import request_json` 這行 import 本身(它讓這個名字「出現」,但不是呼叫)排除在「每次出現都是直接呼叫」的規則之外,也沒講清楚要把 import 敘述裡的名字(`ast.alias.name`,不是 `ast.Name` 節點)一併算進「以任何形式提到這個函式名」的檢查對象(否則 `from rtb.httpclient import request_json as rj` 這種改名匯入,若掃描只找 `ast.Name(id="request_json")` 節點,會在其他模組漏抓,而在允許的兩支檔案裡,替換名字後改叫 `rj(...)` 也不會被「非直接呼叫」抓到,因為呼叫時用的識別字已經不是 `request_json`)。這不是「測不出來」的問題——只要實作時把 `ast.alias.name`、`ast.Name.id`、`ast.Attribute.attr` 三種節點都納入同一次「提到這個名字」的比對(既有的 `test_the_analyzer_can_neither_read_the_signing_key_nor_import_the_signer` 已經用 `ImportFrom.module` 做過同類判斷,可以直接參考套用),就能把這個縫補上;只是設計文字目前沒把這一步寫清楚,留給實作者自己補,值得在合約或設計裡補一句「import 敘述裡的名字也算一次出現,且不算作直接呼叫」,避免實作時漏掉別名改名這條路。

引句:「這個函式名每一次出現都必須是直接呼叫;被賦值給別的名字、當參數傳出去、被偏函式包住,一律紅。」
