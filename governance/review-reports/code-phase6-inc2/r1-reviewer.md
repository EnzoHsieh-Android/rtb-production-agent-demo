severity: clean

## 審查範圍與方法

比對材料:`/Users/enzo/rtb-3b/governance/review-reports/code-phase6-inc2/r1-snapshot.patch`(已套用在 HEAD,`git diff 48007b1..HEAD` 內容一致)對照
`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md` 的「增量 2 設計:護欄表格」一節與合約 [S400]–[S405][S407]–[S409]。
在 `/tmp/rtb3b-copy`(唯讀 repo 的臨時副本)做了兩次「拿掉防護實跑」的殺傷力驗證,跑完即刪除。

沒有發現任何會做出錯的行為、破壞合約、資料損壞或測試假綠的問題。以下是逐項確認的記錄。

## 比例公式與整數邊界

`_increase_too_large` 的算法是 `current * MAX_INCREASE_NUMERATOR // MAX_INCREASE_DENOMINATOR`(即 `current // 2`,運算子優先序沒有踩到 `1//2=0` 的陷阱)與 `MIN_INCREASE_STEP` 取大,`increase > allowed` 才擋。逐一核對 `tests/executor/test_guardrails.py` 的邊界表:

引句:「(100, 150, False), (100, 149, False), (100, 151, True),  # 剛好等於、差 1、超過 1」

100→150(=50 上限,通過)、151(擋)、149(通過);3→4/5、1→2/3、0→1/2、`MAX_INT-1`→`MAX_INT` 全部跟設計文件「現況 100 → 150 過、151 擋、149 過;現況 3 → 4 過...現況 1 → 2 過、3 擋」逐字對得上。減預算、暫停不受影響(`test_decreases_and_pauses_are_not_bounded_by_the_ratio_cap`)也驗證了 0 與大額情形。

殺傷力驗證(拿掉比例檢查那兩行,在 `/tmp/rtb3b-copy` 跑):

```
7 failed, 1034 passed
test_a_budget_increase_is_bounded_by_the_ratio_cap[100-151-True] 等 7 條全部由紅轉綠時才通過
```

確認這批測試對這條規則是真的守衛,不是裝飾。

分析端現況規則(固定加一成、至少加 1)在 1–1000 與 `10**6, 10**9, 2**62, MAX_INT-1, MAX_INT` 下核對過 `policy.decide` 產生的提案(`test_the_analyzer_policy_never_trips_the_ratio_cap`);讀了 `src/rtb/analyzer/policy.py:81-82` 的 `new_budget = min(max(round(budget * (1 + BUDGET_INCREASE_FRACTION)), int(budget) + 1), MAX_INT)`,10% 的加成永遠遠低於 50% 上限,`MAX_INT` 封頂時增量會被壓到 0 或 1,兩種情況都在比例內,測試範圍與公式對得上。

## 順序:比例排在版本已變之後、簽發器之前

`src/rtb/executor/execution.py:287-300` 的 `precheck` 依序判斷 `campaign_not_found → campaign_not_active → version_changed → budget_increase_too_large`,呼叫端 `signed = precheck(proposal, view) or self._sign(proposal)`(execution.py:381)保證比例檢查全部跑完才輪到簽發器。`test_the_first_failing_guardrail_wins` 的交叉組直接驗證這個順序:

引句:「((_tenant(campaigns=("c2",)),), 151, BlockCode.BUDGET_INCREASE_TOO_LARGE),  # 比例 > 租戶」

這一列故意讓「不屬於租戶」與「超比例」同時成立,斷言回比例(排在簽發器之前),對照 `((_tenant(campaigns=("c2",), max_budget=120),), 150, BlockCode.CAMPAIGN_NOT_ALLOWED)` 驗證簽發器內部既有的「不屬於租戶 > 超單一廣告上限」順序沒被動到。四列交叉組把「不在投放 > 比例」「版本已變 > 比例」也覆蓋了,跟合約 [S405] 逐字對上。

## 護欄表每一列是否只讓要驗的那一條在邊界上

