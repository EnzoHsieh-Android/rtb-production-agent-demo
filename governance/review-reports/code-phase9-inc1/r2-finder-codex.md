severity: major

### 1. 待寫清單在正常停機時仍會漏帳，且補寫缺少有界與冪等保護
severity: major
blocking: 是
引句:「跑迴圈;結束前補寫欠著的呼叫紀錄,還是忙就只能放掉(跟當機一樣少記那幾列)。」
file: `src/rtb/executor/execution.py:454`
file: `src/rtb/executor/runner.py:58`
file: `src/rtb/executor/runner.py:86`
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase9可觀測與SLO_計劃.md:132`

觸發情境：DSP 已回應，回呼把紀錄放進 `_pending`，但 `flush_calls()` 撞到 `InboxBusy`；之後資料庫持續忙到 runner 達到 `BUSY_LIMIT`、收到正常停止訊號，或有限輪次結束。`_serve()` 最後只再補寫一次，而 `flush_calls()` 仍會吞掉 `InboxBusy` 後返回，接著資料庫關閉、程序結束。

錯誤行為：這是可預期的受控退出，不是規格唯一允許漏帳的「回應後、寫紀錄前當機」，待寫紀錄卻仍永久消失。若鎖競爭間歇性地只讓補寫失敗、後續業務交易成功，程序還會繼續發出 DSP 呼叫並向沒有上限的 `_pending` 追加，清單可以持續長大。另外，成功提交與 `del self._pending[:len(waiting)]` 之間若被中斷，最後的再次補寫會重複插入；表內沒有可供去重的呼叫識別碼。這會讓 DSP 次數、錯誤率與延遲統計同時可能少算、重算或耗盡記憶體，違反 [S618]。

建議修法：為每次 DSP 呼叫產生穩定且唯一的 `call_id`，以唯一索引做冪等插入，並把待寫項目放進可恢復的本機 durable spool／outbox；成功寫入主表後才確認移除。至少也必須在待寫未排空時停止發出新 DSP 呼叫、設定硬上限與背壓，並讓受控關機持續排空或明確以未完成狀態拒絕退出，而不是吞掉最後一次忙碌。

### 2. DSP 呼叫的內容雜湊回推會把共用冪等鍵的兩份內容壓成同一份
severity: major
blocking: 是
引句:「hashes = {(task, revision, key): digest for (task, revision, digest), (key, _) in keys.items()}」
file: `src/rtb/ops/trace.py:277`
file: `src/rtb/ops/trace.py:348`
file: `src/rtb/domain/attempt.py:20`
file: `src/rtb/domain/proposal.py:297`
file: `tests/ops/test_trace.py:255`

觸發情境：舊提案被清除後，同一任務與修訂收到新內容；兩份提案只有決策建立／到期時間不同，其餘任務、廣告、動作、變更與觀察版本相同。`content_hash()` 包含決策時間，所以兩份雜湊不同；`operation_key()` 刻意不含決策時間，所以兩份共用同一把鍵。

錯誤行為：`keys` 能保留兩個三件組，但 `hashes` 又縮回 `(task, revision, key)`，字典建構時其中一個雜湊會覆蓋另一個。`dsp_calls` 本身沒有內容雜湊，因此舊、新兩代的所有 DSP 呼叫最後都被標成同一個雜湊，事故時間線會錯誤歸屬。新增測試用不同 `campaign_id`，使兩份提案的冪等鍵也不同，沒有覆蓋這個合法碰撞情境。

建議修法：不要事後由冪等鍵回推。把 `content_hash` 加進 `CallSubject` 與 `dsp_calls` 表，在呼叫發生時直接持久化，追蹤時讀該列自己的值；舊資料無法唯一判定時應標成缺失或歧義，不能任選一個雜湊。補一支回歸測試：同任務、同修訂、同操作鍵，只改決策時間，斷言兩代 DSP 呼叫各自帶正確內容雜湊。