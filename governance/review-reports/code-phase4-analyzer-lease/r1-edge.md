severity: minor

## F1 acquire_lease 不驗證 task_id 存在或格式,會替不存在的任務寫租約列
severity: minor
blocking: 否 — 沒有任何合約被違反(commit_step 仍會因 `row is None` 拒收,不會讓幽靈任務寫進 tasks 表),只是多一支孤兒列;作者的 tension 表態(py-memory)已承認租約表無界成長、併入既有歷史表保留期回頭條件,這條孤兒列屬於同一類已被接受的代價,不是新洞。
引句:「目前沒人持有(沒有租約列、目前那一列是放掉列、或已過期)就新增一列取得列、回傳收據」
file: `src/rtb/analyzer/task_store.py:304-316`(`acquire_lease` 全程沒有查 `tasks` 表確認 `task_id` 存在,也沒有像 `create_task` 一樣呼叫 `is_id` 驗格式)。在臨時目錄用真程式碼重現:對從未 `create_task` 過的 `task_id="ghost"` 直接呼叫 `store.acquire_lease("ghost", "someone", NOW)`,回傳合法的 `LeaseReceipt(task_id='ghost', lease_seq=1, owner='someone')`,`task_leases` 表真的多一列 `('ghost', 1, 'someone', '...')`。`advance()` 本身不會踩到這個洞(它先呼叫 `store.latest(task_id)`,任務不存在就在拿租約之前先丟 `TaskNotFound`),只有直接呼叫 `TaskStore.acquire_lease` 的呼叫端(目前程式庫裡沒有,但它是公開方法)才會踩到。標 ⚠ 因為目前沒有真實呼叫路徑會觸發,純粹是防禦性缺口。

## 其餘邊界輸入:未發現具體失敗場景(不列 finding)
- `owner` 傳空字串:`_is_live` 只判斷 `lease[1] is not None`,空字串不是 `None`,行為與任何其他 owner 字串一致;`_append_release` 用 `NULL`(不是空字串)當放掉列的哨兵,兩者不會混淆。實測 `acquire_lease("t2", "", NOW)` 正常取得、正常被 `_holds` 認得。
- `now` 帶非 UTC 時區:`_iso()` 內部 `moment.astimezone(UTC)` 會先轉成 UTC 再格式化,實測 `datetime(2026,9,22,20,0,tzinfo=+08:00)` 與 `datetime(2026,9,22,12,0,tzinfo=UTC)` 格式化結果逐字元相同,到期時間計算正確。
- `LEASE_DURATION` 邊界:實測「剛好在到期那一刻」(`now == acquired_at + LEASE_DURATION`)已經判定為可被接手(`_is_live` 用嚴格 `>`,不是 `>=`),「到期前 1 微秒」仍判定為存活、不可被接手 —— 行為明確、無矛盾,只是測試檔沒有覆蓋剛好那一刻(只測了 `+61s`),但這不是失敗場景,不列 finding。

## 機制拿掉會不會真的紅(各機制逐一變異測試,臨時目錄複製 src/tests、清 __pycache__ 重跑)
所有變異都在 `mktemp -d` 出來的臨時目錄操作,repo 內未修改任何檔案。

1. **完全不取租約(讓 `advance()` 直接呼叫外部介面)**:拿掉 `if lease is None: return row.state` 的 guard 後,`tests/analyzer/test_task_lease.py` 5 支紅(`test_two_real_threads_advancing_the_same_task_pay_for_the_analysis_once`、`test_a_caller_without_the_lease_calls_no_external_interface[*]` ×3、`test_a_holder_that_dies_after_paying_is_analysed_again_once_the_lease_expires`),其餘 9 支綠。有牙齒。
2. **`commit_step` 跳過租約圍籬檢查(拿掉 `if not self._lease_allows(...): return False`)**:2 支紅(`test_an_expired_holder_cannot_commit_after_a_takeover_even_when_the_sequence_still_matches` [S152]、`test_a_commit_without_a_receipt_cannot_write_over_a_live_holder` [S156]),12 支綠。有牙齒。
3. **`_advance_holding` 拿掉取得租約後的重讀比對(`current.seq != row.seq` 那段)**:2 支紅(`test_a_caller_that_acquires_after_another_commit_returns_without_calling_out[*]` [S154] 兩個參數化),其餘 12 支綠;紅的方式是拋出 `_BrokenCollaborator`(因為舊列繼續往下跑到 `_from_analyzing`,`Decide` 回傳 `None`/`NeedsFreshEvidence` 被誤判成合約外型別)——確實是「讀列之後取得之前」交錯被擋下,S154 這支測試有牙齒,量測的是真實的競態序,不是巧合綠。
4. **`advance()` 拿掉例外路徑的 `try/except BaseException` 釋放邏輯**:1 支紅(`test_no_progress_or_an_in_process_error_releases_the_lease` [S155]),13 支綠。有牙齒。
5. **`_release_keeping_the_original_error` 把 `except (sqlite3.Error, DatabaseBusy)` 放寬成 `except Exception`**:14 支全綠,S160(`test_a_failed_release_never_masks_the_original_error`)沒抓到——但這支測試按設計文件([[Projects/RTB_Phase4佇列與重新投遞_計劃]] S160 原文「放掉本身遇到資料庫錯誤」)本來就只承諾資料庫錯誤這條窄路徑,不承諾「release_lease 丟任何例外都不蓋原例外」;另外用探針腳本確認:即使 `release_lease` 丟一個非資料庫例外(如 `TypeError`,模擬 release_lease 自己的程式錯誤),Python 的隱式例外鏈結(`__context__`)仍會把原例外保留在 traceback 裡(顯示「During handling of the above exception, another exception occurred」),不是完全消失。判定:這不是牙齒缺口,是測試範圍精準對齊規格,不列 finding。

