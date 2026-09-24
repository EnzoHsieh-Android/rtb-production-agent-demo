severity: major
Good, quotes confirmed verbatim against the patch. Now producing the final report.

---

severity: major

### 1. DspPort 把 on_call 塞進每支方法簽章,跟分析端既有的兩種顯式做法都不同,是第三種寫法
severity: major
blocking: 是

引句:「呼叫端綁好這次是為了哪份提案(Phase 9 增量 1 代碼審第 1 輪:改成顯式」
引句:「呼叫回呼(Phase 9 增量 1,比照分析端 DSP 用戶端的 on_call):每一支公開方法都收一個必填的關鍵字」

觸發情境:任何要新增或替換 `DspPort` 實作(例如測試替身、之後要多接一個 DSP 供應商)的人,現在被強制在 `read_campaign`/`write`/`operation_version`/`operation_record`/`void` 這五支公開方法的簽章上都加一個必填的 `on_call` 關鍵字參數,而且呼叫端每次呼叫都要現場建一支綁好 `proposal`/`key` 的閉包(`self._calls(proposal, key)`)傳進去。

會出什麼錯的行為:這批修正的 docstring 自稱「比照分析端 DSP 用戶端的 on_call」,但分析端實際上從沒有把 on_call 放進協定簽章本身。分析端對「同一次呼叫要記錄可觀測資訊」這個問題,一貫是兩種做法之一:
- `src/rtb/analyzer/dsp_client.py:130-137` 的 `make_client(base_url, timeout_seconds, on_call=None)`:on_call 在**建構時**用閉包綁一次,回傳的 `fetch()` 呼叫簽章(`(task, now)`)完全不帶 on_call。
- `src/rtb/analyzer/instrumented.py:65-124` 的 `InstrumentedSubmit`/`InstrumentedOperationLookup`:呼叫端在**每次呼叫前**現造一個包裝物件把當下的 `task` 綁進建構子,但被包裝的協定(`src/rtb/analyzer/flow.py:109-126` 的 `Submit`/`OperationLookup`)簽章本身完全不變,一樣只收 `(proposal)`、`(key)`。

也就是說,分析端「顯式傳」的意思是「呼叫端每次自己建一個綁好上下文的物件」,而不是「把回呼參數塞進共用協定的方法簽章」。這批修正走的是第三種、分析端沒有先例的做法:直接改寫 `DspPort` 協定本身的方法簽章。更值得注意的是,這一支檔案自己在這次修正之前(增量 1、r1)用的正是建構子注入 + `listen()`(見 patch 中被刪掉的 `def __init__(self, base_url, timeout_seconds, on_call=None)` 與 `def listen(self, on_call)`),那個版本才是真的貼近分析端 `make_client` 的建構時綁定寫法;這次改動反而是從「跟分析端一致」的舊寫法,換成一個分析端沒有的新寫法。

建議修法:比照 `instrumented.py` 的包裝器模式,讓 `DspPort` 協定簽章維持原樣(不帶 on_call),改由呼叫端在建立每次任務所需的協作者時,包一層帶著 `subject` 的 wrapper(或退回 r1 的建構時 `listen()` 綁定,只是要解決「同一個用戶端給多個工作者共用」的問題可以讓每個工作者各自持有自己的 `DspClient` 實例)。

### 2. Executor 的 `_pending`/`flush_calls` 是跟收件口 `record_event` 不同的第二種「盡力而為寫入」機制,且忙碌會被吞掉、繞過既有的忙碌斷路器
severity: major
blocking: 是

引句:「把欠著的呼叫紀錄用一個新的短交易寫進去;資料庫忙碌就留著下次再補,永遠不丟忙碌例外」
引句:「waiting = list(self._pending)」
file: `src/rtb/executor/inbox_store.py:1468-1472`
file: `src/rtb/executor/runner.py:62-79`

觸發情境:DSP 呼叫紀錄要寫入時撞到資料庫忙碌(`InboxBusy`)。

會出什麼錯的行為:專案裡既有「非關鍵資料盡力而為寫入」的先例是收件口的 `record_event`(`src/rtb/executor/inbox_store.py:1460-1472`):撞到 `(sqlite3.Error, DatabaseBusy)` 就整個放棄、`return`,不緩衝、不重試,是「試一次、不行就丟」的單次模型。這批修正給 Executor 另外引入了一套形狀完全不同的機制:把還沒寫進去的呼叫紀錄留在行程記憶體的 `_pending` 清單裡(`_pending: list[_PendingCall] = field(default_factory=list, init=False, repr=False,`),之後在 `process_one`、`reconcile_all` 每把鍵結束、`runner._serve` 收尾等好幾個時機反覆嘗試補寫,直到成功或行程真的結束才放棄。這是「緩衝 + 重試」模型,跟 `record_event` 的「試一次就放棄」是兩種不同的盡力而為寫入策略,而且沒有沿用 `record_event` 現成的作法。

更關鍵的是,這個緩衝沒有筆數上限(`attempt_store.py` 裡對嘗試紀錄都有 `MAX_ROWS_PER_KEY`、`MAX_UNRESOLVED` 這類上限,`_pending` 沒有對應機制),而且它撞到忙碌時是在 `flush_calls()` 內部用 `except InboxBusy: return` 自己吞掉(不會往外丟),不會被 `runner.py:62-79` 既有的忙碌斷路器(`busy_streak` 累計到 `BUSY_LIMIT` 就 `EXIT_BUSY` 停機)偵測到——那個斷路器只看 `process_one`/`reconcile_all` 主要工作交易本身丟出的 `InboxBusy`。也就是說,只要主要工作交易(例如沒有新訊息、對帳沒有要處理的鍵)大多順利成功、但 `flush_calls` 自己開的短交易持續在跟其他工作者搶鎖輸掉,行程可以完全不觸發 `EXIT_BUSY` 停機、繼續無限期跑下去,`_pending` 在記憶體裡持續累積,直到行程真的當機或被外部訊號結束才把這批 DSP 呼叫紀錄靜默丟掉。

建議修法:要嘛比照 `record_event` 的「試一次就放棄」模型(允許少記幾筆 DSP 呼叫紀錄,不緩衝),要嘛如果決定要重試,至少要幫 `_pending` 訂一個上限(滿了就照 `record_event` 的邏輯放棄最舊的或全部放棄),並讓 `flush_calls` 持續忙碌的情形也算進 `runner.py` 既有的 `busy_streak`/`EXIT_BUSY` 斷路器,不要讓它完全隱形。

---

補充(材料以外查證,未列為發現):`src/rtb/httpclient.py` 新增的 `UnreadableResponse(ValueError)`(帶 `status` 屬性)雖然是這支檔案第一個自訂例外類別,但跟專案其他層既有的「例外子類別帶結構化欄位」慣例(例如 `src/rtb/executor/execution.py` 的 `DspUnavailable.__init__(self, message="", failure=None)`)是同一種做法;繼承 `ValueError` 也保住了既有呼叫端(`src/rtb/ops/trace.py` 的 `except (OSError, ValueError)`、分析端 `dsp_client.py` 的 `except Exception`)不必修改就能相容,`executor/dsp_client.py` 裡 `except UnreadableResponse` 也正確放在 `except (OSError, ValueError)` 之前。這處沒有發現跟既有做法不一致的地方。
