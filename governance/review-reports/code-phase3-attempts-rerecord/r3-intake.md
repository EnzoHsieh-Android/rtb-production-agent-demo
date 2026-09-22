# 代碼審第 3 輪收貨與重現紀錄(2026-09-22)

審材:第 2 輪修正的 diff(r3-snapshot.patch)。三席報告:修正後回歸席檔首標題用 report-normalize --write 只搬格式;quote-check 全數錨定。高風險代碼審上限 3 輪,這是最後一輪。

## 重現表

| id | 發現 | 重現方式 | 結果 |
|---|---|---|---|
| x1-1 | 重複終點列被未結案鍵抵銷 | 新增 test_the_database_itself_refuses_a_second_terminal_row_for_one_key;拿掉唯一限制的變異會紅 | HIT |
| x1-2 | 重啟恢復只接 ValueError | 參數化 test_restart_recovery_skips_an_unreadable_key_and_still_recovers_the_healthy_ones 加二進位時間,修正前 blob 案例紅 | HIT |
| s1-1 | 時區檢查只收斂三分之一 | 新增 test_no_domain_module_defines_its_own_time_zone_check,修正前紅 | HIT |
| sec-1 | 同 x1-1 | 同上 | HIT |
| sec-2 | 同 x1-2 | 同上 | HIT |
| sec-3 | 收件口模組內改用普通交易仍被放行 | 新增 test_the_only_place_that_issues_a_transaction_opens_it_with_the_write_lock;把入口改成普通開始交易的變異會紅;從專案外刻意匯入私有憑證屬刻意繞過,在「防忘記、不防刻意繞過」威脅模型之外 | HIT |

## 修正後驗收

- 變異檢查:唯一限制、讀取毀損型別、入口改用普通交易、共用時區檢查,四個變異全部變紅。
- 發現 sec-1、sec-2、sec-3 的資安席續談驗收(r3-sec-verify.md):三條都用原 PoC 重跑確認修好;第 3 條註明執行期仍不辨交易模式,由原始碼層測試攔下。
- 修正後回歸席(r3-s1.md)用原 PoC 重跑確認第 2 輪四個 major 皆已修好。
