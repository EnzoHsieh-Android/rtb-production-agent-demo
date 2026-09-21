severity: major

# r1 資安審查(攻擊者視角)

方法:把 src 複製到臨時目錄,起真的 InboxServer,用原始 socket 送請求驗證(未改任何專案檔)。

已驗證「擋得住」的項目(無洞):
- Origin:`Origin: null`、空值 Origin 皆 403;`Content-Type` 大小寫混寫、帶參數可過(正常),`text/plain` 為 415,重複 Content-Type 為 400。
- Host:`evil.com`、`127.0.0.1:port.evil.com` 為 400;HTTP/1.1 缺 Host 為 400;僅 HTTP/1.0 缺 Host 放行(瀏覽器一定送 Host,DNS rebinding 進不來)。
- 走私與路徑:Transfer-Encoding 411、重複 Content-Length 400、HTTP/1.0 每請求斷線無殘留;`/proposals/../proposals`、`;x`、`?y` 皆 404;絕對 URI 仍受 Host 檢查。
- 故障標頭:旗標關閉時 `X-Fault`(含空值)一律 400,且在讀本文與開資料庫之前就擋掉,無狀態變化。
- SQL:全部查詢皆 `?` 參數化;`a'; DROP--` 任務編號被解析器拒為 400。
- 回顯:欄位名、腳本字串、非法 UTF-8 皆只回固定錯誤代碼,不回顯內容。
- payload 入庫後本 patch 內無任何讀出路徑。

### 1. 到期時間無上限,不同任務編號可永久佔滿全部名額
severity: major
blocking: 是 任一本機行程可用 8 個請求讓收件口永久拒收合法提案,且沒有任何清理或驅逐路徑可恢復
引句:「if proposal.decision_expires_at <= now:」
實測:送 8 個不同 task_id、`decision_expires_at` 設 9999-12-31,全部 201 進入 pending;第 9 個(含合法的 30 分鐘提案)得到 503 `inbox_full`,且回應標 `retryable: true`,合法呼叫者會無限重試。名額只會因「到期」或「同任務更高修訂取代」釋放;前者被攻擊者控制,後者要知道並搶先送對方任務的下一修訂(無認證,亦可被對方再蓋回)。到期只擋「已過期」,不擋「過遠」。文件宣稱洪水受「到期時間」限制,實際上限制不成立。
file: `src/rtb/domain/proposal.py:234`(只檢查到期晚於建立,無最大時效)
建議:在解析器或收件交易加最大有效期(例如建立時間起算固定上限,且相對 now)。

### 2. 極端時區的時間戳讓 content_hash 溢位,回 500 並在 stderr 印堆疊
severity: minor
blocking: 否 交易會回滾、無狀態損壞,只是可無限製造 500 與 traceback 雜訊
引句:「moment = getattr(proposal, field).astimezone(UTC)」
實測:`decision_expires_at: "9999-12-31T23:59:59-12:00"` 或 `"0001-01-01T00:00:00+14:00"` 通過解析器,在 `accept` 內 `astimezone(UTC)` 丟 OverflowError,回 500 `internal_error`。呼叫者無法分辨是永久錯誤(retryable false 是對的,但被當成伺服器故障)。修正 1 的上限可順帶消除多數,但應讓解析器拒絕無法轉 UTC 的時間。
file: `src/rtb/domain/proposal.py:288`

### 3. 無效請求即可洗掉事件表,抹除稽核證據
severity: minor
blocking: 否 只影響事後追查,不影響收件正確性;文件已宣稱有筆數上限
引句:「DELETE FROM inbox_events WHERE id <= (SELECT MAX(id) FROM inbox_events) - ?」
實測:1200 個 `{"x":1}` 請求(0.7 秒)後,事件表只剩 1000 筆 `invalid_proposal`,先前的 `inbox_full`、衝突事件全被擠掉。無效請求本身每次都寫事件(且每次拿一次寫入鎖),攻擊者不需有效提案就能覆寫紀錄。建議無效請求的事件另計上限或合併計數,不與衝突類事件共用額度。

### 4. 慢速滴灌本文可繞過逾時,執行緒無上限
severity: minor
blocking: 否 僅本機可達(只綁回送位址),且屬共用基礎既有行為,但新入口把它暴露給不可信內容
引句:「parsed = parse_proposal(self.read_json())」
實測:socket 逾時設 1 秒,宣告 Content-Length 60000,每 0.6 秒送 1 位元組,連線持續 4.8 秒以上仍未被切斷(逾時是每次 recv,不是整個請求)。ThreadingHTTPServer 每連線一執行緒、無並行上限,可累積耗盡。建議整個請求設總時限與並行連線上限。
file: `src/rtb/httpkit.py:171`