逐列核對 `GUARDRAILS`(execution 預設現況 100/版本 3/投放中,租戶預設 `max_budget=1000`、`campaigns=("c1","c2","c3")`,見 `tests/executor/fakes.py:37-39,46`):

- 存在/狀態/版本三條的「通過」列都用新預算 150(比例剛好 50,通過;租戶上限 1000,通過),不會被後面的數值規則咬到。
- 比例上限那三列固定現況 100、租戶上限 1000,150/149/151 只讓比例在邊界上。
- 「不屬於租戶」兩列現況 100、新預算 150,同上不會撞到後面的 `over_budget_cap`。
- `over_budget_cap` 三列把現況設成 900(比例上限 450),新預算 1000/999/1001 都遠低於比例上限,只讓單一廣告上限在邊界上。

`test_the_guardrail_table_covers_every_block_code` 斷言每個單筆規則代碼在表上至少各有一列擋下、一列通過,且擋下代碼等於該列規則對應的代碼;這半張表(不含增量 1 依賴的三份清單完整性斷言)跟合約 [S404] 的範圍一致,設計文件也明講這部分留到增量 1 合併後補,不算缺件。

## [S408] 掃描器與十一種自我驗證寫法

讀了 `tests/analyzer/test_boundaries.py` 新增的 `_source_of / _imported_modules / analyzer_import_closure / _resolve_from / _check_http_imports / _check_request_uses / write_call_offenders`,逐一對照真實碼(`src/rtb/analyzer/dsp_client.py`、`src/rtb/analyzer/inbox_client.py`、`src/rtb/httpclient.py`):兩支允許的檔案都只用 `from rtb.httpclient import request_json`(無別名)、每次呼叫都是位置參數字面常數方法、不帶 headers;dsp_client 只有兩個 GET 呼叫端點(`/campaigns/...`、`/operations/...`),inbox_client 只有一個送到 `.../proposals` 的 POST。真跑 `test_the_analyzer_has_no_write_call_besides_submitting_a_proposal` 綠燈。

逐一核對 `_FORGETFUL` 十種 + `domain_helper` 共十一種變體被抓到的**原因**(不是被別的規則順手撈到):

- `dsp_post`:直接呼叫合法(方法字面常數、無標頭),但被 `dsp_client` 專屬的「只准 GET」規則單獨抓到(`write_call_offenders` 的 `if module == "rtb.analyzer.dsp_client": ... if m != "GET"`)。
- `alias_assign` / `partial`:`calls` 字典只收「這個 Name 節點本身就是某個 Call 的 `.func`」的用法;賦值給別名或包進 `functools.partial` 時該 Name 節點不在 `calls` 裡,判成「提到卻不是直接呼叫」。
- `import_as`:在既有正確匯入旁多加一行帶別名的匯入,`_check_http_imports` 對 `names != {(_REQUEST, None)}` 的判斷單獨抓到別名(不是因為多了一次匯入,是因為別名)。
- `method_variable`:方法傳的是變數 `m` 不是 `ast.Constant`,被「方法不是字面常數」那個分支抓到。
- `headers`:方法仍是合法常數,但 `has_headers` 因為 keyword `headers=` 命中而抓到,跟 `method_variable` 是不同分支、不同理由。
- `module_import` / `package_import`:分別是 `import rtb.httpclient as _h` 與 `from rtb import httpclient`,`_check_http_imports` 的整模組匯入分支各自抓到(不靠允許清單,`policy.py` 本來就不在 `_ALLOWED_CALLERS`,但抓到的是「匯入了整個模組」這個更根本的理由)。
- `relative_in_function`:在 `flow.py` 函式內部寫 `from ..httpclient import request_json`,驗證的是相對匯入解析(`_resolve_from` 用點數層級接套件路徑)算出正確的 `rtb.httpclient` 之後,才因為 `flow.py` 不在允許清單而抓到——如果相對匯入解析錯了,這支測試會因為「根本沒偵測到匯入」而抓不到,是真的驗證了解析邏輯。
- `second_entry`:在 `httpclient.py` 自己加一個新入口 `post_json`,`dsp_client.py` 改匯入它;被「從共用用戶端匯入的名字不是 `request_json`」抓到,對應設計裡「共用用戶端哪天多一支請求入口」的防線。
- `domain_helper`:在領域層新開一支會發請求的模組,`flow.py` 在函式內部才匯入它;因為閉包正確地把函式內部的匯入也收進來,才能發現這支領域層模組匯入了 `request_json`(它不在允許清單裡),抓到的理由是「未授權模組匯入請求函式」,不是别的規則誤中。

