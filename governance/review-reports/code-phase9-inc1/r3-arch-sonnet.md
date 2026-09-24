severity: clean
# 報告

已讀取格式規則檔(`/private/tmp/claude-501/-Users-enzo/36c33b75-fd17-4bff-aa49-20f97a550e58/scratchpad/p9i1-fmt.md`)、`governance/review-reports/code-phase9-inc1/r2-arch-sonnet.md`(第 2 條)與 `r3-delta.patch`,並對照 `src/rtb/executor/runner.py`、`src/rtb/executor/execution.py`、`src/rtb/executor/attempt_store.py`、`src/rtb/executor/inbox_store.py` 現況逐一核對三個指定點。以下為完整報告全文:

---

severity: clean

本輪只驗 r2-arch 第 2 條(待寫清單無上限、忙碌被吞掉繞過斷路器)這次修正有沒有帶出新的不一致,三個指定面向都核對過,沒有引入第二種做法:

1. **忙碌計數接進 runner 既有 busy_streak**:`_loop` 沒有另開一顆計數器或另一套斷路門檻,而是把 `flush_calls()` 回傳的忙碌訊號直接併進同一個 `busy_streak`/`BUSY_LIMIT`/`EXIT_BUSY`(`src/rtb/executor/runner.py:69,80-84`),成功時一樣整段歸零、達門檻一樣回 `EXIT_BUSY`,只是判斷條件從單純看例外(`except InboxBusy`)多了一條 straight-line 的 `behind` 分支——這是因為 `flush_calls()` 依 r1 三席的決議永遠不丟 `InboxBusy`(`src/rtb/executor/execution.py:461-464`),回傳值是唯一能餵回同一顆斷路器的管道,不是另立新機制。
2. **補欄位沿用既有加欄做法**:`content_hash` 用 `DSP_CALL_ADDED_COLUMNS` 登記進 `inbox_store.py` 既有的 `_ADDED_COLUMNS` 字典(`src/rtb/executor/inbox_store.py:273-274`),跟 `attempts` 表用同一把 key→[(欄名,DDL)] 的機制;而且它「新鮮 SCHEMA 已內建、同時又登記等補欄位」的雙軌寫法,跟 `attempts` 表 `written_version`/`capability_expires_at` 的既有先例（`src/rtb/executor/attempt_store.py:58-91`,Phase 3 增量 3 就是這樣處理同一代分支內新增欄位)完全一致。
3. **stderr 少記報告跟既有啟動程式輸出慣例**:結束時的訊息(`src/rtb/executor/runner.py:100-103`)沿用同檔既有的「一句中文前綴+冒號+`', '.join(...)`」寫法(對照同檔 `183`、`185-186` 行的「讀不回來的嘗試」「重啟恢復跳過」訊息)。多做的 `sorted({str(key) for key in keys})` 去重排序不是另一種風格,是因為 `unrecorded()` 一次處理可能對同一把鍵留下好幾筆待寫紀錄(`少記 N 列`計的是列數而非去重後的鍵數),既有兩處訊息的來源本身不會重複、不需要這一步;這裡是資料形狀不同帶來的必要適配,不是引入第二種輸出慣例。

三處都沒有發現新的不一致,不列發現項。
