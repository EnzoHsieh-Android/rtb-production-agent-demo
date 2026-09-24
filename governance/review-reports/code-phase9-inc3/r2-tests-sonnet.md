severity: clean
對 r2-delta-tests.patch 的新測試在 /tmp 副本逐條做變異(拿掉/改鬆對應實作關鍵一行),24 種變異全數翻紅,無一漏網;既有測試被改動之處沒有找到放寬斷言。

以下為完整報告全文:

severity: clean

# 審查報告:Phase 9 增量 3 第 2 輪測試殺傷力(governance/review-reports/code-phase9-inc3/r2-delta-tests.patch)

在 `/tmp/p9i3-review`(複製自 /Users/enzo/rtb-p9i3 的 phase9-inc3-fix 分支 HEAD,未動原始 repo)做變異實驗:對照 r2-delta-src.patch,把每一條 r1 折入的修法對應的實作關鍵一行拿掉或改鬆,跑 `tests/dsp/test_commit_order.py`、`tests/ops/test_side_effects.py`、`tests/ops/test_slo.py`、`tests/executor/test_attempt_store.py` 等相關測試,看會不會翻紅。

## 稽核金鑰三種拒絕(401/403/503)

引句:「assert _call(world, path)[0] == 401」

- 拿掉 503(`is_usable_key` 檢查改成 `if False`)→ 翻紅(`test_the_operation_list_endpoints_require_the_audit_key` 失敗,實際觸發 `assert expected is not None` 斷言錯誤)。
- 拿掉 401(`presented is None` 檢查改成 `if False`)→ 翻紅。
- 拿掉 403(`hmac.compare_digest` 檢查改成 `if False`)→ 翻紅(`assert 200 == 403`)。

三種拒絕都被同一支測試準確咬住,沒有漏測的分支。

## 游標上限

引句:「assert (status, body.get("error")) == (400, "invalid_cursor")」

- 拿掉 `value > SQLITE_INTEGER_MAX` 上限檢查 → 翻紅(SQLite 端直接丟 `OverflowError`,測試失敗)。
- 把 `isdecimal()` 退回舊的 `isdigit()`(重新引入上標數字誤收的原始漏洞)→ 翻紅(`int("²")` 丟 `ValueError`)。

`test_a_cursor_must_be_a_plain_decimal_within_the_integer_range` 兩個子問題都咬得住。

## 逐條隔離

引句:「except Exception as exc:  # 一條讀不到不拖垮另外五條」

- `slo.evaluate()` 拿掉逐條 try/except、改成不隔離直接呼叫 → 翻紅(`test_one_unreadable_slo_does_not_hide_the_others`,`RuntimeError` 直接穿出)。
- `duplicates()` 把 `_tally_duplicates` 呼叫移出 try 區塊(重現 r1 資安-2/side-2 原始漏洞:逐鍵查 DSP 失敗沒被接住)→ 翻紅(`test_a_failed_lookup_of_a_sibling_key_makes_duplicates_missing`)。

## 鍵一致性(snapshot_matches_key)

引句:「assert firsts[mismatched].proposal == PROP and not firsts[mismatched].snapshot_matches_key」

- `attempt_store._snapshot_matches_key` 改成恆真 → 翻紅(`test_a_first_row_whose_snapshot_does_not_match_its_key_is_never_good`)。
- `side_effects._scope_violations` 拿掉 `not first.snapshot_matches_key` 判斷 → 同時打紅該測試與 `test_unverifiable_writes_are_reported_apart_from_the_denominator`。
- `_tally_duplicates` 拿掉 `elif first.snapshot_matches_key` 判斷(讓毀損快照也能算好事件)→ 翻紅。

## 其餘一併變異驗證,同樣全數翻紅

