severity: major

## F1 回退計畫沒處理既有與新增的「完整性測試」,會讓回退後測試套件變紅
severity: major
blocking: 是 — 照設計字面回退會讓現行測試(test_execution.py 的 BLOCK_TRIGGERS 完整性斷言)與增量 2 自己新加的 [S404] 完整性斷言同時紅掉,回退不是「無害」而是「壞掉」。

引句:「留著的成員不會再被寫入,無害。表格測試與分析端寫入掃描拿掉比例那幾列即可。」

現況(base repo,增量 1/2 都還沒實作)已經有兩個把 `BlockCode` 當「封閉列舉、每個成員都要有真實觸發路徑」來斷言的測試:

1. `tests/executor/test_execution.py:152-178` 的 `BLOCK_TRIGGERS`:
   `assert set(BLOCK_TRIGGERS) == set(BlockCode)`(第 164 行),而且對每個 code 逐一斷言 `h.process()` 真的產出那個 `block_code`(第 175 行)。這張表現在對應「每個擋下原因各一種觸發條件」,增量 2 現況段落自己也提到這張表(「執行測試的 BLOCK_TRIGGERS,並斷言表的鍵等於整個列舉」),表示設計者知道它存在。
2. 增量 2 自己新加的 `[S404]`:「單筆規則代碼」與「非單筆代碼」兩份明列清單的聯集要等於整個 `BlockCode` 列舉、交集要空。

設計的回退決定是「只拿掉執行前檢查裡的比例上限那一項」但「列舉的新成員…要留著」。這兩件事同時發生時:

- `BLOCK_TRIGGERS` 要嘛保留 `BUDGET_INCREASE_TOO_LARGE` 的鍵(這樣 `set(BLOCK_TRIGGERS) == set(BlockCode)` 還過),但它對應的觸發函式已經沒有真正的比例檢查可以觸發——`h.process()` 不會再回那個 `block_code`,第 175 行的逐一斷言會紅;要嘛把這個鍵從 `BLOCK_TRIGGERS` 一起拿掉,那 `set(BLOCK_TRIGGERS) == set(BlockCode)` 直接不等(`BlockCode` 還有這個成員,`BLOCK_TRIGGERS` 沒有),一樣紅。兩條路都紅,無法兩全。
- `[S404]` 的完整性斷言同理:回退拿掉「表格那幾列」意味著把 `budget_increase_too_large` 從「單筆規則代碼」清單移掉,但設計沒說要把它挪進「非單筆代碼」清單;不挪的話兩份清單的聯集就少了這個仍然留在 `BlockCode` 裡的成員,`[S404]` 自己的聯集斷言也會紅。

這不是「表格測試與分析端寫入掃描拿掉比例那幾列即可」能單獨解決的——那句話只講了刪表格列與刪寫入掃描列,完全沒提到 `BLOCK_TRIGGERS`(既有測試,不在增量 2 的「不做」清單裡,回退時仍在測試套件內)也沒提到要把移除的成員改分類進 `[S404]` 的「非單筆代碼」清單。回退方案要嘛明講「連 `BLOCK_TRIGGERS` 的鍵與 `[S404]` 的清單分類都一起處理」,要嘛承認回退後需要額外改動,不能寫成「無害」「拿掉那幾列即可」。

file: `tests/executor/test_execution.py:152` (BLOCK_TRIGGERS 定義)
file: `tests/executor/test_execution.py:162-178`(`test_each_failed_precheck_blocks_the_proposal_without_a_write`,完整性斷言在第 164 行、逐一觸發斷言在第 175 行)

---

## 已核對、沒發現問題的部分

**[S406] 的「用增量 1 版列舉建舊資料庫」照字面測得出來。** 這不是新手法——`tests/executor/test_queue.py` 已經有完全同構的既有先例:`PHASE3_SCHEMA`(`test_queue.py:74-84`)直接寫死一段舊版 `CREATE TABLE` 字串(舊版 `disposition`/`block_code` 的 CHECK 只認兩三個值),灌進一個乾淨的 SQLite 檔案模擬「舊資料庫」,再用現在的 `InboxStore` 去開它,驗證遷移與重建行為(`test_an_old_inbox_database_migrates_to_four_dispositions_without_losing_data`、`test_only_an_outdated_constraint_also_triggers_a_rebuild`)。`[S406]` 只要照同一手法,把 CHECK 字串換成「含總曝險代碼、不含比例代碼」的版本即可,不需要真的匯入增量 1 某個歷史時點的 `BlockCode` 物件(增量 2 合併後,原始碼裡的 `BlockCode` 本來就只有一份、必然已含比例代碼,不可能匯入出一個「只到增量 1」的列舉物件)。設計文字「用「增量 1 版的列舉」建舊資料庫」若被字面讀成「匯入增量 1 版本的列舉物件」會誤導,但依現有測試慣例,實際做法必然是照 `PHASE3_SCHEMA` 那樣寫死 SQL 字串,這點在既有程式碼裡有明確先例可循,不構成缺陷,只在措辭上可以更精確(可標成 minor,但因為程式碼裡已有現成、無歧義的先例可依循,不另開一條發現)。

**「重跑檢查不會新冒出比例擋下」這個推論對照程式成立。** 兩條重跑路徑——`_after_expiry`(`src/rtb/executor/execution.py:489-515`)與 `_reconcile_not_found`(同檔 `641-666`)——都是呼叫 `precheck(proposal, view)` 拿到 `checked`,再餵給 `_version_changed_or_none(live, checked)`(同檔 `291-296`):只有 `checked is BlockCode.VERSION_CHANGED` 才會把具體代碼往下傳,其他任何 precheck 失敗原因(含將來新增的比例超額)一律變成 `None`,最終落到 `block_code_for_failure(row.code)` 也就是既有的「同一操作先前已失敗」。所以就算比例檢查排進 `precheck()`,在這兩條重跑路徑上也生不出新的比例擋下代碼,行為上是安全的。

再往底層看,「版本沒變就代表預算沒變,所以第一次過的比例第二次一定也過」這個業務假設也對得上 `src/rtb/dsp/store.py`:`_apply`(`store.py:205-210`)裡 `update_budget` 一定同時把 `budget` 換新值、`version` 加一(`Campaign(campaign.id, budget, campaign.status, campaign.version + 1, campaign.name)`),寫回時兩欄也在同一句 `UPDATE` 一起寫(`store.py:419-420`)。也就是說在這個模擬 DSP 裡,`budget` 與 `version` 是綁在一起變的:版本沒變,預算就真的沒變,precheck 的版本檢查(排在比例檢查之前)會先擋下任何「預算被別人動過」的情形,比例檢查用的基準不會被帶偏。

**回退對分析端值域清單無害的部分屬實。** `src/rtb/analyzer/flow.py:293-294` 的 `_BLOCK_CODES` 本來就只認 `not_permitted` 而不是個別的權限類代碼,而合併的動作發生在收件口那一層(`src/rtb/executor/inbox_server.py:133-139` 的 `_PERMISSION_BLOCKS`/`_answered_block_code`)。回退時只要把新代碼繼續留在 `_PERMISSION_BLOCKS` 裡(設計也是這樣說的),分析端這邊確實不用動,也沒有像 `BLOCK_TRIGGERS`/`[S404]` 那種「列舉必須跟某張明列表對等」的斷言會被留下的成員絆倒——這條路徑上找不到類似 F1 的問題。
