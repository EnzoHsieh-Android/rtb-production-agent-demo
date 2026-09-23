severity: minor

## F1 版本已變與提案已過期同時成立時,永遠回報「版本已變」,沒有測試釘住這個優先序
severity: minor
blocking: 否 — 不違反任何已寫的合約句(S310 沒有規定兩者同時成立時誰優先),只是覆蓋面缺口,不是錯誤行為

引句:「return BlockCode.VERSION_CHANGED if checked is BlockCode.VERSION_CHANGED else None」

`_version_changed_or_none`(`src/rtb/executor/execution.py:149`)只看 `precheck` 的結果,不管
`proposal.decision_expires_at` 是否也已經過期。在 `_after_expiry`(`execution.py:242-243`)與
`_reconcile_not_found`(`execution.py:675-676`)裡,`checked = precheck(proposal, view)` 跟
「是否過期」是兩個獨立條件;若重跑檢查時廣告版本剛好也變了、同時提案也已過期,兩處都會判定
`block = VERSION_CHANGED`,收件口回應與嘗試代碼都記成「版本已變」,不會出現「提案已過期」那一類
擋下原因。目前的 5 支新測試只覆蓋「只過期不換版本」與「只換版本不過期」兩種單一原因的情況
(`test_other_failed_rechecks_are_still_acknowledged_as_previously_failed` 的 `expired` 分支
故意不换版本),沒有「兩者同時成立」的案例,所以這條優先序目前只是程式行為,沒有合約或測試守著。
不算錯——分析端本來就該對版本已變重新規劃,同時過期不影響這個結論——但如果之後有人想讓「過期」
優先於「版本已變」,這裡不會有測試翻紅提醒。

## 逐條驗證结果(通過)

- **擋下原因參數傳遞**:新增的 `block_code` 參數(`_write`、`_ack_terminal`、`_void_then_fail`)
  只在兩條新路徑(`_after_expiry` 的 `execution.py:501`、`_reconcile_not_found` 的
  `execution.py:678` 經 `_void_then_fail`)顯式傳非 None 值,其餘既有呼叫點(終點確認、開始一筆
  撞既有失敗鍵、收件口取件撞既有失敗鍵三處原始 S310 路徑,以及驗證逾時、冪等衝突等其他終點寫入)
  全部維持預設 `None`,不會被新參數污染。逐一核對 `execution.py` 全部 16 個 `self._write(` 呼叫點
  確認只有 2 處帶 `block_code`。
- **不會蓋錯原因**:`_version_changed_or_none` 只在 `precheck` 真的回 `VERSION_CHANGED` 時才回非
  None 值;其他原因(過期、廣告不存在、不在投放、權限不過)一律回 None,交回
  `block_code_for_failure(row.code)`(此時 `row.code` 是 `NOT_HAPPENED`)判成
  `OPERATION_PREVIOUSLY_FAILED`。用把 `_version_changed_or_none` 改成永遠回 `None` 做刪除測試,
  三支新測試(`test_a_version_change_found_after_capability_expiry_is_acknowledged_as_version_changed`
  等)全部翻紅,確認測試真的測到這個守衛;還原後全部通過。
- **`not_permitted` 合併只影響回應本文**:`InboxHandler` 端 `_answered_block_code` 只用在
  `_accepted_body`(唯一組裝 HTTP 回應的地方),`InboxStore.ack_blocked` 寫進資料庫的還是原始
  `BlockCode`(`over_budget_cap` / `campaign_not_allowed`),沒有任何寫入路徑被改動。
  `test_a_resend_hides_which_permission_blocked_it` 直接讀
  `SELECT block_code FROM proposals` 核對資料庫仍是細分代碼,只有回應本文變成
  `not_permitted`。把 `_answered_block_code` 改成直接回傳 `stored`(拿掉合併)重跑該測試,
  `over_budget_cap` 與 `campaign_not_allowed` 兩個參數化案例翻紅(斷言 `'campaign_not_allowed' ==
  'not_permitted'` 失敗),其餘三個不受影響的案例仍綠——確認測試只鎖住這兩種需要合併的代碼,
  沒有連坐其他代碼。
- **執行緒例外收回主執行緒**:`test_two_concurrent_writers_reproduce_a_version_conflict` 的
  `run()` 用 `try/except BaseException` 把子執行緒的例外收進 `outcomes[name] = ("error",
  repr(exc))`,斷言改成 `all(o[0] != "error" for o in outcomes.values())`,失敗訊息會帶原始例外
  字串。這是診斷性修正(原本執行緒拋例外時,主執行緒只會看到 `len(outcomes) == 2` 斷言失敗、看不
  到原因),不影響原本判斷併發版本衝突對錯的斷言邏輯,沒有引入誤判。
- 相依的 `test_crash_recovery.py` monkeypatch(`inside_transaction(self, tx, row, receipt, now,
  *rest)`)正確地用 `*rest` 把新增的 `block_code` 位置參數原樣轉交給 `ORIGINAL_ACK`,不會因為
  `_ack_terminal` 多一個參數而在故障注入測試裡對不上簽名(已跑 `tests/executor/` 全套 334 個
  測試,`before_terminal_commit` 那組情境含在其中,全數通過)。

## 重現指令
```
cd /Users/enzo/rtb-3b
/Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest -p no:cacheprovider \
  tests/executor/test_version_conflict.py -q
# 15 passed

# 拿掉 not_permitted 合併(在副本上做,不動原 repo):
sed -i '' 's/return NOT_PERMITTED if stored in _PERMISSION_BLOCKS else stored/return stored/' \
  src/rtb/executor/inbox_server.py
python -m pytest -p no:cacheprovider tests/executor/test_version_conflict.py -q -k hides
# 2 failed(over_budget_cap、campaign_not_allowed)、3 passed

# 拿掉版本已變的重跑確認(在另一份副本上做):
# 把 _version_changed_or_none 改成永遠 return None
python -m pytest -p no:cacheprovider tests/executor/test_version_conflict.py -q
# 3 failed(三支新測試)、12 passed

# 全套 executor 測試(確認沒有連坐其他行為):
/Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest -p no:cacheprovider \
  tests/executor/ -q
# 334 passed
```

## 筆記對照
`docs/rtb-production-agent-demo-knowledge/Systems/執行迴圈.md` 與
`docs/rtb-production-agent-demo-knowledge/Systems/提案收件口.md` 這輪修正的 `RULE:` 段落
(擋下原因分流三處共用 `block_code_for_failure`、`not_permitted` 只在回應本文合併)跟
程式行為一致,已用重現指令核對過,沒有發現對不上的地方。F1 是程式行為裡沒有筆記或合約覆蓋到
的組合情境,不是筆記與程式衝突。
