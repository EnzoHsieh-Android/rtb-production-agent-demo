severity: minor

### 1. 迴圈忙碌連續三次停機沿用 EXIT_HALTED,沒有沿用啟動時新開的 EXIT_BUSY

severity: minor
blocking: 否(不影響行為正確性,只是退出碼語意不一致,且專案自陳目前沒有自動重啟依退出碼分流)
引句:「多工作者下等鎖逾時是正常競爭:這一輪休息,連續幾次才停」

`runner.py` 為「啟動時開庫/重啟恢復連續忙碌」特地新開了 `EXIT_BUSY = 6`,註解明講是「啟動時資料庫連續 BUSY_LIMIT 次都忙:乾淨結束,稍後再啟動」(`src/rtb/executor/runner.py:40`),語意上是「可重試、非系統錯誤」。但同一份設計原文說「啟動時的開庫與重啟恢復同樣連續 3 次都忙才結束」——即迴圈每輪的忙碌停機跟啟動時是「同一條」政策。實作卻讓 `_loop` 連續三輪忙碌時直接 `return EXIT_HALTED`(`src/rtb/executor/runner.py:69`),與 `ExecutorHalted`(設定檔壞掉、無進展等真正系統錯誤)共用同一個退出碼,而不是沿用剛新增的 `EXIT_BUSY`。這跟模組頂端的說明「啟動時與每一輪都是休息一下再試,連續 BUSY_LIMIT 次都忙才結束」給人的「一致待遇」印象不符——事後若有人想憑退出碼分辨「純粹忙碌、稍後重試即可」跟「真的壞了、要人看」,兩種情境在迴圈執行期會混成同一碼,而在啟動期卻能分開。S135 合約只要求「以非零代碼停機」,字面上沒被破壞,所以列為 minor,但這是作者在同一次改動裡新增了一個更精確的分類卻沒有把它用滿的不一致,值得補上。
file: `src/rtb/executor/runner.py:38-40`
file: `src/rtb/executor/runner.py:65-70`

### 2. 假 DSP 補了寫入路徑的鎖,但查詢路徑(read_campaign / operation_record / operation_version)沒有納入同一段鎖

severity: minor
blocking: 否(CPython GIL 讓單一字典讀寫不會撞壞;40 次重跑 `test_multi_worker.py`、`S132`/`legacy`/`timing_out` 相關測試均未見 flaky)
引句:「多個執行緒(並行測試裡的多個工作者)同時呼叫時,狀態的讀改寫要一段做完」

`tests/executor/fakes.py` 這次補的說明明講「狀態的讀改寫要一段做完」,並在 `write`/`apply`/`_apply`/`void` 都包進了新加的 `self._lock`(`tests/executor/fakes.py:61,79,83,97,138`)。但同樣讀寫 `self.campaigns`/`self.operations` 這兩份共享狀態的 `read_campaign`(`tests/executor/fakes.py:67-73`)與 `_lookup`(供 `operation_record`/`operation_version` 用,`tests/executor/fakes.py:121-135`)完全沒有拿鎖。目前測試情境靠應用層的 barrier/event 精確排開了讀寫重疊的時間點,所以沒有實際踩雷,但假 DSP 的鎖覆蓋跟它自己聲明的意圖(「狀態的讀改寫要一段做完」)不一致,是這次改動裡「補了一半」的執行緒安全,日後有人在多工作者測試裡新增一個會在讀取路徑上真正重疊的情境時,容易被這個沒鎖的縫隙咬到。
file: `tests/executor/fakes.py:67-73`
file: `tests/executor/fakes.py:121-135`

### 3. 舊測試仍標記已被增量 3a 取代的 [S13],追不回現行合約

severity: minor
blocking: 否
引句:「def test_restart_recovery_moves_every_in_flight_key_to_unknown_and_touches_nothing_else」

計劃筆記明寫「Phase 3 的 S13:重啟恢復把每一把嘗試中轉結果不明:改由 [S127] 取代(只轉沒有處理中訊息的)」。測試 patch 只把這支測試呼叫加上 `held=()`(讓它繼續驗證 `attempt_store.recover_in_flight` 在「沒有要略過的鍵」時的基礎行為,這本身合理),但測試上方的 `# ---- [S13] ----` 標記沒有一併處理(改成指到 S127、或註明是被取代合約的降級單元測試)。純屬可追溯性/文件精度問題,不影響測試本身守住的行為。
file: `tests/executor/test_attempt_store.py:310-311`
