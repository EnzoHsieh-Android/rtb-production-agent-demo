severity: major

审查范围:只审「## 增量 2 設計:護欄表格」節(比例上限、護欄表、新擋下原因)。鏡頭:新增一個擋下原因列舉成員牽動的既有測試/程式,以及三條 precheck 呼叫路徑的相容性。已對照程式碼:src/rtb/executor/execution.py、inbox_store.py、inbox_server.py、capability_signer.py、src/rtb/analyzer/flow.py、tests/executor/test_execution.py、tests/analyzer/test_boundaries.py。

## F1 S310 的 `_version_changed_or_none` 會把新擋下原因吞成「同一操作先前已失敗」,漏了 not_permitted

severity: major
blocking: 是 — 行為錯:憑證過期後重讀與對帳查不到重跑這兩條路徑,比例上限擋下會回報成跟原始擋下原因不同的代碼,違反 [S407]/使用者對「不允許」合併的裁定在這兩條路徑上不成立。

引句:「新規則放進 precheck 對三條呼叫路徑(處理一筆、憑證過期後重讀、對帳重跑)自動都套用,不用各補一次」

程式核對:`precheck()`(execution.py:280)只吃 `(proposal, view)`,加比例檢查確實不用改簽章,三處呼叫(`_process` execution.py:362、`_after_expiry` execution.py:496、`_reconcile_not_found` execution.py:654)都會跑到新規則——**擋下這件事**三條路徑都會生效,設計這句話對「擋不擋」是對的。

