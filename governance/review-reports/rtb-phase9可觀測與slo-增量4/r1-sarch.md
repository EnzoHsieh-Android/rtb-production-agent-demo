severity: major

### F1 用測試包一層 DSP 用戶端加故障標頭這條路徑,被專案自己的封閉列舉標頭擋死,跟既有端到端測試的做法不是同一種
severity: major
blocking: 是
引句:「測試端包一層 DSP 用戶端,只在寫入呼叫上加故障標頭」
說明:字面上要做的是「拿執行迴圈真正在用的那支 `DspClient`,包一層讓它在寫入呼叫上多帶一個 `X-Fault` 標頭」。但這條路在現有程式碼裡走不通,而且跟既有端到端事故測試處理同一個限制的做法不是同一種:
- `src/rtb/httpclient.py:24-28` 的 `ClientHeader` 是封閉列舉,只有 `IDEMPOTENCY_KEY`、`CAPABILITY` 兩個成員;`request_json`(`src/rtb/httpclient.py:60-61`)對每一個標頭鍵做 `isinstance(key, ClientHeader)` 檢查,不是成員就 `raise TypeError`。
- `src/rtb/executor/dsp_client.py:62`、`:107` 的 `write()`/`void()` 內部直接寫死 `headers = {ClientHeader.IDEMPOTENCY_KEY: key, ClientHeader.CAPABILITY: token}`,呼叫端(包括包一層的子類別若透過同一支 `request_json` 送出)沒有任何參數可以多塞一個 `X-Fault` 進去。
- 這正是專案自己記下的合約(`docs/rtb-production-agent-demo-knowledge/Systems/共用行程基礎.md` 的 ★INVARIANT★:「分析行程送不出故障注入標頭…而共用用戶端的標頭只接受封閉列舉的成員」,執行端用的是同一份 `httpclient.py`,同樣受限)。
- 既有的真端到端測試 `tests/executor/test_execution_e2e.py:1-5` 就是為了解掉同一個限制才寫的,原文明講:「執行行程的用戶端送不出故障注入標頭(共用用戶端的封閉列舉擋掉),所以故障改由測試在 DSP 伺服器那一側排定」——做法是在 DSP 伺服器端掛一個 `PlannedHandler`(依請求序列彈出 `(fault, offset)`,見 `tests/executor/test_execution_e2e.py:24-30`),不是在用戶端加標頭。另一條既有做法(`tests/dsp/test_server.py:42`)是測試直接用低階 HTTP 用戶端(不經過生產用的 `DspClient`)送 `X-Fault`,那是在測 DSP 本身,不是端到端演練。
- 增量 4 描述的第三種做法(包生產用戶端加標頭)兩邊都不是:如果真的用共用 `request_json` 送,會在測試一啟動就 `TypeError`;如果為了塞標頭改成不經共用用戶端的另一套發送邏輯,那麼被測的就不是執行端真正在用的那條程式路徑,整支「真的 DSP 伺服器 + 真的執行迴圈」端到端測試名不副實。
- 具體例:實作者照字面寫一個 `class FaultyDspClient(DspClient)` 覆寫 `write()`,在 `headers` 字典裡加 `"X-Fault": "transient_5xx"` 再呼叫 `request_json`——一啟動測試就在第一次寫入呼叫時丟 `TypeError: 標頭名稱必須是 ClientHeader 的成員`,[S670]~[S674] 全部沒機會跑到。
建議改法:比照 `test_execution_e2e.py` 的 `PlannedHandler`/`plan` 機制,在 DSP 伺服器端依請求序列或冪等鍵決定要不要回故障,執行端仍用未修改的生產 `DspClient`。

