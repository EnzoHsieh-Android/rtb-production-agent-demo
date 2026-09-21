severity: major

# r2 資安審查(攻擊者視角,最終版)

方法:把工作目錄 src 複製到臨時目錄,起真的 InboxServer(本機 Python 3.14.6),用 http.client 與原始 socket 送請求;沒有改任何專案檔。凍結 patch 與工作目錄內容逐檔比對過,行為以工作目錄為準。

## 第 1 輪四項發現的實測結論
- 極端時間溢位:已修好。`9999-12-31` 與 `0001` 系列在解析器被拒(400 `invalid_proposal`),無 500。
- 到期無上限:只修一半,見 finding 1。
- 事件表被沖:只修一半,見 finding 3。
- 慢速滴灌:總期限有效(Content-Length 5000、每 0.5 秒送 1 位元組,第 31.0 秒被切斷,BrokenPipe),但換來連線耗盡面,見 finding 4。

## 已驗證擋得住(無洞)
- 有效期一天上限:created 與 expires 的組合都試過。有效期 25 小時為 400;剛好 24 小時為 201;created 在 23 小時前且 expires 在 1 小時後為 201(合理,有效期仍 24 小時內);`-12:00` 時區寫法照常處理。expires 比 now 晚超過一天則由收件交易回 422 `expiry_too_far`(單元測試覆蓋,本輪未另送)。時區無法繞過:比較前都換 UTC,字串比大小用固定 UTC 格式。
- Host:`evil.com` 400;HTTP/1.1 缺 Host 400;HTTP/1.0 缺 Host 在收件口也是 400(`require_host` 生效)。
- Origin:`Origin: null`、空值 Origin 皆 403;Content-Type `text/plain` 為 415;帶參數的 `application/json;charset=x` 放行(正常)。
- 故障標頭:旗標關閉時 `X-Fault: crash_after_commit` 與空值都是 400 `fault_injection_disabled`,在讀本文、開資料庫之前就擋。
- SQL:所有查詢皆 `?` 參數化;`a'; DROP--` 任務編號加 `<script>` 欄位名只得到固定的 `invalid_proposal`,無回顯。
- 拒收路徑提交:`accept` 內所有拒收都在 supersede 的 UPDATE 與 INSERT 之前,拒收照樣提交(只留下「標過期」)。
- 事件去重的元組比較可用;無效請求不碰資料庫不記事件。

## 發現

### 1. 一天上限只把「永久佔滿」縮成「每天重送八個請求」,名額仍可被無認證地永久壟斷
severity: major
blocking: 是 第 1 輪 blocker 的根因(名額只靠到期釋放、無驅逐、無每來源或每任務限額)仍在,攻擊者一天只需 8 個請求就能讓合法提案永遠得到可重試的 503
引句:「if proposal.decision_expires_at > now + MAX_DECISION_LIFETIME:」
實測:送 10 個不同 task_id、到期為 23 小時 59 分,前 8 個 201,其後全部 503 `inbox_full`(`retryable: true`)。這批在 24 小時內不會釋放;一到期,攻擊者再送 8 個即續佔。合法呼叫者會被鼓勵無限重試。修法把佔位時間變成有界,但沒有改變「任何本機行程都能讓收件口拒絕服務」這件事。可接受的緩解方向:低名額保留給已有 pending 的任務、對到期時間按佔用量分級、或收件口綁認證。若判定此為設計取捨(僅回送位址、無認證),必須寫進節點的殘餘風險並附回頭看的條件,現在文件宣稱防住了洪水。
file: `src/rtb/executor/inbox_store.py:163`

### 2. 已過期的提案列永不清除,用一秒到期的提案即可讓資料庫無上限長大
severity: minor
blocking: 否 僅本機可達且需持續發請求,後果是磁碟與每個請求的全表掃描變慢,不影響收件正確性
引句:「MAX_DECISION_LIFETIME = timedelta(hours=24)」
實測:每輪送 8 個到期為 1 秒的提案(各帶 400 字元 risk_summary),等 1.2 秒,重複 3 輪,`proposals` 表為 pending 8、expired 16;expired 列沒有任何刪除路徑,`state='pending'` 的 UPDATE 與 COUNT 也沒有索引。名額上限只限制 pending,不限累積列數。速率約每秒 8 列,約一個 payload 幾 KB。建議對已過期列設保留期限或總筆數上限,並替 state 加索引。
file: `src/rtb/executor/inbox_store.py:147`

### 3. 事件去重只比最新一筆:任何格式合法的不同提案都能洗掉事件表(不只交替兩種)
severity: minor
blocking: 否 只影響事後追查,沒有收件正確性影響;比第 1 輪門檻高但仍幾乎無成本
引句:「if latest == (code, task_id, revision, digest):」
實測:1200 個不同 task_id、revision 2 的合法提案(各回 409 `revision_out_of_order`),0.84 秒後事件表 1000 筆全是 `revision_out_of_order`,先前的 6 筆 `inbox_full` 被擠掉。r2-s1 與 r2-x1 說的「交替 A、B」太保守,只要換任務編號就每筆都是新事件。第 1 輪報告的修法「無效請求不記」只擋掉垃圾請求,沒擋合法格式的請求。建議事件依代碼分開計上限、或對同代碼事件按時間窗合併計數。
file: `src/rtb/executor/inbox_store.py:217`

### 4. 連線上限與總期限讓慢速用戶端能持續擋住合法呼叫者;計時器另佔一條執行緒
severity: minor
blocking: 否 僅回送位址可達,且是「有界資源」的設計取捨,但沒有為合法呼叫者保留名額
引句:「if not self._slots.acquire(blocking=False):」
實測:socket 逾時設 2 秒,開 70 條只連線不送資料的連線後,合法請求得到 RemoteDisconnected(被立即關閉);等閒置逾時過後恢復 201。預設閒置逾時 10 秒,攻擊者每 10 秒重開 64 條連線就能維持拒絕服務;送滴灌的連線可佔滿 30 秒。每條連線另有一個 Timer 執行緒,`cancel()` 後不等待退出,所以上限 64 實際同時可有約 128 條執行緒(建立成本低,未見累積:實測全部連線關閉後執行緒數回到 3)。與 finding 1 同屬本機可達的可用性面,建議記為殘餘風險,並考慮較短的閒置逾時或依來源限制。
file: `src/rtb/httpkit.py:100`

## 已讀檔案
- file: `src/rtb/domain/proposal.py:1`
- file: `src/rtb/executor/inbox_server.py:1`
- file: `src/rtb/executor/inbox_store.py:1`
- file: `src/rtb/executor/ruff.toml:1`
- file: `src/rtb/dsp/ruff.toml:1`
- file: `src/rtb/httpkit.py:1`
- file: `src/rtb/sqlitekit.py:1`
- file: `tests/domain/proposal_samples.py:1`
- file: `tests/domain/test_proposal.py:1`
- file: `tests/executor/test_inbox_server.py:1`
- file: `tests/kit/test_httpkit.py:1`
- file: `tests/kit/test_shared_base.py:1`
- file: `tests/kit/test_sqlitekit.py:1`
