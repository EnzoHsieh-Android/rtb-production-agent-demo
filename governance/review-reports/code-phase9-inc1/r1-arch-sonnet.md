severity: major
# 審查報告

severity: major

## 查核範圍說明

依指示對照 `governance/review-reports/code-phase9-inc1/r1-snapshot-src.patch` 與同層既有檔(`src/rtb/executor/*`、`src/rtb/analyzer/*`、`src/rtb/sqlitekit.py`、各層 `ruff.toml`)。

**新增的 `src/rtb/ops` 套件依賴方向**:查過沒發現問題——`src/rtb/ops/ruff.toml` 自己禁了 `sqlite3`、`rtb.sqlitekit`、`rtb.dsp`(只准經唯讀開法讀、DSP 只經 HTTP 唯讀端點),而 `src/rtb/analyzer/ruff.toml`、`src/rtb/domain/ruff.toml`、`src/rtb/dsp/ruff.toml`、`src/rtb/executor/ruff.toml` 四層都同步補上 `"rtb.ops"` 的 banned-api,依賴方向確實只有「ops → 分析端/執行端的唯讀介面」單向;`src/rtb/ops/trace.py` 的匯入也確實只取 `TaskReader`、`ReadOnlyInbox`、`attempt_store` 的讀取函式與 `rtb.httpclient.request_json`,沒有匯入任一層的寫入介面。這部分沒有違反既有分層。

## 發現

### 1. 用模組層級 ContextVar 當隱性通道把提案身分傳給 DSP 呼叫回呼,跟專案既有的「顯式傳身分」做法不一致

severity: major
blocking: 是

引句:「_SCOPE: ContextVar[_CallScope | None] = ContextVar("dsp_call_scope", default=None)」

觸發情境:`src/rtb/executor/execution.py` 新增的 `record_dsp_call(call)` 是掛在 `DspClient` 上的唯一回呼(`Executor.__post_init__` 呼叫 `self.dsp.listen(record_dsp_call)`)。它不接收「這次呼叫是哪一份提案」當參數,而是呼叫 `_SCOPE.get()` 去讀一個模組層級的 `ContextVar`,這個變數由 `Executor._calls_for()` 在 `process_one()`/`reconcile_all()` 進入處理一筆/對帳一把鍵時 `set()`、離開時 `reset()`。也就是說,「這次 DSP 呼叫屬於哪個任務/哪把鍵」不是沿著函式呼叫鏈顯式往下傳,而是靠一個全域可變狀態在背後隱性提供。

但專案裡解決過完全同一類問題(同一個 DSP 用戶端被多個呼叫端共用、每次 HTTP 呼叫要各自歸屬到正確的身分再記錄),既有做法是顯式傳參數,不是隱性通道:

- file: `src/rtb/analyzer/dsp_client.py:33` — `OnDspCall = Callable[[TaskRow, str, str, float], None]`,`task: TaskRow` 整段從 `fetch(task, now)` 一路顯式傳到 `_get(...)` 再到 `on_call(task, endpoint, outcome, latency_ms)`,呼叫端(`instrumented.py`)拿到的 `task` 就是回呼參數本身,沒有任何全域/隱性狀態。
- file: `src/rtb/analyzer/instrumented.py:65-96` — `InstrumentedSubmit`、`InstrumentedOperationLookup` 遇到「協定簽章沒有任務身分」時,做法是每次呼叫前用當下讀到的 `TaskRow` 另建一個新的包裝實例(構造時綁定,不是共用單例、不是用背景狀態去猜),文件裡還特別寫明「不能是長壽命、重複給不同任務列共用的單一實例」。
- 甚至連「執行緒各自範圍」這個需求,專案測試裡既有的慣用工具也是 `threading.local()`,不是 `contextvars.ContextVar` — file: `tests/executor/test_multi_worker.py:57,114`(`local = threading.local()`、`paused = threading.local()`),而且該測試檔本身就是「同一個 DspPort/`h.dsp` 假物件被多個工作者執行緒共用」這個確切場景的既有測試模式(見該檔第 2-9 行的說明與 `worker()` 建構式)。`src/` 底下在這次改動之前完全沒有出現過 `ContextVar`(全庫掃描為 0 筆)。

會出什麼錯的行為:這是在專案裡引入原本沒有的第二種「把身分傳給共用回呼」的做法,而且解決的正是姊妹檔案 `analyzer/dsp_client.py` 已經用顯式參數解掉的同一個問題。用隱性通道帶來的具體風險:`record_dsp_call` 讀不到 scope 時只是靜默地什麼都不記(`if scope is not None:`,沒有任何例外或告警),一旦呼叫路徑經過任何不會自動延續 context 的邊界(例如日後把 `threading.Thread` 換成 `concurrent.futures.ThreadPoolExecutor`/`asyncio.to_thread`,或有人在 `_calls_for` 範圍外呼叫 `dsp.read_campaign()` 之類的方法),DSP 呼叫紀錄會悄悄漏記,且沒有測試以外的機制能發現——這正好牴觸這次修補自己在 docstring 裡強調的「結果寫入可能回滾,呼叫本身確實發生了,不能跟著消失」的稽核目的。

建議修法:比照 `analyzer/dsp_client.py` 現有解法,把 `CallSubject`(或至少 `task_id`/`key`)當成 `DspPort.listen()` 回呼簽章的顯式參數(即 `OnDspCall = Callable[[CallSubject, DspCall], None]`),由 `Executor` 在呼叫 `self.dsp.read_campaign(...)`/`write(...)`/`void(...)` 之前用當下的 `proposal`/`key` 建一個綁定好身分的閉包或包裝物件傳進 `DspClient._send`,取代模組層級 `_SCOPE: ContextVar` 這條隱性通道,讓漏記或歸錯身分在型別層/呼叫點就顯性暴露,而不是靠背景狀態默默兜底。
