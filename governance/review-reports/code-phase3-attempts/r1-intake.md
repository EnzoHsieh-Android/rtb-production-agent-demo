# 代碼審第 1 輪收貨與重現紀錄(2026-09-22)

機械檢查:七份報告 quote-check 全數錨定;資安席報告檔首多一行標題,用 report-normalize --write 只搬格式;refcheck 除資安席一處以省略號縮寫的路徑外全部存在。

## 重現表

| id | 發現 | 重現方式 | 結果 |
|---|---|---|---|
| s1-1 | 重啟恢復撞歷史列上限整批失敗 | 新增測試 test_restart_recovery_still_works_for_a_key_whose_history_is_full,修正前紅 | HIT |
| s2-1 | 全表未結案計數隨歷史線性變慢 | 臨時資料庫灌 10 萬把已結案鍵(30 萬列):舊查詢 89.9 毫秒;改成第 1 列數減終點列數、走部分索引後 3.56 毫秒 | HIT |
| s3-1 | 沒有時區的時間被當本地時間 | 新增測試 test_a_time_without_a_time_zone_is_refused_by_every_write_and_nothing_is_written,修正前紅 | HIT |
| s3-2 | 快照非 JSON 丟錯例外型別 | 新增測試 test_a_snapshot_that_is_not_json_reads_back_as_a_corrupted_row,修正前紅 | HIT |
| s3-3 | 預期序號 True 被當 1 | 新增測試 test_an_expected_sequence_that_is_not_a_plain_integer_is_refused,修正前紅 | HIT |
| s4-1 | 動作沒被單獨驗證 | 拿掉鍵裡的動作後舊測試全綠;新增 test_the_action_alone_changes_the_key_even_with_the_same_requested_change 後同一變異轉紅 | HIT |
| sarch-1 | 不合法轉換例外沒放領域層 | 讀 src/rtb/domain/task_state.py 與 src/rtb/analyzer/task_store.py 對照;新增 test_the_illegal_transition_error_is_defined_in_the_domain_layer | HIT |
| sec-1 | 快照不核對鍵 | 新增測試 test_a_snapshot_whose_content_no_longer_matches_its_key_reads_back_as_corrupted,修正前紅 | HIT |
| x1-1 | 自己開的延遲交易也能寫入 | 新增測試 test_only_a_transaction_from_the_executor_database_entry_is_accepted,修正前紅 | HIT |

## 修正後的變異檢查

逐一拆掉新防護(時區、序號型別、快照對鍵、非 JSON 包裝、重啟恢復豁免、只收交易物件、計數算法、互斥、全表上限、動作進鍵),測試全部變紅。
