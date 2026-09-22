severity: major

# 資安審查(攻擊者視角):r1 — dsp_client / inbox_client / httpclient / policy / tool_calls / Evidence.payload

審材:`governance/review-reports/code-analyzer-network/r1-snapshot.patch`(凍結)。威脅模型依據 `Projects/RTB_Agent_Phase0架構.md`、`Projects/RTB_Phase2任務流程_計劃.md` 增量 4 設計(S42~S54)。

## 1. httpclient.request_json 沒有關閉 HTTP 重新導向,「網址寫死」的信任假設可被目標主機用 3xx 打破(SSRF pivot)

severity: major
blocking: 是 理由:實際攻進去要看的是「DSP/收件口是否可信」這個假設本身有沒有守住;`request_json` 用的是 `urllib.request.urlopen` 的預設 opener,預設會自動跟隨 301/302/303/307/308,而且完全沒有限制跳轉後的主機。程式碼自己的註解主張「網址由呼叫端寫死,不是外部輸入」,但這句話只保證*第一個*請求的目的地,沒有保證*最終*回應是誰給的——只要應答端(DSP 或收件口)回一個 3xx,`request_json` 就會默默把請求接到任意主機,呼叫端(`dsp_client`/`inbox_client`)完全看不到中間發生過跳轉,拿到的就是最終主機的 200 回應,直接被當成合法的 DSP 現況/指標或收件口回應處理。

引句:「只用來打本機的 DSP/收件口,網址由呼叫端(dsp_client/inbox_client)寫死,不是外部輸入」

**已實測驗證**(在暫存目錄起兩個本機 HTTP 伺服器,不動專案檔):用 `urllib.request.urlopen` 對一個回 302、`Location` 指到另一個埠的本機伺服器發出請求,結果拿到的是第二個伺服器的 200 回應與內容,呼叫端毫無感知地被導向了跟原本網址完全無關的主機。額外驗證:跳轉到 `file://` 這類非 http(s) scheme 時 urllib 會直接拋錯拒絕(不是這裡的風險),但**同 scheme 跨主機**(http→http)的重新導向完全沒有限制,這正是 `dsp_client`/`inbox_client` 實際會踩到的形狀。

為什麼這條在目前的威脅模型下算數:S44/S52 花了兩輪設計把「分析端連不到故障注入」這件事做到「唯一真正防線」(封閉列舉),但那道防線只管「呼叫端主動塞了什麼標頭」,完全沒有涵蓋「應答端能不能把呼叫端導去別的地方」。一旦 DSP 或收件口這一側出現任何能讓它吐出 3xx 的方式(例如日後接上真的外部 DSP、或 DSP 行程本身被攻陷、或有另一個較低危的開放重新導向漏洞),分析行程的 HTTP 用戶端就會被當成任意主機的探測/資料外洩跳板,而且完全繞過「只綁定回送位址」這條在**伺服器端**做的隔離——因為那條隔離只保護「誰能連進 DSP/收件口」,保護不到「DSP/收件口能叫用戶端連去哪裡」。

修法方向(不佔用你的行動,只是指出可行的最小改法):`request_json` 建一個停用重新導向的 opener(例如自訂 `HTTPRedirectHandler` 讓 `redirect_request` 回 `None`,再用 `build_opener(...).open(...)` 取代模組層級的 `urlopen`),讓 3xx 直接變成 `HTTPError`(呼叫端本來就有處理非 2xx 的路徑,不需要新邏輯)。

## 2. policy.decide() 對 DSP 數值沒有任何上限或來源檢查,一旦上一條的重新導向被觸發,budget/spend/impressions/clicks 可被攻擊者直接決定

severity: minor
blocking: 否 理由:這條**依附**於第 1 條才成立——目前程式庫裡沒有其他管道能讓外部內容進到 `policy.decide()` 的輸入;第 1 條若修掉,這條的實際可觸發性就消失。而且 `policy.py` 檔頭已明白承認「這不是交接文件後面階段要做的真正業務規則」,是示範規則,真正的合理性檢查本來就規劃留給後面階段。故不單獨擋。

引句:「new_budget = round(budget * (1 + BUDGET_INCREASE_FRACTION))」

`budget`、`spend`、`impressions`、`clicks` 全部直接讀自 DSP 回應的 payload,唯一的把關是型別檢查(`isinstance(..., int | float)`)與 `Proposal.__post_init__` 的「正整數、≤2^63-1」結構檢查;沒有任何「這個數字合不合理」「跟上一次觀察到的版本/預算差距是否誇張」的檢查。若第 1 條的重新導向被觸發,攻擊者能完全決定 `underpacing` 是否成立(進而決定 `risk_summary`/`reason_codes` 這兩個欄位**要不要出現**)以及 `new_budget` 的實際數值——這不是「數字比較大」而已,是「送進收件口的提案內容本身由攻擊者的回應直接決定」。

殘留風險說明:`RTB_Phase2任務流程_計劃.md` 的架構承諾「執行的那一刻執行行程仍要重讀 DSP 現況、重新授權」,理論上會在真正寫入 DSP 前再擋一次;但 Phase 3 執行行程目前**還沒有程式碼**,這個承諾目前只是文件,不能當成已生效的緩解證據。

