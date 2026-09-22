severity: clean

第 1 條:已修好,重跑結果——同一把鍵再寫第二列終點列時,`attempts_one_terminal_per_key` 部分唯一索引直接讓 SQLite 丟出 `sqlite3.IntegrityError: UNIQUE constraint failed: attempts.key`,寫不進去;搭配另一把仍在 `in_flight` 的正常鍵重跑,`unresolved_count(campaign="c1")` 正確回報 `1`(不再被抵銷成 0),第三把鍵 `begin()` 被 `CampaignLocked` 擋下,S6 互斥沒有再被繞過。

第 2 條:已修好,重跑結果——把 `written_at` 改成 `bytes`(`b"bad"`)重現原本會逃過 `except ValueError` 的 `TypeError`,現在 `_row()` 改成 `except (ValueError, TypeError, AttributeError)` 後,`recover_in_flight` 正常回傳 `Recovery(moved=(健康鍵,), unreadable=(受害鍵,))`,健康鍵狀態正確變成 `unknown`,沒有整批回滾。

第 3 條:針對你點名要判斷的 (b) 情境(收件口模組內把 `transaction()` 改成普通 `BEGIN`)——已擋住。用原本手法(直接對 `InboxStore` 自己的連線下普通 `BEGIN`,再用私有化後的 `attempt_store._EXECUTOR_TRANSACTION_ISSUER` 包成 `ExecutorTransaction`)重跑,執行期本身仍然只驗身分與 `is_open`,不驗證是不是 `BEGIN IMMEDIATE`,所以執行期這一層跟修正前一樣會放行寫入——但這次新增的 `test_the_only_place_that_issues_a_transaction_opens_it_with_the_write_lock` 是機械化的原始碼層守衛:我把 `inbox_store.py` 目前的原始碼讀進來,在記憶體裡套用同一種「把 `immediate_transaction()` 換成普通 `self._conn.execute("BEGIN")`」的修改,再跑測試同一套 AST 判斷邏輯——現況(79e0cc0)通過,模擬修改後確實變紅(`immediate_transaction` 沒被呼叫、原始碼裡出現裸 `BEGIN`)。也就是說(b) 情境如果真的發生在收件口模組裡,會在測試(pre-push/CI)被攔下,跟本專案其他「防忘記」守衛(如 S8 轉換表防竄改、S19 AST 掃描)是同一套機制,在既有測試會被跑到的前提下判定已擋住。至於「從專案外刻意 import 私有憑證」你已經判為範圍外,這裡不重複列。
