# 代碼審第 2 輪收貨與重現紀錄(2026-09-22)

審材:第 1 輪修正的 diff(r2-snapshot.patch)。四席報告 report-normalize 皆已正規化、quote-check 全數錨定。

## 重現表

| id | 發現 | 重現方式 | 結果 |
|---|---|---|---|
| s1-1 | 舊交易物件在同一連線的下一筆交易裡復活 | 新增 test_a_finished_transaction_object_stays_dead_even_when_the_connection_opens_another,修正前紅 | HIT |
| s1-2 | 子類別繞過原始碼掃描 | 新增 test_a_subclass_of_the_transaction_object_is_not_accepted,修正前紅 | HIT |
| sarch-1 | 時區檢查寫了第三份 | 讀 src/rtb/domain/evidence.py 與 src/rtb/domain/proposal.py 對照;新增 test_the_shared_time_zone_check_is_the_one_the_attempt_store_uses | HIT |
| sec-1 | 重啟恢復一把壞鍵拖垮整批且每次重演 | 新增 test_restart_recovery_skips_an_unreadable_key_and_still_recovers_the_healthy_ones(連跑兩次),修正前紅 | HIT |
| sec-2 | 交易物件不驗來源、換別名就繞過掃描 | 新增 test_a_transaction_object_cannot_be_built_without_the_entry_s_issuer(含別名),修正前紅 | HIT |
| x1-1 | 交易物件可偽造、舊物件復活、直接插第二列終點讓計數變負 | 前兩者同 s1-1、sec-2;負數部分新增 test_a_negative_unresolved_count_is_treated_as_a_corrupted_table,修正前紅 | HIT |

## 修正後的變異檢查

逐一拆掉(建構憑證、完全型別、作廢旗標、入口作廢、負數計數、逐鍵隔離、共用時區檢查、重啟恢復豁免、快照對鍵),測試全部變紅。