但擋下之後**回報哪個代碼**,三條路徑不一致:
- 路徑 1(`_process`)：`precheck` 直接回傳新代碼,`_settle` 用這個代碼確認 `ack_blocked`,收件表存的就是精確代碼「單筆加預算超過比例上限」,之後 [S407] 那條 not_permitted 合併看到的是這個精確代碼。
- 路徑 2(`_after_expiry`)與路徑 3(`_reconcile_not_found`)：這兩處在业务不過時,都是呼叫 `_version_changed_or_none(live, checked)`(execution.py:291-296)取得要記的 `block_code`,再丟給 `_void_then_fail`。這支函式的邏輯是:
  ```
  return BlockCode.VERSION_CHANGED if live and checked is BlockCode.VERSION_CHANGED else None
  ```
  只特判 `VERSION_CHANGED`,其他一切(包含新的比例上限代碼)回 `None`。`_void_then_fail` 拿到 `block_code=None` 時,終點確認走 `_ack_terminal`(execution.py:432-448)的 `block_code or block_code_for_failure(row.code)`;`row.code` 是作廢成功那格對應的 `C.NOT_HAPPENED`,`block_code_for_failure`(inbox_store.py:84-89)不認 `NOT_HAPPENED`,落到預設值 `OPERATION_PREVIOUSLY_FAILED`。

  這不是我編的邊界情況——`_version_changed_or_none` 的 docstring 原文就是:「其他原因(含提案過期、權限不過)回空值,照嘗試結果代碼確認」,而 Phase 5 [S310] 的合約行本身也寫死了「其他失敗應照舊寫『同一操作先前已失敗』」,並且已經有兩支綁定測試(`test_a_version_change_found_after_capability_expiry_is_acknowledged_as_version_changed`、`test_a_version_change_found_while_reconciling_is_acknowledged_as_version_changed`)釘住「非版本已變 → 一律歸同一操作先前已失敗」這個行為。也就是說:**這兩支既有測試不會紅**(它們沒斷言新代碼),但增量 2 的新規則一旦真的在路徑 2/3 觸發,收件表存的擋下原因會是 `operation_previously_failed`,不是 `budget_increase_too_large`——`_answered_block_code`(inbox_server.py:138)裡的 `_PERMISSION_BLOCKS` 就算把新代碼加進去也接不到,因為存進去的根本不是新代碼。分析行程那端收到的是 `not_permitted`(因為 `operation_previously_failed` 本身也在分析端 `_BLOCK_CODES` 域裡,回應能過關),行為上「分析行程結案不重新規劃」這一點還算矇對了,但稽核紀錄裡看到的擋下原因是錯的(死信 UI、稽核想知道「為什麼擋」時,查到的是誤導性的「同一操作先前已失敗」而不是「超過比例上限」)。

  補一句:什麼情況會真的走到路徑 2/3 才第一次撞到比例上限?比例上限的判斷基準是「執行前重讀到的現況預算」,只要在憑證過期重簽或對帳重跑之間,DSP 現況預算被別的請求改動過(版本已變的排序在比例上限之前,所以只要版本沒變、但現況預算本身因為另一個較早已提交的操作而變了——這是可能發生的,因為版本會反映那次變更,「版本已變」會先擋下……仔細想一輪:`precheck` 順序是 not_found → not_active → version_changed → (新的)比例上限,只要版本沒變化,比例上限這格看到的現況預算就是第一次執行前檢查時看到的那個,不會在路徑 2/3 中突然變化。**所以嚴格說,同一個提案第一次執行前檢查若沒被比例上限擋下,路徑 2/3 重跑同一個 precheck 得到的 view 版本沒變,比例上限也不會在路徑 2/3 才第一次被觸發**——除非第一次是在簽發器之後才失敗(比例上限排在版本已變之後、簽發器兩項之前,所以只要版本沒變,比例上限判斷應該跟第一次一致)。

  這一點削弱了此發現的實際觸發面,但沒有完全排除:路徑 2/3 的 `checked = precheck(proposal, view)` 是拿**當下重讀**的 `view`,而 `live`(是否仍在決策有效期內)與 `view` 是分開判斷的;如果版本沒變但 `view` 本身在 DSP 端有其他讀取抖動(例如同一個 campaign 的 budget 欄位被別的字段更新但 version 号未变——需視 DSP 是否保证 budget 变更必然带动版本递增而定,原始碼裡沒看到這個保證式的合約行,只在「事故 F4」一類討論版本比對,没有針對「budget 变了但 version 没变」这个组合写不变量),所以不能排除。即使把觸發面收窄到「機率很低」,設計文件的主張「三條路徑自動都套用,不用各補一次」在事實上是誇大的——它對「擋下」為真,對「回報的代碼一致」為假,而 [S407] 的驗收準則字面上沒有限定路徑,若之後有人依字面替 S407 補一支涵蓋路徑 2/3 的測試,現在的最小設計會讓它紅。

建议:在最小設計裡明講這個限制(路徑 2/3 只保留「版本已變」的精確代碼,其它一律降級為「同一操作先前已失敗」,新增的比例上限代碼跟著這條既有規則走,不特別處理),或者把 `_version_changed_or_none` 擴成也認新代碼(但這樣 S310 的既有合約行與其中兩支綁定測試的措辭要一起改,牽動面更大)。目前草稿兩者都沒選,是空白。

## F2 「新擋下原因要能寫進舊資料庫」倚賴的既有判斷式,現在的寫法不會因為新增列舉成員而觸發重建

severity: major
blocking: 是 — 這是增量 2 的 [S406] 直接依賴的機制;若增量 1 對 `_proposals_outdated()` 的修法只是「碰巧解決」而不是通用判斷,增量 2 不改這段程式的前提就不成立,[S406] 會紅。

引句:「前提:增量 1 已合併。重建判斷的修法由先實作的增量 1 做一次...增量 2 沿用,不再改那段程式,只驗新代碼在重建後寫得進去。」

程式核對(inbox_store.py:288-293)目前的 `_proposals_outdated()`:
```python
sql = self._conn.execute(
    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'proposals'").fetchone()
return sql is None or f"'{Disposition.DEAD_LETTER.value}'" not in sql[0]
```
這支函式只檢查 CREATE TABLE 全文裡有沒有 `'dead_letter'` 這個子字串,而 `dead_letter` 是 `Disposition` 列舉(處置四態)的既有成員,不是 `BlockCode`(擋下原因六態)的成員——這兩個列舉分屬 proposals 表的兩個不同 CHECK 約束(`_DISPOSITION_COLUMN` 與 `_BLOCK_CODE_COLUMN`,inbox_store.py:96-97)。任何現存資料庫只要是「加入 dead_letter 那次遷移」之後建的,CREATE TABLE 全文裡老早就有 `'dead_letter'` 這個子字串,`_proposals_outdated()` 對這種資料庫永遠回 `False`——不管 `BlockCode` 列舉之後加了幾個新成員。這正好是計劃「現況」段自己寫的那句「既有『要不要重建』的判斷只看死信這個值在不在,看不出新代碼」的字面驗證:不是我猜的風險,是目前程式碼可重現的事實(`grep` 得到的就是唯一一處判斷邏輯)。

這代表:增量 1 的 [S338] 如果只是「多加一個字串判斷」(比如再檢查一次某個增量 1 專用代碼在不在 CHECK 裡),那個修法本質上跟現在這行一樣是「認一個固定標記」,不是「比對整份 BlockCode 列舉」——增量 2 的新成員仍然不會觸發重建,[S406] 會撞上同一個坑,不是「沿用就好」。要讓增量 2 真的不用碰這段程式,增量 1 必須把 `_proposals_outdated()` 改成通用檢查(例如把 CHECK 約束字串裡的允許值集合,跟 `set(BlockCode)` 逐一比對,任何缺項都重建),不能只加一個新的固定標記。這件事目前只在增量 2 的「不做的事」與「前提」兩句話帶過,沒有一條合約釘住「增量 1 的修法必須是通用的、能接住任何未來新增的 BlockCode 成員」,建議在增量 1 的 [S338] 措辭或增量 2 的前提裡明講這個要求,否則兩個增量各自看設計都「合理」,合起來卻不成立。

## F3 回退段「收件表已重建過的限制多認一個值,留著無害」對已寫入的舊列不成立

severity: minor
blocking: 否 — 屬於回退之後的邊界情境,不影響增量 2 前進時的正確性,但回退聲明本身不夠精確,可能誤導日後真的要退版的人。

引句:「收件表已經重建過的限制多認一個值,留著無害;表格測試與分析端寫入掃描是純測試,留著無害。」

程式核對:`_PERMISSION_BLOCKS`(inbox_server.py:132-134)是在**回應時**現算的合併(`_answered_block_code`,inbox_server.py:138),不是寫入時就把合併結果存進收件表——收件表的 `block_code` 欄位永遠存精確代碼(inbox_store.py 的 `BlockCode` 欄位),`not_permitted` 只在 `_accepted_body` 組回應那一刻現算。回退時如果把「新擋下原因加入 `_PERMISSION_BLOCKS`」這行也一併拿掉(增量 2 的回退範圍寫「擋下原因列舉的新成員與回應合併清單裡的新代碼即回到現況」——即拿掉列舉成員也拿掉 `_PERMISSION_BLOCKS` 裡那一項),那麼:

- 退版**之前**已經因為比例上限被擋下、寫進收件表的舊列(`block_code = 'budget_increase_too_large'`),退版**之後**如果分析行程對同一份提案重送(這是完全正常的路徑——分析行程不知道有沒有收,會定期用同一把鍵重送查詢),收件口會用退版後的程式碼重新組回應:`_answered_block_code('budget_increase_too_large')`——因為 `_PERMISSION_BLOCKS` 已經不含這個字串(隨列舉一起被拿掉),函式會原樣把 `'budget_increase_too_large'` 回給分析行程,不會合併成 `not_permitted`。
- 分析行程那端,`_BLOCK_CODES`(flow.py:293)域列表也隨退版拿掉這個字串(增量 2 回退範圍寫「分析端寫入掃描是純測試,留著無害」,但沒提回退分析端要不要保留這個值域字串——如果照described「回到現況」也一起拿掉),`answer.block_code in _BLOCK_CODES` 檢查落空,`_belongs_to` 判定這個回應「內容不一致」,整包回應被當成「回應讀不懂」丟棄(`_from_handed_off`/`_belongs_to`,flow.py:317-324),`step()` 回 `None`(這一輪沒有進展)。

  結果:那個任務不會結案,也不會重新規劃,會停在原地每輪重送、每輪被判讀不懂,直到有人手動介入——這跟「留著無害」的意思不一樣,是「舊資料在特定回退窗口裡會卡住一個任務,不是拋例外、不是資料損毀,但也不是『無害』」。

建议:回退段補一句處理办法,例如「退版前先讓所有帶著新擋下原因的舊列走完(死信或已交給執行),或者退版時把 `_PERMISSION_BLOCKS`/`_BLOCK_CODES` 的新成員留著不拿掉,只拿掉會再寫入新值的那段程式」——後者(只退觸發端、留著讀取端的相容)才是真正無害的退版順序,現在的回退段沒有分清楚「移除觸發」跟「移除辨識」是兩件事,分批退版順序寫反會卡任務。

## 其他核對:沒有發現問題的觀察

`BLOCK_TRIGGERS` 全集斷言(test_execution.py:162-172,`assert set(BLOCK_TRIGGERS) == set(BlockCode)`)是逐支跑的 parametrize,新增 `BlockCode` 成員後這支測試會在收集時把新成員也排進 `list(BlockCode)`,若沒同時在 `BLOCK_TRIGGERS` 字典補上對應的觸發函式,**所有**參數化案例(不只是新代碼那一格)都會在 `assert set(...) == set(...)` 這一行斷言失敗——這是設計草稿裡「測試端:把既有『每個原因一種觸發』的表擴成邊界表」這句話隱含、但沒有明寫的必要條件:增量 2 落地時,`tests/executor/test_execution.py` 這支既有檔案**一定要改**(至少要在 `BLOCK_TRIGGERS` 裡補新成員的觸發函式),不能只加一支新測試檔就了事,否則現有測試套件會整批變紅,不是新增測試才紅。草稿裡 [S403]/[S404] 的敘述方向是對的(擴成邊界表、斷言列舉完整覆蓋),只是沒有指名這張表要不要就是 `BLOCK_TRIGGERS` 本身或另開一張——如果是另開一張新的邊界表而不動 `BLOCK_TRIGGERS`,仍然逃不掉上面這個全集斷言,兩者都得補。新觸發函式要小心排序:比例上限排在 `precheck` 最後(在 `version_changed` 之後、在簽發器兩項之前),觸發函式必須讓 `campaign_not_found`/`campaign_not_active`/`version_changed` 都不成立且 `campaign_not_allowed` 也不成立(否則會被更前面的規則先擋,量出來的是別的代碼),`_over_cap` 現有寫法(改租戶設定的 `max_budget`)刚好示范了「只動一個維度」的写法,新觸發函式可以照抄这个模式(只调整提案的 new_budget 相对现况budget的比例,不动别的)。

`_answered_block_code`/`_PERMISSION_BLOCKS`(inbox_server.py)與分析端 `_BLOCK_CODES`(flow.py:293)這兩份清單確實都要加新代碼,但分析端**只**要新代碼最終被收件口合併成 `not_permitted`(已經在域裡)就不用改——這點跟設計草稿「分析端的值域清單不用改」的說法核對是對的,前提是 F1 提到的兩條路徑真的都把新代碼正確合併(目前只有路徑 1 成立)。

增量 1 的 [S339]/[S338] 與增量 2 的 [S406]/`_PERMISSION_BLOCKS` 新增項都改同一個 frozenset 字面值與同一個 `_proposals_outdated`/`_rebuild_proposals` 機制,兩個增量若在不同工作區平行開發,合併時大概率是文字層的合併衝突(兩邊都在同一個 frozenset 字面裡加一行),不是語義衝突,只是提醒順序:增量 2 的分支要 rebase 在增量 1 之後,不能反过来(草稿自己也写了这个顺序要求)。

`_version_changed_or_none` 的三個既有呼叫點(process_one 內的直接 precheck、`_after_expiry`、`_reconcile_not_found`)裡,只有直接 precheck 那條(`_process`,execution.py:362)在偵測到擋下時完全不開嘗試、不寫 DSP 呼叫——這跟 [S400] 「不寫嘗試紀錄、不呼叫 DSP 寫入」的字面完全對得上,不需要改 `_take`/`_write` 任何一段:比例上限跟現有 `campaign_not_found`/`campaign_not_active`/`version_changed` 走的是完全一樣的“precheck 直接擋、不進 `_take`”路徑,程式碼層面相容,不用新增分流。