file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase2任務流程_計劃.md:38`

## 3. httpclient 對回應本文沒有位元組上限,伺服器端的界限沒有鏡射到用戶端

severity: minor
blocking: 否 理由:目前 DSP/收件口都是本機模擬器,回應內容固定且小;真的要打爆記憶體需要對方(已經算可信的一側)主動配合送出超大回應,不是外部匿名攻擊者能直接觸發的路徑,列為防禦縱深缺口而非可直接利用的洞。

引句:「return response.status, json.loads(response.read())」

`src/rtb/httpkit.py` 對「收」的方向(伺服器讀請求本文)有明確的 `MAX_BODY_BYTES = 64 * 1024` 與逐段讀取上限;但 `httpclient.request_json`(「打」的方向)完全沒有對稱的上限,`response.read()` 會把整個回應一次讀進記憶體。若日後這支用戶端接上真的外部 DSP(計劃書已預告這是後面階段的方向),或本機 DSP 因為 bug/被攻陷吐出超大回應,分析行程沒有任何機制擋住。

## 4. ClientHeader 封閉列舉:檢查過 body、query string、urllib 標頭機制,沒有找到繞過空間

severity: clean

引句:「if not isinstance(key, ClientHeader):」

檢查了三個可能的繞過方向:
- **透過 body 夾帶**:`request_json` 的 `body` 參數只會被 `json.dumps` 進請求本文,不會被拿去組標頭;伺服器端 `read_fault()` 只呼叫 `self.single_header("X-Fault")` 讀實際 HTTP 標頭,完全不看請求本文。file: `src/rtb/httpkit.py:204`
- **透過 query string 夾帶**:同理,伺服器路由只有 `_get_metrics` 會解析 query string(`window` 參數),沒有任何路徑把 query string 內容轉成標頭判斷依據。
- **urllib 本身的機制**:`headers` 參數的型別是 `dict[ClientHeader, str]`,`request_json` 用 `isinstance(key, ClientHeader)` 逐一核對,不是這個封閉列舉的成員(包含任何手寫、拼接、f-string 組出來的字串)一律 `TypeError`;`ClientHeader` 目前只有一個成員(`Idempotency-Key`),`dsp_client.py`、`inbox_client.py` 這次的呼叫實際上連 `headers` 參數都沒有傳,X-Fault 沒有任何管道進得去。伺服器端本身也是兩層防護(見合約 S52 的邊界測試:沒開 `--fault-injection` 一律 400)。

（另見上方第 1 條:重新導向繞過的是「連到哪個主機」,跟這裡的「送出哪個標頭」是不同機制,不影響本條結論。）

## 5. dsp_client 組出的 URL 路徑(campaign_id)沒有路徑注入風險

severity: clean

引句:「state = _get(base_url, f"/campaigns/{task.campaign_id}", timeout_seconds)」

`task.campaign_id` 唯一的來源是 `TaskStore.create_task()` 寫入時驗證過的值,字元集被 `is_id()` 限制在 `[A-Za-z0-9._:-]`,不含 `/`、`%`、`?`、`#`、換行等任何能跨越路徑分段或注入 query/header 的字元;就算允許 `.` 兩個相連(`..`)組成路徑,DSP 伺服器端的路由本身也是各自獨立的正規表示式比對(`^/campaigns/([^/]+)$`),不是檔案系統路徑串接,所以就算收到 `/campaigns/..` 這種請求,對應到的仍是 `_get_campaign` 這個 handler、`campaign_id` 參數等於字串 `".."`,不會被拿去存取檔案系統或跳到別的路由。

file: `src/rtb/domain/_checks.py:11`(`ID_PATTERN` 字元集)、`src/rtb/domain/_checks.py:23`(`is_id()`)、`src/rtb/dsp/server.py:59`(路由用獨立正規表示式比對,不是路徑串接)

目前程式庫裡也還沒有任何對外入口能繞過 `create_task()` 直接餵一個未經 `is_id()` 驗證的 `campaign_id` 進資料庫。

## 6. tool_calls.outcome 只存例外類別名稱,沒有洩漏內部細節

severity: clean

引句:「self._record(task, type(exc).__name__, started)」

`InstrumentedEvidenceSource`/`InstrumentedSubmit` 失敗時只記 `type(exc).__name__`(例如 `RuntimeError`、`DspRequestFailed`),不記 `str(exc)` 或例外訊息全文——而例外訊息全文裡可能帶著 DSP 的錯誤內容、URL 路徑等細節(例如 `dsp_client.py` 的 `DspRequestFailed(f"{path} 回 {status}:{body.get('error', ...)}")`)。追過這些訊息全文實際上有沒有機會流到別處:`flow.py` 的 `_from_collecting_evidence`(蒐證失敗)與 `_from_proposed` 對 `SubmitBusy`/未定義例外(送出提案失敗)都是 `except Exception: return None`,直接丟棄例外本身,不寫進 `error_detail`;只有 `_from_analyzing`(Decide 丟例外)與 `SubmitRejectedPermanently` 這兩條路徑會把 `repr(exc)` 寫進永久保留的 `error_detail` 欄位,但這兩個例外的內容都是程式自己組的固定訊息或收件口回的封閉列舉錯誤代碼(`too_many_revisions`),不含 DSP/收件口回應的自由文字。

## 7. Evidence.payload 目前沒有被任何介面原樣回顯給不信任的一方

severity: clean

引句:「payload: MappingProxyType[str, PayloadValue]」

搜尋了整個 `src/`:讀取 `Evidence.payload` 的只有 `rtb/domain/evidence.py`(定義與驗證)、`rtb/analyzer/task_store.py`(存進/讀出 SQLite)、`rtb/analyzer/policy.py`(讀數字做決策計算)。這次的增量沒有任何 HTTP 端點把 `trace_for()`、`evidence_for()` 或 `Evidence.payload` 的內容組進 HTTP 回應——`trace_for` 目前只在 `task_store.py` 內定義,沒有任何呼叫端把它接到收件口或 DSP 的伺服器介面上。這個結論只對「這次增量」成立:一旦後面階段把 `trace_for`/`evidence_for` 接上任何對外(尤其是對不信任呼叫者)的介面,就要重新檢查 payload 內容(目前只驗證型別與筆數上限,沒有驗證字串長度或內容)會不會被原樣回顯。
