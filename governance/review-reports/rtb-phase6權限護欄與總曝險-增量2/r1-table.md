severity: major

## F1 合約 S403 沒有承接設計正文「非數值規則兩列」的例外,字面讀起來要求全部六條都有三組邊界

引句:「每條規則至少有「剛好通過」「差 1 通過」「剛好超過 1 擋下」三列(不適用數值的規則用「成立/不成立」兩列)」

引句:「[S403] 護欄表上每一條單筆規則,在剛好通過、差 1 通過、剛好超過 1 三組邊界上應分別通過或擋下,擋下代碼應是表上那一列的代碼。」

severity: major
blocking: 是 — S403 是唯一綁測試(`test_every_guardrail_holds_at_its_boundaries`)的合約句,實作者只會照 S403 的字面去寫測試,不會回頭套用正文裡那句括號註記(合約句通常被當成獨立、完整的規格)。六條規則裡至少四條(campaign_not_found、campaign_not_active、campaign_not_allowed 是存在性/歸屬布林判斷,version_changed 是等值比對)本質上沒有可以「差 1」的純量可調,程式面也印證這點:file: `src/rtb/executor/execution.py:280-288`(`precheck` 的前三項都是 `is None`／`!=`／`!=` 布林比較,沒有任何閾值運算)與 `src/rtb/executor/capability_signer.py:130-134`(`campaign_not_allowed` 是「找不找得到租戶」的存在性檢查,不是數值比較)。只有 `budget_increase_too_large`(新)與 `over_budget_cap` 兩條是真正的數值上限,能造出「剛好等於／差1／超過1」三組輸入。若照 S403 字面對其餘四條也要求三組邊界,測試作者會被迫發明沒有意義的「差 1」輸入(例如 campaign_not_found 沒有可以差 1 的量),或者測試乾脆漏掉這四條的邊界覆蓋而不自知——因為 S403 本身沒有寫出可以豁免的判準,只有正文的括號注記寫了,但合約句沒有指回那個判準,也沒有講「哪些規則屬於不適用數值」。建議:把括號裡的豁免判準搬進 S403 本文(或至少讓 S403 明確指名六條裡哪幾條走三列、哪幾條走兩列),否則落地時會出現「合約要求」與「正文允許」兩份互相打架的依據。

多條規則同時不成立時「回表上順序最前那一條」對簽發器涵蓋的兩條(campaign_not_allowed、over_budget_cap)成立,而且是由程式結構保證,不只是巧合:`sign()` 一開始呼叫 `_tenant_of()`,找不到租戶會在讀到預算上限之前就 raise `SigningRefused("campaign_not_allowed")`(`src/rtb/executor/capability_signer.py:110-118` 與 `128-134`),`over_budget_cap` 的檢查在它之後才跑,所以「租戶歸屬」必定先於「預算上限」被判定,跟表格第 5、6 列的順序一致。往上一層,`execution.py:362` 的 `signed = precheck(proposal, view) or self._sign(proposal)` 用短路運算,保證執行前檢查(表格第 1–4 列)全數通過才會呼叫簽發器(表格第 5–6 列),所以「回最前面那條」對整張六列表(不只是簽發器那兩條)在架構上是站得住的斷言,不是設計文件一廂情願的敘述。

擋下原因合併成 `not_permitted` 的既有實作與設計描述完全吻合:`src/rtb/executor/inbox_server.py:133-139` 的 `_PERMISSION_BLOCKS = frozenset({BlockCode.OVER_BUDGET_CAP.value, BlockCode.CAMPAIGN_NOT_ALLOWED.value})` 正是表格裡標「not_permitted」的第 5、6 列現況(第 4 列 budget_increase_too_large 是設計要新加進這個集合,程式裡目前還沒有,屬預期中的待實作)。

過期(EXPIRED)與「同一操作先前已失敗」為何不在護欄表上,正文交代得夠清楚,程式也印證:EXPIRED 完全不是 `BlockCode` 列舉成員,而是獨立的 `Result.EXPIRED`(`execution.py:259,355,361,409`),`ack_expired` 寫的欄位跟 `ack_blocked` 分開(`inbox_store.py:578` 起的 `ack_expired` 與 `568` 起的 `ack_blocked`,`block_code` 欄位只在 `ack_blocked` 被寫入,且受 CHECK 限制只收 `BlockCode` 成員清單,`inbox_store.py:98`),所以 EXPIRED 天生就不會出現在以 `BlockCode` 列舉為基礎的護欄表或 S404 完整性測試裡,不需要額外排除語句;「同一操作先前已失敗」則是表格與 S404 都有明確排除,理由(鍵已存在的分流,不是單筆規則)寫在正文,判斷一致。

比例上限(第 4 列)與租戶單筆上限(第 6 列)兩條數值規則同時存在時,由於執行前檢查(含比例上限)排在簽發器(含單筆上限)之前,理論上「先撞到哪條」已經被表格順序決定,不是隨機的;但要在表格測試裡真的造出「剛好卡在比例上限邊界、同時又要通過或卡在單筆上限邊界」的組合,需要挑選夠寬鬆的租戶 `max_budget`,這件事設計正文沒有明講——例如正文給的例子「現況 100 → 150 過、151 擋」(針對比例上限)並未說明測試用的租戶單筆上限是多少,若剛好選得比 151 小,這組原本要驗證比例上限的邊界會先被單筆上限攔下,驗證的其實是另一條規則。這屬於措辭/文件精度層級,不影響任何合約句本身的正確性,實作時只要挑一個明顯夠大的 `max_budget`(例如遠大於所有測試用的現況預算)即可避開,但設計文件目前沒有把這個限制寫清楚。
