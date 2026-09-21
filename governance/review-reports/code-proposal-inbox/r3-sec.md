severity: major

# r3 資安審查(攻擊者視角,第 3 輪,全新初讀)

方法:把工作目錄 `src` 複製到臨時目錄,起真的 InboxServer(Python 3.14.6),用 http.client 與原始 socket 送請求;沒有改任何專案檔。行為以工作目錄檔案為準,並與凍結 patch 對過。

## 前一輪(r2-sec)四項的實測結論
- r2-1 名額壟斷:未修,與文件所述一致(每小時 8 份不同任務、有效期一小時即可佔滿,實測第 9 份得到 503 `inbox_full`)。作者已寫入節點並附 REVISIT 2026-10-20。本輪重評為 minor,見 finding 2。
- r2-2 已過期列不清:未修,而且本輪找到更快、且不吃名額的寫入路徑,並實測出查詢會隨列數線性變慢,升為 major,見 finding 1。
- r2-3 事件表被沖:已修好。事件已依代碼分別設上限;先造成 1 筆 `inbox_full`,再灌 1200 份 `revision_out_of_order`,結果 `inbox_full` 1 筆仍在、`revision_out_of_order` 剩 200 筆。
- r2-4 連線上限:未修,行為如 r2(70 條閒置連線時,合法請求得到 RemoteDisconnected)。作者已寫入並附 REVISIT。重評為 minor,見 finding 3。

## 已驗證擋得住(無洞)
- Host:`evil.com`、`127.0.0.1:PORT.evil.com` 為 400;HTTP/1.0 缺 Host 為 400;絕對 URI 目標不繞過 Host 檢查。
- Origin(含小寫標頭名)為 403;重複 Content-Type 為 400 `duplicate_header`;`text/plain` 為 415;`Application/JSON ; charset=utf-8` 大小寫與空白正常放行。
- `Transfer-Encoding: chunked` 為 411;`Transfer-Encoding` 空值時走 Content-Length,連線是 HTTP/1.0 一請求一連線,沒有請求走私面。
- 路徑:`/proposals/`、`/Proposals`、`/proposals;x` 皆 404;`//proposals` 被標準庫收斂成 `/proposals`(正常)。
- 深層巢狀 `[[[…` 六萬層與 `{"a":{"a":…` 三萬層:本文超過 64 KB,回 413,不到 JSON 解析。
- 重複 JSON 鍵取最後一個,只有一個解析器,沒有前後解讀不一致。
- 錯誤回應皆為固定代碼,無請求內容回顯;SQL 全部參數化。
- 與時間相關:極端年份、無時區、有效期超過一小時皆在解析器被拒;到期比較用固定 UTC 字串。
- 事件與重送:重送同一份提案不重複記事件;拒收路徑照樣提交、不留半寫入。
- 提案取代:同一任務的下一個修訂會把待處理的前一版標為已取代。任何呼叫者只要知道任務編號,就能用下一個修訂取代別人的待處理提案(實測 `victim` 任務被取代)。這屬「無身分認證」的已知缺口(節點已載明,Phase 3 處理),取代者仍要通過執行前授權,本輪不另立 finding。

## 發現

### 1. 用「取代鏈」寫入不佔名額的無限列,資料庫無上限長大,且每個請求的全表掃描讓收件口永久變慢
severity: major
blocking: 是 這不是名額壟斷那種一小時自癒的癱瘓:寫入不受 `max_pending` 限制、沒有任何刪除路徑、重啟也不會恢復,而且列數一多,每個請求的掃描讓正常呼叫者被逾時擋掉;還會吃光整台機器的磁碟,波及分析行程以外的元件
引句:「MAX_DECISION_LIFETIME = timedelta(hours=1)」
實測:同一任務連續送修訂 1、2、3…(每份都合法),每份取代前一份,取代不增加待處理數,所以上限 8 完全不起作用。1500 份寫入 2.77 秒(約 540 份/秒),1499 列 `superseded`,每列約 1.7 KB(欄位塞滿時可到約 6 KB;修訂上限 1,000,000,每個任務一路可寫到 1e6 列,任務編號無上限)。接著把表補到指定列數,量一個合法新請求的延遲:空表 2 毫秒;20 萬列 148 毫秒;100 萬列 741 毫秒(檔案 2 GB)。原因是 `WHERE state = 'pending'` 的更新與 COUNT 沒有索引,每個請求在寫入鎖內全表掃描,請求全部串行;100 萬列時吞吐約 1.3 請求/秒,同時幾個請求就會撞上 5 秒忙碌逾時,回 503 `busy`。攻擊者用約 30 分鐘就能做到,之後沒有自動恢復,只能人工動資料庫。節點只寫「沒有收件表清理與保留期限:會累積」,沒有寫出「可被主動灌入、灌入不吃名額、會拖垮每個請求」。到期回頭日 2026-10-20 太晚,也沒有機械守衛。修法方向:已取代與已過期的列設保留期限或總列數上限、替 `state` 加索引、每任務修訂數或每任務寫入速率設限。
file: `src/rtb/executor/inbox_store.py:166`

