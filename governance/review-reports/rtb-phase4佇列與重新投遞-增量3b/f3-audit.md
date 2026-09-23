# 獨立審計結果:事故 F3 預告合約轉正

## 背景确认

合約原文與 `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase4佇列與重新投遞_計劃.md` 第 484 行「建議措辭」逐字相符,是使用者 2026-09-23 覆核同意的版本(已修正過原句兩處說過頭的地方)。宣稱要綁的 10 支測試,與同一篇計劃第 487 行「事故 F3 轉正」步驟 2 列出要補綁的測試清單完全一致(S150、S121、S125、S126、S130、S151、S152、S153、S154、S159)。這代表這次轉正確實照著計劃在走,不是臨時拼湊證據。

## 一、是不是真合約

句子每個詞在程式裡都有具體對應,不是空話:

- **任務識別維持不變**:執行側是收件表的 `(task_id, revision)`;分析側是 `task_id`。`tests/executor/test_crash_recovery.py::test_f3_a_message_delivered_twice_applies_once` 直接斷言 `world.proposals() == [("t1", 1, "handed_off", None, 2)]`——同一個 task_id/revision,只有投遞次數(deliveries)從 1 變 2。
- **狀態轉換合法**:指 `src/rtb/domain/attempt.py` 的 `can_transition` 表,`src/rtb/executor/attempt_store.py:transition()` 在每次寫入時就地強制;測試用同一張表逐對核對實際寫入的序列,不是自訂了一套平行的判準。
- **同時推進 / 持有有效租約**:對應 `src/rtb/analyzer/task_store.py` 的 `task_leases` 表(只增不改,取得/放掉各新增一列,「目前那一列」序號最大者才算持有,`_is_live` 判到期)。`test_two_real_threads_advancing_the_same_task_pay_for_the_analysis_once` 用兩條真執行緒、各自資料庫連線、進交易前的柵欄逼真正同時出發,不是模擬。
- **執行閘**:對應 `src/rtb/executor/execution.py:precheck()`,尤其 `view.version != proposal.campaign_version_observed → VERSION_CHANGED`,是生產路徑上真正擋寫入的檢查,不是測試專用鉤子。

括號裡承認的兩種重做情況(持有者寫入前當機 / 一步做超過租約被接手)寫得準確,跟設計文件〈保證什麼、不保證什麼〉一節逐句對得上:執行側因為有冪等鍵去重與收據條件寫入,重複投遞不重複副作用是無條件保證;分析側因為沒有續租、猝死放不掉租約,只能保證「同一時間只有一個有效持有者會花錢」,當機後重做與跨租約接手兩種情況會真的花兩次錢——合約措辭誠實地把這個差異標了出來,沒有把分析側的較弱保證偷渡成跟執行側一樣強。

結論:是真合約,可機械驗證,措辭準確。

## 二、10 支測試夠不夠格

逐句對應下來,每個子句都有測試守、也都是斷言直接命中(不是間接推論):

| 子句 | 守護測試與斷言 |
|---|---|
| 任務識別不變、重複投遞不重複副作用(訊息面) | `test_f3_a_message_delivered_twice_applies_once`:同一 task_id/revision、`world.counts()["write"] == 1`、`budget() == (150, 1)` |
| 狀態轉換合法 | 同一支測試:`assert all(can_transition(a, b) for a, b in itertools.pairwise(states))`,用生產同一張表核對 |
| 重做分析不繞過執行閘(目標不同) | `test_f3_a_reanalysed_revision_still_passes_through_the_execution_gate`:第二版被 `version_changed` 擋下,DSP 只收到 1 次寫入 |
| 「已完成不重做」不是執行閘的證據(刻意分開列) | `test_f3_a_revision_with_the_same_operation_is_settled_from_the_finished_record`:走的是既有紀錄結案路徑,不是執行閘 |
| 真的同時投遞只處理一次 | `test_two_live_workers_process_a_message_once`(執行側,兩條真執行緒 + 柵欄) |
| 同一時間只有一個持有有效租約的一方花分析費用 | `test_two_real_threads_advancing_the_same_task_pay_for_the_analysis_once`:`len(calls) == 1` |
| 沒租約的一方完全不呼叫外部介面 | `test_a_caller_without_the_lease_calls_no_external_interface`:三個介面呼叫次數都是 0 |
| 過期被接手後,舊持有者寫不進去(即使序號仍對) | `test_an_expired_holder_cannot_commit_after_a_takeover_even_when_the_sequence_still_matches` |
| 一步超過租約被接手 → 重分析一次 | 同一支測試:`len(decide_a.calls) + len(decide_b.calls) == 2` |
| 持有者寫入前當機 → 到期後才重分析一次 | `test_a_holder_that_dies_after_paying_is_analysed_again_once_the_lease_expires`(真的用子行程 `os._exit(9)` 猝死,不是丟例外模擬) |
| 讀完列後被別人搶先完成 → 不呼叫外部、回新狀態 | `test_a_caller_that_acquires_after_another_commit_returns_without_calling_out` |
| 遲來的放掉不會偷走接手者的租約 | `test_a_late_release_from_a_replaced_holder_writes_nothing` |

沒有發現沒人守的子句。唯一值得注意但不構成缺口的地方:分析側「同一則訊息穩定對應同一個任務編號」這半句,設計文件自己承認分析行程目前沒有訊息入口,所以合約措辭已經把範圍改寫成「同一個任務被同時推進」,刻意迴避了這個還不存在的機制,並留了 2026-12-31 的回頭條件——這是誠實地縮小範圍,不是漏測。

## 三、綠燈是否可能掩蓋合約不成立(改壞後重跑這 10 支)

把 `src`、`tests` 整份複製到臨時目錄後,只跑這 10 支測試名稱,做了 6 組改壞實驗:

1. 拔掉執行閘版本檢查(`precheck` 裡 `VERSION_CHANGED` 判斷失效)→ `test_f3_a_reanalysed_revision_still_passes_through_the_execution_gate` **變紅**。
2. 讓分析側租約「永遠沒人持有」(`_is_live` 恆回 False)→ 6 支變紅,包含 `test_two_real_threads_advancing_the_same_task_pay_for_the_analysis_once`(兩邊都真的花了錢)。
3. 只拔掉提交時的收據核對(`_lease_allows` 對帶收據一律放行)→ 被接手測試變紅(這次是撞到 `task_leases` 主鍵衝突連帶炸掉交易,結構上的第二道防線也生效,但測試本身依然紅,沒有假綠)。
4. 把 `_holds` 改成「只要有人持有就算我的」→ 2 支變紅,同樣是紅,不是假綠。
5. 拿掉分析側呼叫外部介面前的租約門檻(沒租約也照樣呼叫)→ 5 支變紅(含直接接住「沒租約不該呼叫外部介面」那支)。
6. 拿掉執行側「這把鍵已有嘗試紀錄就直接確認、不重讀 DSP」的去重(`receive()` 裡 `existing is not None` 判斷失效)→ `test_f3_a_revision_with_the_same_operation_is_settled_from_the_finished_record` **變紅**,證明「已完成不重做」這個子句是真的被這支測試守著,不是掛名。

六組刻意改壞的實驗全部讓對應的測試變紅,沒有出現「程式明顯違反合約、這 10 支卻全綠」的情況。原始專案檔案未被更動(只改臨時目錄的複本)。

## 結論

`結論: 同意轉正`