實測用臨時副本在 `dsp_client.py` 尾端加一行 `_sneaky = request_json`(未在原十一種列表但同一類別),掃描立刻回報：

引句:「提到請求函式卻不是允許的直接呼叫」

確認掃描器對「未列在自我驗證清單但同類別」的別名寫法也有效,不是只認得清單裡那幾行字面文字。

## 既有三支測試的改動

- `tests/executor/test_multi_worker.py`:`test_two_workers_never_run_the_same_campaign_at_once` 把第二份提案的新預算從 160 改成 140。現況預算是 100(比例上限 150),160 在新規則下會在 `precheck` 被直接擋下,永遠走不到這支測試要驗的「開始嘗試互斥」那一段;改成 140 讓兩份提案都能通過 precheck、真正進到柵欄互等的那一段,是必要的修正,不是削弱——互斥驗證的邏輯(先開始的停在 DSP 寫入、`meet_before` 柵欄位置)完全沒動。
- `tests/executor/test_version_conflict.py`:`test_two_concurrent_writers_reproduce_a_version_conflict` 把兩個執行緒的預算從 (150,160) 改成 (150,140)。真 DSP 種子預算是 100(`real_dsp` fixture:`CampaignStore(...).seed_campaign("c1", budget=100)`),160 一樣會在各自的 precheck 就被擋下,兩邊就不會真的在 DSP 寫入這一步相撞,測不到版本衝突;改成 140 保留了兩邊都能通過 precheck、在 `barrier.wait` 真的競爭寫入 DSP 的原始意圖,斷言內容(一勝一敗、敗方 `version_changed`)完全沒動。
- `tests/executor/test_inbox_disposition.py`:`test_block_reasons_are_a_closed_list` 只是把封閉列舉的期望集合多加一個新成員,是純粹的資料更新,沒有動到任何斷言邏輯。

三處改動都是「新規則的副作用逼著調整既有測試的輸入數值」,不是「為了讓測試變綠而放寬判定」。

## 回應合併與收件表列舉

`src/rtb/executor/inbox_server.py:132-134` 把 `BlockCode.BUDGET_INCREASE_TOO_LARGE.value` 加進 `_PERMISSION_BLOCKS`,`src/rtb/executor/inbox_store.py` 新增列舉成員排在 `OVER_BUDGET_CAP` 之後、`OPERATION_PREVIOUSLY_FAILED` 之前,`tests/executor/test_version_conflict.py` 的 `test_a_resend_hides_which_permission_blocked_it` 新增一組參數驗證合併成 `not_permitted`,跟 [S407] 的合約與使用者裁定一致。

## 附帶查證(非本次改動,但跟 [S406] 相關的既有機制)

讀了 `src/rtb/executor/inbox_store.py:289-296` 的 `_proposals_outdated`:目前只檢查 `Disposition.DEAD_LETTER` 是否在建表 SQL 裡,並不逐一核對 `BlockCode` 每個成員,所以現在若真的把一個舊收件表打開並寫入 `budget_increase_too_large`,理論上會撞資料庫層 CHECK 約束。這正是計畫筆記「現況」段落已經寫明的已知缺口("重建判斷目前只看死信那個處置在不在限制裡,不看擋下原因")與 [S406] 延後到增量 1 合併後驗證的原因,本次改動沒有碰這段程式、也沒有動作想繞過它,純屬確認設計文件的描述跟程式碼現況相符,不算新發現、不影響本次判定。