## 並行測試時序假綠/偶發紅
- `tests/analyzer/test_task_lease.py` 整支(含 S150 真雙執行緒、S152 過期接手、S153 真子行程猝死、S159 遲來放掉)連跑 30 次,清 `__pycache__` 重跑,全數 `14 passed`,0 失敗、0 flaky。
- S150、S152、S159 用 `threading.Event`/`Barrier` 卡住持有者直到另一方真的試過才放行,不是靠 `sleep` 賭時序;S154 直接把交錯寫死在 monkeypatch 裡(不靠真並行的隨機性),邏輯上不可能偶發假綠。

## S153 子行程測試在 CI 上能不能跑
`env={"PYTHONPATH": SRC}` 整個蓋掉環境變數(不含 `PATH`/`HOME`),用 `sys.executable`(絕對路徑,不查 `PATH`)開子行程。這不是這支測試新引入的風險寫法——`tests/executor/test_crash_recovery.py:153` 與 `tests/dsp/test_store.py:411` 已經用同一種 `env={"PYTHONPATH": ...}` 模式開子行程,是既有、已經在跑的慣例,不是新增的未知數。判定:未能找到具體失敗場景,不列 finding。

## 既有測試(test_flow.py 等)有沒有悄悄改變語意卻仍綠
- `tests/analyzer/test_flow.py` 全部 50 支在改動後仍綠,包含既有並行不變量測試 `test_two_concurrent_advance_calls_on_the_same_task_never_both_commit_conflicting_outcomes`(對應 [[Systems/分析行程流程與檢查點]] 的 INVARIANT「每一步都要落地成新的歷史列」)。
- `advance()` 對外簽章沒變(仍是 `store, task_id, evidence_source, decide, submit, now, before_commit=None`),租約只多寫/放掉 `task_leases` 表的列,不動 `tasks`/`evidence` 表,既有測試斷言的是 `history()`/`latest()`,不會被租約表的變動影響——這解釋了為什麼既有測試全綠不是巧合,是因為新機制刻意寫在另一張表裡、不動既有讀取路徑。
- `tests/analyzer` 全套(116 支)在改動後跑過,全綠。

## 圖譜鏡頭:固定席逐條判
- [[Systems/執行迴圈]] ★INVARIANT★(F1/F2,牽連檔 attempt_store.py/execution.py/runner.py):不影響。這份 diff 只動 `src/rtb/analyzer/task_store.py`、`src/rtb/analyzer/flow.py`、`tests/analyzer/test_task_lease.py`,不在這份筆記的牽連檔清單裡,執行側的租約/對帳邏輯完全沒被碰。
- [[Systems/外部寫入嘗試紀錄]](牽連檔 attempt_store.py、runner.py):不影響,理由同上。
- [[Systems/Mock-DSP]] ★INVARIANT★(牽連檔 execution.py、runner.py、tests/executor/fakes.py):不影響,理由同上。
- [[Systems/任務流程領域模型]] ★INVARIANT★(間接相依,牽連檔 attempt_store.py):不影響 domain 層本身(`rtb/domain/task_state.py` 未變動),`flow.py` 仍呼叫既有的 `transition()`/`can_transition()` 做合法性判斷,租約只是在呼叫這些函式之前加一道「誰能進來跑」的閘,不改變狀態機規則本身;`attempt_store.py` 不在本次牽連檔內。
- [[Systems/共用行程基礎]] ★INVARIANT★(間接相依,牽連檔 execution.py、runner.py):不影響,理由同上,本次改動不碰故障注入或共用 HTTP 用戶端。
- [[Systems/分析行程流程與檢查點]] ★INVARIANT★(間接相依;但實際讀該筆記,它自己 `about_code` 就列了 `src/rtb/analyzer/task_store.py`、`src/rtb/analyzer/flow.py`——即這份 diff 改的兩支檔真正的家,impact 工具這裡標的牽連檔卻是 execution.py/runner.py,跟筆記正文不一致,懷疑是圖譜索引沒更新,值得之後核一下,但不影響本次審查判斷):**相關,已驗證**。筆記裡的 INVARIANT「每一步都要落地成新的歷史列;並行推進同一個任務時只有一個寫得進去」由 `test_two_concurrent_advance_calls_on_the_same_task_never_both_commit_conflicting_outcomes` 綁定,本次改動後仍綠(見上一節);筆記正文也已經寫了新的 RULE(`[since:2026-09-23] [retire:...]` 齊全)描述租約表只增不改、取得/放掉各一列,跟這份 diff 的 `SCHEMA`/`acquire_lease`/`release_lease` 逐字對得上,沒有落差。
- [[Systems/提案收件口]] ★INVARIANT★(間接相依,牽連檔 attempt_store.py、execution.py、runner.py):不影響,理由同執行迴圈一節。

## 設計依據交叉核對(S150–S160)
對照 `Projects/RTB_Phase4佇列與重新投遞_計劃.md` 的「## 增量 3b 設計」節:S150–S160 十一條逐條都能在 `tests/analyzer/test_task_lease.py` 找到同名綁定測試且本次跑綠;S158「過期沒人接手照樣寫得進去」、commit_step 不核對到期只核對收據號碼,計劃文件裡有明寫「與執行側 [S101] 的差異與撤回條件」(`RULE`/正文段落,非孤立斷言),跟程式行為一致,不是程式碼與筆記打架的情況。

總結:severity 最高 minor,blocking 0 條(minor 1 條、共 1 條 finding),其餘查核項目均未能重現具體失敗場景或已驗證與筆記/規格一致。
