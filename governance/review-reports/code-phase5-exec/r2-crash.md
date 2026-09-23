severity: clean

## 檢查過程(供交叉核對,不是發現)

鏡頭涵蓋:終點確認交易邊界、三條擋下原因分流(終點確認/開始撞既有失敗鍵/取件撞既有失敗鍵)加新增
兩條重跑檢查路徑(憑證過期後重讀、對帳查不到重跑檢查)、沒有收據的舊資料路徑、當機復原替身改參數
列後是否還測得到「交易提交前當機會一起回滾」。用複本 `/tmp/rtb3b_copy`(git -C 操作,未動唯讀
repo)做了三組突變測試:

1. 把 `tests/executor/test_crash_recovery.py` 的 `inside_transaction` 替身改回舊的 4 參數版本
   (拿掉 `*rest`):`test_a_crash_before_the_terminal_commit_rolls_back_the_result_and_the_acknowledgement`
   立刻失敗,原因是 `TypeError: inside_transaction() takes 5 positional arguments but 6 were given`
   ——不是靜默通過,而是大聲炸掉,證明 `*rest` 轉交是必要的相容修正,不是偷懶蓋掉真的檢查。
2. 把 `src/rtb/executor/execution.py` 的 `_write` 改成把 `_ack_terminal` 移出交易(開第二個獨立
   交易呼叫),同一支測試立刻抓到:斷言 `attempt=committed_unverified` 變成看到
   `attempt=verified`,證明現在的替身仍然真的測到「終點確認跟嘗試寫入在同一個交易」這件事,改壞
   會翻紅。跑完立刻 `git checkout` 復原。
3. 把 `_version_changed_or_none` 改成永遠回 `None`,`tests/executor/test_version_conflict.py` 三支
   新增測試(憑證過期後重讀版本已變、對帳查不到重跑版本已變、及其重送作廢那支)全部翻紅,證明這是
   有殺傷力的斷言而非空殼。跑完復原。

另外逐一讀過三個分流函式(`_after_expiry`、`_reconcile_not_found`、`_void_then_fail`)、
`block_code_for_failure`(inbox_store.py:84)、`_ack_terminal`(execution.py:433),核對:
- `_version_changed_or_none` 只在 `precheck` 回 `VERSION_CHANGED` 時才覆蓋,其他原因(過期、
  不在投放)回 `None`,落回 `block_code_for_failure(row.code)` 走原本規則,沒有把過期或其他業務
  拒絕誤標成版本已變。
- `_write`/`_ack_terminal` 只在 `receipt is not None` 時才呼叫 `ack_blocked`;沒有收據的舊資料
  路徑完全不走到這段(`if receipt is not None and new.state in TERMINAL_STATES:`),`block_code`
  參數對這條路徑是安全的死代碼,不會因為多帶一個參數就出事。
- `_void_then_fail` 作廢後查到已提交(`VoidOutcome.FOUND`)直接轉 `_found`,不會用到傳入的
  `block_code`;作廢逾時(`VoidOutcome.TIMEOUT`)留在結果不明交給對帳,也不消費 `block_code`;
  只有真的判失敗才用它覆蓋 `_ack_terminal` 裡的擋下原因,語意跟合約描述一致。
- `InboxServer._answered_block_code` 只在回應本文把 `over_budget_cap`/`campaign_not_allowed`
  合併成 `not_permitted`,收件表寫入(`ack_blocked`)仍用原始細分代碼(`tests/executor/
  test_version_conflict.py::test_a_resend_hides_which_permission_blocked_it` 直接查
  `SELECT block_code FROM proposals` 核對)。

跑過的指令與結果:
- `/Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest -p no:cacheprovider tests/executor/ -q`
  → 334 passed(在複本上,含以上三組突變測試前後的還原狀態)。

沒有發現行為錯誤、合約破壞或資料損壞。這次修正在我的鏡頭範圍內(交易邊界、確認時機、各分流擋下
原因、舊資料安全、當機復原替身的有效性)看起來正確且測試確實有殺傷力。
