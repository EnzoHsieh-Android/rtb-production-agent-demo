severity: minor

## F1 待核可轉出後(放回待處理／到期轉擋下)不再計入待核可,沒有測試覆蓋
severity: minor
blocking: 否 — 實測（見下）目前實作行為正確,純粹是測試涵蓋度缺口,不是錯誤行為,不符 major/blocking 判準。

引句:「待核可被放回待處理、到期轉擋下之後不應再算待核可」

這句來自派工說明,不是凍結 patch 原文;凍結 patch 裡沒有任何測試呼叫 `settle_awaiting` 之後再讀 `awaiting_count`／`approval_counts`。全域檢索確認：

file: `tests/executor/test_observability.py:449`(`test_approval_counts_cover_waiting_and_applied` 只造待核可、從沒讓任何一份被 `settle_awaiting` 轉走)
file: `tests/executor/test_approval.py`、`tests/executor/test_inbox_disposition.py` 有呼叫 `settle_awaiting`,但不接 `awaiting_count`／`approval_counts`——兩邊測試互不覆蓋這個交叉情境。

我拿臨時副本手動重現：造一份待核可(`await_approval`+同一交易 `record_stop`,跟正式流程一樣),確認 `awaiting_count` 回 `(1, 0)`；分別對它跑 `settle_awaiting(..., AwaitingOutcome.RELEASED, ...)` 與 `AwaitingOutcome.EXPIRED`,之後 `awaiting_count` 都正確變回 `(0, 0)`。命令與結果：

```
$ .venv/bin/python -c "...settle_awaiting RELEASED..."
before settle: (1, 0)
settle released ok: True
after RELEASED settle: (0, 0)

$ .venv/bin/python -c "...settle_awaiting EXPIRED..."
settle expired ok: True
after EXPIRED settle: (0, 0)
```

原因是 `AWAITING` 判準是 `state = 'pending' AND disposition = 'awaiting_approval'`(`src/rtb/executor/inbox_store.py:150`),而 `settle_awaiting` 的三種轉換（EXPIRED 改 disposition、SUPERSEDED 改 state、RELEASED 清空 disposition)都會讓這個判準變假,所以目前程式碼是對的。但這個「對」目前只靠我手動重現驗證,凍結 patch 沒有任何測試釘住它；日後改動 `settle_awaiting` 或 `awaiting_count` 的任一邊，都不會被這批新測試發現。

## 殺傷力驗證(非發現,附記於此供審查對照)

在臨時副本（`/tmp/rtb3b-copy`，複製自唯讀 repo，未動原始 repo)對 `src/rtb/executor/inbox_store.py` 做三種改壞測試，皆確認 `test_approval_counts_cover_waiting_and_applied` 會翻紅：
1. `awaiting_count` 的 `unknown` 一律回 0（不回報接不到停下紀錄的份數）→ 翻紅（index 1 diff: 0 != 1）。
2. `approval_use_count` 拿掉依廣告篩的 `write_stops` join（讓核可使用表單獨計數，不核對關卡)→ 直接觸發 `sqlite3.OperationalError: no such column: w.campaign_id`，翻紅。
3. `approval_use_count` 的時間範圍改成「不含起點、含終點」（跟設計「含起點不含終點」相反）→ 翻紅（index 2 diff: 0 != 1）。

三次改壞測試後都用 `git checkout -- src/rtb/executor/inbox_store.py` 還原，全套 `tests/executor/test_observability.py` 復原後仍 24 passed。實作拿掉不會假綠。

## 造法與正式流程一致性檢查(非發現)

`tests/executor/test_observability.py` 的 `_awaiting` 輔助函式：`store.accept` → `store.receive` → 同一交易內 `store.await_approval` + `store.record_stop`；比對 `src/rtb/executor/execution.py:503-511` 的 `_await_in`，正式流程也是「`await_approval` 沒失敗就在同一個交易寫 `record_stop`」，兩者一致，不是造法不同造成假綠。

`_approval_use` 輔助函式直接呼叫 `record_approval_use`，跟 `src/rtb/executor/execution.py:680-696` 的 `_audit`（開始一筆的同一交易內對每一則 `ApprovalUse` 呼叫 `record_approval_use`）在欄位語意上一致（租戶、任務、修訂、內容雜湊、關卡）。

另外確認 `write_stops` 有 `UNIQUE (kind, task_id, revision, content_hash)`、`approval_uses` 有 `UNIQUE (task_id, revision, content_hash, stage)`（`src/rtb/executor/inbox_store.py:172`、`:187`），驗證了測試裡「同一份提案同一種類/同一關只一列」的假設站得住，`awaiting_count`／`approval_use_count` 的 JOIN 不會因重複列而算多。

`awaiting_count` 的「接不到停下紀錄」(`with_stop=False`)分支在目前生產路徑下其實不可達——全庫只有 `execution.py:509-511` 一處呼叫 `await_approval`，且必定緊接著同交易 `record_stop`；這個分支是防禦性設計（RULE 明寫「不悄悄丟掉」），測試涵蓋它沒有問題，只是提醒審查員這不是目前會被觸發的真實情境。
