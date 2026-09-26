---
type: verification
status: pass
date: 2026-09-26
valid_under: 僅 Phase 14 增量 2a 本工作樹（含代碼審 r1 修正）；全套測試（含綁本機埠的 HTTP 測試）、宣稱驗證器與 72 筆錄製重播；未跑午夜前後的真實完整 F1–F7 展示
revalidate_when:
  - 在 UTC 午夜前後實跑完整 F1–F7 展示時
  - DSP 日期桶、金額格式、讀取白名單或評估提示與錄製鍵再變動時
tags:
  - type/verification
  - status/pass
plan_refs:
  - "[[Projects/RTB_Phase14正式規則照九條判斷_計劃]]"
---
# Phase14增量2a離線驗證

範圍：[[Projects/RTB_Phase14正式規則照九條判斷_計劃]] 增量 2a 的模擬 DSP 資料模型、種子、讀取層與 Phase 13 評估資料相容。正式決策、分步蒐證、政策版本與送件流程尚未接線，本紀錄不替它們驗收。

## 紅綠與離線結果

- [S1414] 逐日對 1d/7d 的 Fraction 精確核對、整數分儲存與固定兩位字串：先紅後綠；test_daily_rows_must_match_the_longer_windows、test_amounts_are_stored_as_integer_cents_and_returned_as_fixed_decimal_strings 通過。
- [S1415] 正常與種子預算操作單源、後續減額不擠掉最新加額、D±3 與後段未滿為空、舊資料遷移：先紅後綠；test_past_adjustments_include_normal_budget_operations、test_old_adjustment_seed_tables_migrate_to_the_single_operation_source 通過。正式規則的三天切點留待後續子增量。
- [S1422] 50 列上限與完整七日摘要：先紅後綠；test_bounded_history_preserves_recent_budget_changes 通過。
- [S1423] 最近加額 D±3 桶超過 30 日後仍保留：先紅後綠；test_latest_raise_daily_buckets_survive_rolling_retention 通過。
- [S1425] 帶時區時間戳、未滿三天讀取及評估 72 筆：先紅後綠；test_adjustment_timestamp_preserves_recorded_receipts、test_adjustment_timestamp_preserves_recorded_receipts_for_all_72_cases 通過。以入庫錄製離線重播 72 筆，缺錄製 0、批次問題 0，未呼叫即時模型。
- [S1426] 午夜後冪等物化剛完成的 UTC 日桶、同交易更新 1d/7d：先紅後綠；test_demo_daily_rollover_preserves_full_windows 通過。完整 F1–F7 午夜前後情境需能綁本機網路埠，本環境未跑。

其他離線檢查：tests/domain + tests/dsp/test_store.py 408 過；相關 DSP 種子純測試 13 過；評估讀取與收據 2 過；ruff check src tests、七支變動來源檔 mypy、git diff --check 均通過。六篇改動圖譜筆記逐篇 lumos lint 為 0；lumos doctor 0 issues，其他既有提醒仍在。五份 claims 清單依改動重算 scope/harness 雜湊，policy/evidence 語意未變；claim_hashes 再查沒有過期雜湊。全套與宣稱驗證器未跑，因它們會啟動需綁埠的測試。

## 待驗

可綁埠環境須跑 DSP 與分析端 HTTP 測試、完整 F1–F7 午夜前後情境、全套與宣稱驗證器。增量 2 後續須接正式九條規則、分步蒐證、政策版本與完整決策時鐘三天切點。

## 代碼審 r1 修正（2026-09-26）

九席報告（governance/review-reports/code-phase14-inc2a/r1-*）全數處理，方向依協調者裁定，細節見 [[Projects/RTB_Phase14正式規則照九條判斷_計劃]]〈2a 代碼審 r1 修正〉。新增或改寫的測試，先以 `git archive HEAD` 的原程式搭現行測試跑，24 支全紅（多數紅在症狀本身：讀取後保留筆數 36≠11、歷史形狀與快照、七日溢位 OverflowError、舊 REAL 1e30 升級 InvalidOperation、舊加額 404、Unicode 數字與 14 位金額被收、負數整份無結果、評估列時間不一致、稽核表 UPDATE；少數紅在新介面參數 `template`／`store_clock`），修後全綠：

- 讀取不寫、鎖內可讀、一次時鐘：test_reads_never_write_and_still_work_while_the_write_lock_is_held、test_one_read_uses_one_clock_reading_across_midnight、test_writes_persist_days_after_reading_the_newest_day_under_the_write_lock、test_the_daily_and_past_adjustment_endpoints_are_get_only_and_idempotent（改寫成讀取前後整庫不變）。
- 固定時鐘與不照抄：test_a_missing_first_day_seeds_an_empty_one_day_window、test_seeded_daily_and_adjustment_data_agree_with_windows_and_history、test_a_completed_day_without_a_template_has_no_data_instead_of_a_copy、test_demo_daily_rollover_preserves_full_windows、test_latest_raise_daily_buckets_survive_rolling_retention、test_a_seven_day_window_without_any_data_is_not_found。
- 金額與溢位：test_fixed_amount_strings_have_one_definition_everywhere、test_negative_amounts_are_not_compared_across_windows、test_the_basic_hour_read_keeps_exact_cents_in_the_receipt、test_seven_day_totals_never_overflow_into_a_server_error、test_amounts_are_stored_as_integer_cents_and_returned_as_fixed_decimal_strings、test_legacy_amounts_that_no_longer_fit_do_not_stop_the_dsp_from_starting、test_an_extreme_amount_never_leaves_the_task_stuck_collecting_evidence（1e300 在邊界拒收；「不卡蒐證」改以能存的最大金額驗）。
- 舊加額缺前值與稽核表：test_a_legacy_raise_without_a_provable_budget_before_is_reported_as_missing_evidence、test_a_legacy_adjustment_without_budget_before_is_read_as_missing、test_audit_tables_are_only_ever_inserted_into。
- 歷史：test_bounded_history_preserves_recent_budget_changes、test_an_untruncated_history_keeps_the_existing_shape、test_history_summary_and_rows_come_from_one_snapshot、test_a_history_summary_that_contradicts_its_rows_is_invalid。
- 評估：test_each_past_adjustment_has_the_same_moment_as_its_history_row；72 筆收據雜湊 0f2e89c3…d562 與 SYSTEM_PROMPT 雜湊不變（test_adjustment_timestamp_preserves_recorded_receipts_for_all_72_cases）。