### 2. 名額仍可被無認證地持續佔滿(第 2 輪 finding 1 的殘留)
severity: minor
blocking: 否 影響是分析行程(唯一呼叫者)自己的流程拿到可重試的 503;佔位一小時內自癒,不是越權寫入,作者已如實記在節點並附回頭日期與 Phase 3 的真正解法
引句:「if proposal.decision_expires_at > now + MAX_DECISION_LIFETIME:」
實測:9 份不同任務、到期各為一小時內,第 9 份得到 503 `inbox_full`(retryable 為 true)。上限 1 小時確實把佔位時間壓短,但攻擊者每小時 8 份就能續佔。我不因為作者已承認就降級,也不因為「可能」就升級:真實影響只落在分析行程自己,且會在期限後自行恢復,所以定 minor。與 finding 1 的差別在於後者不自癒也不吃名額。
file: `src/rtb/executor/inbox_store.py:163`

### 3. 全域連線上限讓一個慢速用戶端擋住合法呼叫者(第 2 輪 finding 4 的殘留)
severity: minor
blocking: 否 只在本機可達且只癱瘓唯一呼叫者自己,閒置逾時後恢復;已寫入節點並附 REVISIT 2026-10-20
引句:「if not self._slots.acquire(blocking=False):」
實測:同一行程開 70 條只連線不送資料的連線後,合法請求得到 RemoteDisconnected(連線被立即關閉)。攻擊者每個閒置逾時(預設 10 秒)重開一批就能維持;滴灌可佔到 30 秒總期限。沒有為合法呼叫者保留名額,也不分來源。
file: `src/rtb/httpkit.py:63`

### 4. `decision_created_at` 沒有和現在比對,可以填未來時間
severity: minor
blocking: 否 目前只有有效期被綁在 `now` 上,建立時間只被要求「早於到期、相差不超過一小時」,不影響收件正確性,也沒有下游在使用它;但之後若有階段用它排序或判斷新鮮度,就會被偽造
引句:「if expires - created > MAX_DECISION_LIFETIME:」
實測:`decision_created_at` 設為現在加 2000 秒、到期設為現在加 3000 秒,回 201 並存入。呼叫者可任意填「決策建立時間」,收件口沒有用它自己的接收時間(`received_at`)校驗。建議要求 `created <= now`(容許少量時鐘偏差),或文件寫明此欄位不可信、下游不得依賴。
file: `src/rtb/domain/proposal.py:243`

## 已讀檔案
- file: `src/rtb/domain/proposal.py:1`
- file: `src/rtb/executor/inbox_server.py:1`
- file: `src/rtb/executor/inbox_store.py:1`
- file: `src/rtb/executor/ruff.toml:1`
- file: `src/rtb/dsp/ruff.toml:1`
- file: `src/rtb/dsp/server.py:1`
- file: `src/rtb/httpkit.py:1`
- file: `src/rtb/sqlitekit.py:1`
- file: `tests/domain/proposal_samples.py:1`
- file: `tests/domain/test_proposal.py:1`
- file: `tests/domain/test_proposal_hash.py:1`
- file: `tests/executor/conftest.py:1`
- file: `tests/executor/test_inbox_server.py:1`
- file: `tests/kit/test_httpkit.py:1`
- file: `tests/kit/test_shared_base.py:1`
- file: `tests/kit/test_sqlitekit.py:1`
