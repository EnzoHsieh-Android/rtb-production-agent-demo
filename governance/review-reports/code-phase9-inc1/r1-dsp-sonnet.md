severity: blocker
# 報告全文

severity: blocker

### 1. 呼叫紀錄寫入撞資料庫忙,會吞掉已經發生的 DSP 結果,還會冒充成正常忙碌重試
severity: blocker
blocking: 是

引句:「一起:結果寫入可能因收據失效回滾,呼叫本身確實發生了,不能跟著消失。」

**觸發情境**:執行迴圈呼叫 `DspClient.write`(或 `read_campaign`/`void`)拿到 HTTP 回應、解析成功之後,`_send` 在同一個呼叫堆疊裡呼叫 `self._report(...)` 觸發回呼(`dsp_client.py` 的 `_report`,見下方引句),回呼經 `record_dsp_call` 呼叫到 `Executor.log_dsp_call`,後者用 `self.store.transaction()` 開一個「獨立短交易」寫 `dsp_calls` 那一列。這個獨立交易走的是跟嘗試紀錄同一個連線、同一把寫入鎖(`file: \`src/rtb/executor/inbox_store.py:844\``),8 個工作者共用同一個檔案時完全可能在這個瞬間撞上忙碌逾時。`InboxStore.transaction()` 忙碌逾時會丟 `InboxBusy`(`file: \`src/rtb/executor/inbox_store.py:851-855\``)。

**會出什麼錯的行為**:`DspClient._report`(`file: \`src/rtb/executor/dsp_client.py:100-106\``)呼叫 `self._on_call(...)` 時完全沒有 try/except 包住,`_send` 的三個呼叫點(成功、DspUnavailable、沒拿到回應)也都沒有攔截這支回呼可能丟出的例外。所以 `log_dsp_call` 的 `InboxBusy` 會直接從 `_report` 穿透 `_send`,讓 `DspClient.write()`(或 `read_campaign`/`void`)整支方法都不回傳——即使 DSP 那一次呼叫已經真的成功、`read()` 也已經算出正確答案。呼叫端(`_run`/`_record`,`file: \`src/rtb/executor/execution.py:509\``)因此完全拿不到那個已經發生的寫入結果:嘗試紀錄停在 `in_flight`,不會被轉成已送出/已驗證。更糟的是,規格 [S618](`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase9可觀測與SLO_計劃.md:133`)明講「執行迴圈應在回呼裡寫一列 DSP 呼叫紀錄(**除非行程在回應之後、寫紀錄之前當機**)」——也就是說只有「行程真的當機」才准漏記這一列;`InboxBusy` 是可以攔截、可以重試的正常忙碌狀況,不在這個除外條款裡,但目前的寫法讓它跟當機一樣把那一列呼叫紀錄整個吞掉,違反了自己寫的「呼叫本身確實發生了,不能跟著消失」。

再往上一層,`InboxBusy` 也不是 `DspUnavailable`,所以 `_process` 裡 `except DspUnavailable: return self._release(...)`(`file: \`src/rtb/executor/execution.py:483-485\``)這條既有的釋放租約/計投遞次數路徑會被整個跳過;`InboxBusy` 一路穿透到 `runner._loop` 才被接住,但那裡的 `except InboxBusy`(`file: \`src/rtb/executor/runner.py:69-75\``)是設計給「取件或開始一筆時鎖不到、什麼都還沒發生」的正常競爭用的,只會 `busy_streak += 1` 然後睡一輪重來——它沒辦法分辨這次忙碌是「什麼都沒做」還是「DSP 已經真的寫過一次」。後果:(a) 該筆消息的收據既沒釋放也沒被算進投遞次數,要等到租約到期才由重啟恢復/對帳收尾,比原本快路徑慢很多;(b) 連續幾輪都撞上這種「其實有在做事」的忙碌,會被誤算進 `busy_streak`,達到 `BUSY_LIMIT` 就讓整個執行迴圈以 `EXIT_BUSY` 收工(`file: \`src/rtb/executor/runner.py:71-73\``)——但系統其實不是卡住,是可觀測寫入自己撞了鎖。

**已用可重現的實驗驗證**(在暫存目錄跑,未改動 repo):用 `tests.executor.fakes.Harness` 建一個真的執行迴圈,monkeypatch `Executor.log_dsp_call` 讓「write」這一類呼叫在寫紀錄時丟 `InboxBusy`(模擬資料庫忙),其餘照常執行。結果:
- `process()` 直接把 `InboxBusy` 丟出到最外層(不是被 `except DspUnavailable` 接住,也不是正常回傳的 `Processed`)。
- 呼叫序列印出 `['read_campaign', 'write']`——即 FakeDsp 的寫入語意已經真的跑過(依真 DSP 語意升版本、記操作紀錄)。
- `attempts` 表只有一列 `in_flight`,沒有任何後續轉換——那次已經發生的寫入結果沒有被寫進嘗試紀錄。
- `dsp_calls` 表只有 `read_campaign` 一列,`write` 那一列(那次真的發生過的呼叫)完全沒有留下紀錄——正是規格 [S618] 說「不能跟著消失」的那一列消失了,而且不是因為當機。

**建議修法**:`log_dsp_call`(或更上層的 `record_dsp_call` 回呼)應該把寫入 `dsp_calls` 的例外自己吞掉(至少吞 `InboxBusy` 這種可預期的忙碌/可重試錯誤,寫個警告或計數就好),不要讓它有機會蓋掉呼叫端已經算出來的真實 DSP 結果;或者把「觸發回呼」搬到 `read()`/`no_reply()` 已經把結果準備好、且 `_send` 已經要 `return` 之後才做,並確保回呼本身的例外不會取代原本要回傳的值。兩種做法都要補一個「回呼寫入失敗時,呼叫端仍拿得到正確的 DSP 結果」的測試(目前 `tests/executor/test_dsp_calls.py` 只測了正常寫入與 LeaseLost 情境,沒有測回呼寫入本身失敗的情形)。