- `expected_version=None` 誤判證實違規(reintroduce 資安-1 類問題)→ `test_unverifiable_writes_are_reported_apart_from_the_denominator` 翻紅。
- 同鍵兩筆防禦性核對(tests-3)拿掉 `seen_keys` 判斷 → `test_the_same_key_committed_twice_is_a_harmful_duplicate` 翻紅。
- DSP 提交時間讀不懂改成裸丟 `ValueError`(不轉 `DspUnreadable`)→ `test_a_malformed_commit_time_from_the_dsp_is_unreadable_not_a_crash` 翻紅。
- `commit_text` 統一 UTC 寫法還原成原樣存(重現 x1-1)→ `test_dsp_commit_times_are_stored_in_one_utc_form` 翻紅。
- `approval_uses_for_query` 拿掉新增的依鍵索引(migration 那行刪掉)→ `test_approval_uses_for_a_batch_of_keys_uses_the_key_index` 翻紅(查詢計畫變回 `SCAN`)。
- 結果不明對帳「期限當刻轉人工」邊界(x1-3)退回舊的嚴格 `<` → `test_deadline_based_slis_are_fixed_once_the_window_has_passed` 翻紅。
- DSP 窗口讀取邊界(`>=` 改 `>`)→ `test_the_dsp_operation_window_reader_is_bounded_and_read_first` 與 `test_a_page_boundary_between_equal_commit_times_reads_each_operation_once`(tests-5)同時翻紅。
- `SloStatus.stable` 寫死 `True`、`slo.run()` 拿掉 `EXIT_UNSTABLE` 出口 → `test_an_unstable_cross_database_read_is_reported` 翻紅。
- CLI 入口 `audit_key` 讀取被拿掉 → `test_the_command_line_reports_fixed_exit_codes` 翻紅。
- 端到端交給執行 `<=` 改 `<`(tests-4 原始問題)→ `test_end_to_end_handoff_sli_is_event_based` 翻紅(3,4)→(2,4)。
- `slo.run()` production lambda 參數對調 `since`/`until`(tests-1 原始問題)→ `test_the_command_line_reports_fixed_exit_codes` 翻紅。
- `unauthorized()` 拿掉 `missing=True`(tests-2 原始問題)→ `test_the_dsp_being_unreachable_is_missing_not_zero` 與 `test_a_malformed_commit_time_from_the_dsp_is_unreadable_not_a_crash` 同時翻紅。

共 24 種變異,全部翻紅,無漏網。r1 測試席指出的 5 條(tests-1 到 tests-5)在這輪都已補上能實際咬住的測試,包括最關鍵的「slo.run() 真正被執行的 lambda 從未被測過」與「DSP 讀不到時 missing 旗標」兩條 major。

## 既有測試修改處查核(未發現放寬)

逐條核對 r2-delta-tests.patch 裡對既有測試的改動:

- `tests/ops/test_side_effects.py::test_a_second_commit_of_the_same_proposal_is_a_harmful_duplicate`:把最後一組手選鍵 `"k1-"+"e"*64` 改成 `operation_key(alone)`,是因為新的鍵一致性檢查要求第一列快照要算得回它自己的鍵;原本手選的鍵本來就對不上 `alone` 這份提案算出的鍵,新檢查生效後會被判成無法核對而非好事件,測試作者改用正確算出的鍵讓情境維持「快照對得上鍵的正常第一列」的原意。斷言數值 `(1, 3, 1)` 不變,另外加了 `tally.stable is True` 的新斷言,屬強化不是放寬。
- `test_the_dsp_operation_window_reader_is_bounded_and_read_first`、`test_a_full_operations_page_fits_the_client_response_limit`:改動只是幫 `se.get_json`/`se.read_dsp_window` 加上新增的 `AUDIT` 參數(函式簽章變了必須跟著改),斷言邏輯未動。
- `tests/ops/test_slo.py::test_deadline_based_slis_are_fixed_once_the_window_has_passed`:新增 `kE`(期限當刻轉人工)資料,斷言從 `(1,3)` 改成 `(1,4)`、`bad_at` 多一筆,數值變化完全對應新增的一筆壞事件,是擴大覆蓋而非放寬。
- `test_end_to_end_handoff_sli_is_event_based`:新增 `e1`(剛好 120 秒)資料,斷言從 `(2,3)` 改成 `(3,4)`,同樣是新增好事件樣本後的正確跟隨,已用上面的變異驗證這條邊界真的被守住。
- `test_a_single_zero_target_violation_fires_for_the_whole_period`:只新增兩段斷言(`missing` 與已證實違規優先、`missing` 又無壞事件時回 `None`),沒有刪改原本任何一行斷言。

沒有發現任何一處把 `==` 改鬆成範圍比對、拿掉檢查項、或放寬容差的情況;所有既有測試的改動都是配合新增欄位/參數的必要同步,或是新增更嚴格的斷言。

## 材料
- governance/review-reports/code-phase9-inc3/r2-delta-tests.patch
- governance/review-reports/code-phase9-inc3/r2-delta-src.patch
- governance/review-reports/code-phase9-inc3/r1-tests-sonnet.md、r1-intake.md(對照第 1 輪指出的 tests-1 到 tests-5 是否已補上有效測試)
- 變異實驗:`/tmp/p9i3-review`(複製自 /Users/enzo/rtb-p9i3,實驗結束已清除,未修改原始 repo 任何檔案)