### F2 「真的分析行程 + 時鐘由測試控制」在現有程式碼裡做不到:分析端沒有任何時鐘注入點
severity: major
blocking: 是
引句:「真的 DSP 伺服器、收件口、兩個執行迴圈工作者、分析行程,時鐘由測試控制」
說明:設計把「真的分析行程」跟「時鐘由測試控制」放進同一句話裡當成可以同時成立的組法,但分析端目前完全沒有可注入的時鐘:
- `src/rtb/analyzer/instrumented.py:47`、`:60`、`:95`、`:124` 四處都是直接呼叫 `datetime.now(UTC)`,沒有 `clock` 參數、沒有建構子選項、也沒有任何模組層級的時間函式可以替換。
- 對照既有的真端到端測試(`tests/analyzer/test_f4_end_to_end.py:35-39`)明講:「各服務與簽發都用真實時間:DSP 只容許 30 秒的時鐘誤差」——現有做法是遇到「真的分析行程」就整組服務都用真實時間,不混用測試時鐘;唯一支援可控時鐘的既有端到端測試(`tests/executor/test_f7_end_to_end.py`)反過來是完全沒有真的分析行程,DSP 也是行程內假物件。這兩種既有組法目前互斥,增量 4 想同時要「真的分析行程」與「測試控制的時鐘」,是第三種、程式裡不存在支撐它的組法。
- 具體例:實作者照字面把 `clock` fixture 撥快(例如撥過事故時段、撥過對帳期限)去驅動執行迴圈與燒損評估器,但分析行程那一側的 `instrumented.py` 仍記真實牆鐘時間;追蹤檢視(增量 1)排時間線時混進「執行端事件用測試時鐘、分析端事件用真實時鐘」兩套不同步的時間源,[S673] 要求的「時間不倒退」斷言會不穩定甚至翻紅——尤其測試時鐘一旦被撥到比真實時間更早或落後幾分鐘,分析端寫的任務建立時間跟執行端寫的取件/確認事件時間先後順序會顛倒。
建議改法:要嘛給分析端補一個跟執行端、DSP 同一顆的時鐘注入點(工程量不小,增量 4 沒編列),要嘛改成跟既有 F1–F3 執行端事故測試一樣不含真的分析行程、只在執行端層造出結果不明與重送,分析端那一段用既有「接續任務」等別的既有測試手法頂替,設計裡先講清楚選哪一種。

### F3 就算解決了 F2,DSP 伺服器的能力憑證時鐘偏差檢查沒有跟測試時鐘綁在一起,撥動測試時鐘會讓真的 DSP 判憑證過期
severity: major
blocking: 是
引句:「測試照控制的時鐘推進,每次推進都讓兩個工作者各跑一輪」
說明:即使只看執行端這一側(不牽涉 F2 的分析端問題),用「真的 DSP 伺服器」搭配「可以任意撥動的測試時鐘」也有一個既有測試已經解過、但增量 4 沒交代要照做的細節:
- `src/rtb/dsp/server.py:250` 的 `DspServer.__init__` 預設 `clock: Callable[[], float] = time.time`,能力憑證的到期檢查(`src/rtb/dsp/server.py:173`、`:193`)是拿 `self.server.clock()` 跟憑證的 `exp` 比,不是拿測試時鐘比。
- 憑證有效期只有 120 秒、DSP 端上限 300 秒(`src/rtb/executor/capability_signer.py:24`);增量 4 的演練要把時鐘撥過「事故時段的一半」再撥過「對帳超過 10 分鐘(示範縮短後也還有數十秒)」,只要測試時鐘跟 DSP 伺服器讀到的真實牆鐘時間差超過這個量,寫入請求送到 DSP 就會被判 `capability_expired`,跟事故劇本(前兩次收到的是「暫時 5xx」)混在一起,[S670]、[S673]、[S674] 的斷言會因為錯誤原因翻紅或誤判。
- 既有做法(`tests/executor/test_execution_e2e.py:34-37`)已經示範怎麼避開:把 DSP 伺服器建構成 `clock=lambda: clock().timestamp() + self.offset`,讓 DSP 伺服器讀的是「同一顆測試時鐘」而不是系統真實時間。增量 4 的最小設計沒有提這一段配線,照「真的 DSP 伺服器」字面直接用預設建構,就是漏掉這個既有模式。
建議改法:在最小設計裡明寫「這支端到端測試的 DSP 伺服器建構時,`clock` 參數綁同一顆測試時鐘」,並照既有 `PlannedDsp` 的寫法處理(必要時保留 `offset` 機制模擬前掃 pf2 提到的提交後逾時情境)。

共 3 條,blocking 3 條。