實跑結果（2026-09-26，本機可綁埠）：

- 全套 `pytest -q -p no:cacheprovider`：3305 passed、1 skipped、0 failed（892 秒）。修前兩次全套紅的 test_ai_judge 兩支、test_narrate 一支這次綠，修前失敗訊息是呼叫途中收到 SIGTERM，屬當次執行環境被中斷，非本分支邏輯；test_the_suite_never_touches_the_real_ledger 這次也綠（修前紅是跑測期間一鍵展示伺服器寫真帳本，另見 Issues，不在本次修）。
- `ruff check src tests`、`mypy`（94 檔）通過；`tools/verify_claims.py claims/`：5 條宣稱、78 支證據測試通過（五份清單依改動重貼雜湊，宣稱語意未變）。
- `python -m rtb.eval.investigation_eval --verify`（空 HOME、只重播、未呼叫模型）：驗收通過。

## 代碼審 r2 修正（2026-09-26）

五份 r2 報告（r2-鏡頭A、鏡頭B、外家finder、架構對齊 clean、資安 clean）中需修的條目依協調者指示處理：

- 鏡頭A 1：溢位測試的 HTTP 段改在同一行程起 DSP 並注入固定時鐘；把測試檔的 NOW 往前推 5 天（等同真實日期到 10/01）修前這支紅、修後綠。另加時鐘守衛（本檔自動夾具：未注入時鐘的儲存層拿到 NOW 後 400 天）；在副本拿掉兩支測試的時鐘注入，兩支當場翻紅，證明守衛抓得到這類問題。全檔掃過，唯一還用真時鐘的是子行程那支，斷言與日期無關，已在說明寫明。
- 外家 finder 1、鏡頭B 2：test_a_truncated_history_summary_must_account_for_every_operation、test_a_truncated_history_summary_cannot_undercount_recent_rows 修前紅（60 筆說成 50 筆仍收；讀取收口沒有時刻參數），修後綠。
- 鏡頭B 1：test_seven_day_totals_never_overflow_into_a_server_error 改寫為日桶每天上限、七天合計經 HTTP 由讀取層收下；修前紅（沒有每天上限）。
- 鏡頭A 2、鏡頭B 3、架構 r1 第 3 條：只改圖譜（Mock-DSP 邊界、分析行程白名單 RULE、計劃增量 2b 待辦）。

實跑（2026-09-26）：全套 3306 passed、1 skipped、1 failed——失敗的是 test_the_suite_never_touches_the_real_ledger：跑測期間本機有一個非本次啟動的一鍵展示伺服器（`rtb.demo.server --work-dir /tmp/rtb-demo`，10:55 起）在跑，帳本大小在跑測期間變了，即 Issues〈錄製模式的原因假說寫進真帳本〉記的已知原因，非本分支造成（上一輪同一支在沒有展示伺服器時是綠的）。ruff、mypy 通過；宣稱驗證器 5 條、78 支證據測試通過；72 筆錄製重播（空 HOME、只重播）驗收通過。

## 代碼審 r3 修正（2026-09-26，末輪）

- 鏡頭A 1／外家 finder 3：截斷歷史近期核對的時鐘先後說明更正，並留 15 分鐘容忍；test_a_normal_read_delay_near_the_three_day_line_is_not_invalid 修前紅、修後綠。
- 鏡頭A 2：test_only_the_seven_day_check_catches_an_undercounted_week；在副本拿掉「最近 7 天」核對，這支（與上一支）翻紅。
- 鏡頭A 3／外家 finder 2、外家 finder 1：test_a_legacy_day_whose_every_value_is_over_the_daily_limit_becomes_no_data、test_stored_date_keyed_buckets_over_the_daily_limit_read_as_missing 修前紅、修後綠。
- 架構對齊 1：`DAILY_MAX_COUNT` 改用 `SQLITE_INTEGER_MAX`（純重構，既有測試守）。

實跑（2026-09-26）：全套 3310 passed、1 skipped、1 failed——仍是 test_the_suite_never_touches_the_real_ledger：失敗訊息顯示真帳本在 17:40:11 被寫（全套 17:39 開跑、本分支沒有碰模型帳本的程式），同時段本機有非本次啟動的一鍵展示伺服器在跑（r2 時已記），屬 Issues〈錄製模式的原因假說寫進真帳本〉的已知原因。ruff、mypy 通過；宣稱驗證器 5 條、78 支證據測試通過；72 筆錄製重播（空 HOME）驗收通過。

REVISIT:2026-10-10 在 UTC 午夜前後各實跑一次完整 F1–F7 展示，確認跨日後七個完整日與可提案（[S1426] 的展示那半）。
