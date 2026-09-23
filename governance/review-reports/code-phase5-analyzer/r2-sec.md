severity: clean

查了什麼:針對第 1 輪 major 發現(`block_code` 未過濾直接寫入永久稽核表 `tasks.error_detail`,可注入 ANSI escape 與偽造稽核行)這一輪的修法 `_BLOCK_CODES` 白名單(`src/rtb/analyzer/flow.py:293`、`_belongs_to`),從攻擊者角度重新逐一驗證三個鏡頭:

1. 白名單是否真的擋住每一段寫進歷史表的不可信字串——通讀 `flow.py` 裡所有組成 `error_detail`/`_write_follow_up` 的 f-string(`_closed`、`_replan`、`rejected={stale}`、`rejected={exc}`、`idempotency_conflict`、`replan={reason.value}`、`follow_up={child}` 等全部組成段),確認每一段不是固定字面值就是先經過白名單/型別/雜湊比對才落地(`SubmitStale`/`SubmitRejectedPermanently` 的訊息本身也受 `inbox_client.py` 的 `_STALE_CODES`/`_PERMANENT_CODES` 固定枚舉限制);`block_code` 只有落在 `_BLOCK_CODES` 白名單內才會被寫進 `_closed(f"{answer.state}={answer.block_code}")`,否則 `_belongs_to` 判 `consistent=False` 整包被丟棄、不落地。用第 1 輪原本那個 payload(`over_budget_cap\x1b[31mFAKE SYSTEM ALERT...injected-fake-audit-line`)重跑攻擊實驗(`/private/tmp/.../scratchpad/p5a-r2-sec-exp/exp2.py`),結果:任務停在 `HANDED_OFF`、seq 不變、`error_detail` 仍是 `None`,沒有寫入;另外試了大小寫近似繞過(`Not_Permitted`)同樣被擋。

2. 回應讀不懂會不會被利用成無限重試或拖住租約——讀 `_advance_holding`/`advance()`:不論是 `_belongs_to` 判不屬於這份提案(回 `None`)、`operation_lookup`/`submit` 丟未知例外、還是往外傳的例外(`except BaseException: store.release_lease_quietly(...); raise`),每條路徑都會在同一次呼叫內把租約放掉,不會撐到 `LEASE_DURATION`(60 秒)到期。實驗驗證:偽造未知 `block_code` 之後,另一個 `owner` 立刻能拿到租約(`exp2.py` 第 5 項輸出 `True`);且下一次呼叫用合法 `handed_off` 回應仍能正常結案(第 2 項),不是卡死或無限迴圈,只是「這一輪沒進展」的正常重試語意,跟 Phase 4 既有的可重試合約一致。

3. `not_permitted` 合併之後分析行程還能不能從別的訊號試出預算上限或租戶歸屬——確認 `_from_inbox_answer` 對所有非 `version_changed`/`expired` 的 blocked 原因(含 `not_permitted`)一律走同一個 `_closed(...)`,不建立接續任務(`follow_up=None`),因此不會出現在 `replan_counts()`(只統計 `version_changed`/`expired`/`after_retention` 三類原因)、也不會有跟其他擋下原因不同的分支行為或欄位差異;實驗確認 `not_permitted` 的 `error_detail` 就是固定字串 `blocked=not_permitted`,不含任何額外資訊,且不產生接續關係。DSP 端冪等鍵查詢路徑(`_matches`)也只在欄位不合時寫死 `inbox_purged;idempotency_conflict`,不會把實際 `new_budget`/`campaign_id` 等內容洩漏進歷史紀錄。額外確認 `make_operation_lookup` 用 `is_id()`(固定字元集正則)驗過冪等鍵格式再組進 `/operations/{key}` 這條 URL 路徑,新增測試 `test_an_operation_lookup_failure_other_than_not_found_raises` 也用 `"has space/../x"` 驗過路徑穿越字串會被擋。

補充:另外對照檢查了新增/改動的 `_positive_or_none`(0 不再視為合法預算/版本)、`_write_follow_up` 的重入防線(`link is not None`)、`follow_up_id` 碰撞防線與 `MAX_GENERATION` 代數上限,均無法被外部輸入繞過或用來延長攻擊面。實際跑了 `tests/analyzer/test_replan.py`、`test_dsp_client.py`、`test_inbox_client.py`、`test_instrumented.py` 全數 119 條測試,全綠。

没有找到這一輪修法之外、或修法本身引入的新洞。
