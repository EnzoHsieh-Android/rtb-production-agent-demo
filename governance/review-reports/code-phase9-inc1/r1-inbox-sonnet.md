severity: major

### 1. DSP 呼叫紀錄的獨立短交易撞鎖時,`InboxBusy` 會穿透 `DspPort`「不丟例外」的合約,並拖垮整輪對帳/處理

severity: major
blocking: 是

引句:「獨立的短交易(資料庫忙碌照一般寫入丟 InboxBusy,由啟動程式休息再來)」(`src/rtb/executor/execution.py` 新增的 `log_dsp_call` docstring,對應 patch 裡 `+    def log_dsp_call(self, call: DspCall, subject: CallSubject) -> None:` 那個 hunk)

觸發情境:照 `runner.py` 檔頭本來就寫明的部署模式「可以同時跑好幾個執行迴圈」,兩個工作行程各自開一條連到同一個 SQLite 檔的連線。工作者 A 呼叫 `self.dsp.write(...)`(或 `read_campaign`/`void`/`operation_record`)送出真正的 HTTP 呼叫、DSP 已經回應(寫入已經在 DSP 端發生);與此同時工作者 B 正持有這個資料庫檔的寫入鎖(例如在 `accept()`/自己的 `process_one()` 交易裡)。此時 A 的 DSP 用戶端在同一次呼叫裡同步觸發 `record_dsp_call` 回呼 → `Executor.log_dsp_call` → `with self.store.transaction()`(`BEGIN IMMEDIATE`),因為鎖被 B 占用超過 `busy_timeout_seconds` 而丟出 `InboxBusy`。我在 `/private/tmp/.../p9i1-exp/exp2.py` 用另一條連線模擬持鎖,直接呼叫 `Executor.log_dsp_call(...)` 重現:

```
InboxBusy raised from log_dsp_call, as predicted: database is locked
```

`dsp_client.py:100-106` 的 `_report()`(`self._on_call(DspCall(...))`,回呼掛的就是 `record_dsp_call`)沒有任何 `try/except`,所以這個 `InboxBusy` 會直接從 `_report` 穿出 `_send`,再穿出 `DspClient.write`/`read_campaign`/`void`/`operation_record` 本身。

會出什麼錯的行為:
1. `execution.py:138`、`:150` 等處的 `DspPort` 介面明白寫著「`write`/`void` 不丟例外,沒拿到回應就回 status 為 None 的回應」,`read_campaign`/`operation_record` 也只承諾丟 `DspUnavailable`。現在它們實際上還可能丟出完全不同類別的 `InboxBusy`,違反這份合約。
2. `execution.py` 裡圍著 DSP 呼叫的例外處理全部是窄範圍的 `except DspUnavailable:`(第 484、942、981、1098、1111 行),沒有一處接住 `InboxBusy`;`reconcile_all()` 逐鍵迴圈(`execution.py:1033-1038`)也只接 `LeaseLost`/`CorruptedInboxRow`。結果是:DSP 的寫入其實已經成功送出,但這次 `_run`/`_record` 根本沒機會把結果寫回嘗試紀錄——不是因為這把鍵的收據失效(那有既有的 `LeaseLost` 機制接住),而是因為一個跟「這把鍵」完全無關的旁支(記錄呼叫紀錄的短交易)撞鎖。`InboxBusy` 一路穿出 `process_one()`/`reconcile_all()`,最後只在 `runner.py:69` 的 `_loop` 頂層被當成「正常等鎖競爭」吞掉、`sleep` 後重試整輪——這一輪 `reconcile_all()` 裡還沒處理到的其他鍵全部被連帶放棄,而且這一次 DSP 呼叫本該寫的那一列呼叫紀錄永遠遺失(不會重試、不會補寫)。
3. 這違反了本增量自己定的合約 [S618](`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase9可觀測與SLO_計劃.md:132`):「執行端 DSP 用戶端的每一支公開方法,每一次 HTTP 呼叫都應剛好觸發一次呼叫回呼;執行迴圈應在回呼裡寫一列 DSP 呼叫紀錄(除非行程在回應之後、寫紀錄之前當機)」——這裡漏記的原因不是「行程當機」,而是「資料庫忙碌」,這是文件裡多工作者部署下的正常事件、不是意外,合約沒有把它排除在外。

雖然依賴既有的對帳機制最終能自我修復(下一輪 `reconcile_all()` 會把還卡在 `IN_FLIGHT` 的鍵轉成 `UNKNOWN` 再查證),但這是「靠別的機制意外接住」而非設計如此:整輪處理被中止、其他鍵被連坐延後,而且違反了 `DspPort` 對呼叫端(執行迴圈其餘程式碼)明講的「不丟例外」介面保證。

file: `src/rtb/executor/dsp_client.py:100-106`(`_report` 沒有 try/except)
file: `src/rtb/executor/execution.py:444-445`(`__post_init__` 掛上 `record_dsp_call`)
file: `src/rtb/executor/execution.py:461-464`(`log_dsp_call` 開自己的短交易)
file: `src/rtb/executor/execution.py:137-138,150`(`DspPort.write`/`void` 的「不丟例外」合約)
file: `src/rtb/executor/execution.py:1033-1038`(`reconcile_all` 只接 `LeaseLost`/`CorruptedInboxRow`)
file: `src/rtb/executor/runner.py:69-75`(`_loop` 頂層才接住 `InboxBusy`,整輪重來)
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase9可觀測與SLO_計劃.md:132`([S618] 只排除「行程當機」這一種遺失原因)

建議修法:在 `log_dsp_call`(或更底層 `record_dsp_call`)裡把 `InboxBusy` 當成「盡力而為、記不到就放棄」處理——比照既有 `InboxStore.record_event` 的做法(docstring:「盡力而為:寫不進去(忙碌、磁碟滿)就放棄,不影響對呼叫者的回應」),用 `try/except InboxBusy: pass` 包住,不讓它穿出 `DspPort` 的公開方法;若要保留可觀測性,退而求其次記一個內部計數/log,而不是讓一個記錄用的旁支交易中止呼叫端正在做的主要工作。

---

除了以上一項,已針對本增量核對:每個生命週期事件觸發點(收件/取代/到期、取件/接手、放租約、確認、待核可三轉換、重放放回)的欄位與 `[S612][S620][S624]` 等合約逐一比對程式碼與測試(`tests/executor/test_lifecycle_events.py`、`test_read_only.py`),事件與狀態寫入確認都在同一個 `self.store.transaction()` 內完成(續租刻意不寫事件,符合文件);`_calls_for` 這個 `ContextVar` 範圍在正常路徑與例外路徑(含非 `LeaseLost` 的例外)都靠 `contextmanager` 的 `finally` 正確清掉,DSP 用戶端的 HTTP 呼叫在現有程式碼裡都是同執行緒同步呼叫,沒有發現跨執行緒竄改範圍的路徑;`InboxReads`/`ReadOnlyInbox` 的唯讀交易守衛、`WRITE_FUNCTIONS` 清單、終點部分索引的查詢字串等都與規格一致,`.venv/bin/python -m pytest tests/` 全數 1576 項通過。未發現其他會造成事件漏寫、重複寫或資料損壞的問題。
