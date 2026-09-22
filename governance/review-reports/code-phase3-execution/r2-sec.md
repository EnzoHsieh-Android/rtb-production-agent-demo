severity: minor

### 1. 新測試檔案的理由性文件與實際呼叫模式不符

severity: minor
blocking: 否(不影響行為正確性,close-on-fail 修法本身對三支模組一視同仁地套用,不因這句理由錯誤而漏保護;純屬文件精度問題)
引句:「另外兩支是長活物件,但同一件事用同一種寫法」
`governance/review-reports/code-phase3-execution/r2-delta.patch` 新增的 `tests/test_migration_failure_closes_the_connection.py` 檔頭把 `dsp.store.CampaignStore` 與 `analyzer.task_store.TaskStore` 描述成「長活物件」,只有收件口(`InboxStore`)才是「每個請求都新開一個」。但實際上 `CampaignStore` 在 `src/rtb/dsp/server.py:103`(`DspHandler.handle_request`)也是**每個 HTTP 請求都新開一個**、`finally` 裡才 `close()`,跟 `InboxStore` 是同一種每請求新建模式,並非長活物件。這句話本身不影響這次修法的正確性(close-on-fail 三支模組都套了,不看呼叫端是否長活),但作為「為什麼要修」的理由記錄,把 CampaignStore 的暴露面(外部可觸發的每請求連線洩漏)描述得比實際更小,可能誤導之後的維護者低估這條路徑被反覆觸發(例如攻擊者對 DSP 端點灌流量、在補欄位視窗期間製造鎖競爭)時的資源耗盡風險評估。

---

查了什麼(其餘均 clean):

- **鎖檔名只留裝置+inode**(`src/rtb/executor/runner.py`):驗證了拿掉檔名分量不會降低唯一性——同一裝置上任兩個「同時存在」的檔案 inode 必不相同,拿掉檔名只是拿掉一個對唯一性沒有貢獻的裝飾欄位;同目錄符號連結、硬連結、相對路徑三種別名都會落在同一把鎖(新測試 `os.link` 驗證),不同目錄硬連結仍算不到同一把——這是威脅模型明講的「防忘記不防繞過」範圍外的已知限制,不是這次改法帶進的新洞。沒有發現鎖檔可預測性被這次改動放大(舊寫法本來就含 dev+inode,同樣可被同帳號攻擊者 `stat` 猜到)。
- **DSP 回應整數上限**(`_SQLITE_INTEGER_MAX = 2**63 - 1`,`src/rtb/executor/dsp_client.py`):確認 `read_campaign`、`write`、`operation_version` 三個對外呼叫點都經過同一個 `_positive_int` 把關,惡意或故障的 DSP 回傳超大整數(例如 `2**63`)會被吃掉、走「讀不懂當結果不明」的既有安全路徑,不會讓 `sqlite3` 對整數欄位寫入時丟 `OverflowError` 而讓整個 runner 行程未捕捉例外崩潰(這正是 s2-1 要擋的 DoS 面)。邊界值 `2**63 - 1` 本身可接受、`2**63` 起拒收,測試 `test_an_oversized_version_from_the_dsp_is_treated_as_unreadable` 覆蓋了寫入與兩個讀取端點。也確認 `domain/proposal.py` 既有的 `MAX_INT = 2**63 - 1` 已經在收件時對 `campaign_version_observed` 做同等上限,兩處口徑一致,沒有留下另一個未加蓋的整數輸入面。
- **讀 DSP 期間過期的第二次判斷**(`src/rtb/executor/execution.py` `process_one`):確認插入點正確——緊接在 `dsp.read_campaign` 成功回傳之後、簽發與寫入之前,關掉了「送出逾時偵測期間曾被攻擊者刻意拖長回應以搶到一個已過期決策仍被執行」的窗口(x1-1)。簽發(`_sign`,純 HMAC 運算)與取件(`_take`,純本地交易)之間沒有可被外部拖長的 I/O,所以這一次的修法已經把唯一有意義的外部可控延遲窗口(DSP 讀取)堵上;`dsp.write()` 本身之後沒有再重判到期,但那是既有設計就沒有承諾的第三個檢查點,不是這次修法本身的錯誤,也不是新開的洞。
- **三支資料庫模組統一「補欄位失敗就關連線」**(`task_store.py`、`dsp/store.py`):確認 `except BaseException: close(); raise` 正確涵蓋所有例外型別並保留原始例外往外傳,新增的 `tests/test_migration_failure_closes_the_connection.py` 用真連線+monkeypatch 驗證「開過的連線在失敗後確實被關掉」,不是假綠;這條修法本身補的是「未受控的連線洩漏」這個可被外部反覆觸發的資源耗盡面(DSP 伺服器與收件口伺服器都是每請求新建這兩種店面之一),修法是對的、範圍正確套到三支模組。唯一沒被這次修法一併處理、但也不是這次修法帶進去的既有落差是:`task_store.py`/`dsp/store.py` 的補欄位若因等鎖逾時丟出 `DatabaseBusy`,不像 `InboxStore` 那樣轉譯成各自的 `TaskStoreBusy`/`StoreBusy`,而是原樣往外丟——但這個行為在這次修法之前就是如此(原本連 try/except 都沒有),不屬於「改法本身改錯」,故不計入本輪發現。
- **能力憑證標頭加入共用用戶端封閉列舉**(`X-Capability`,`src/rtb/httpclient.py`,不在 delta 內但掃過確認無新洞):字串未經匯入 `capabilitykit` 取得,避免分析行程間接載入憑證模組;新增測試比對兩邊字串一致,並確認共用用戶端仍然拒絕夾帶故障注入標頭(`X-Fault`),與威脅模型「分析端正常路徑上沒有簽發/寫入相關金鑰」「故障注入靠旗標加標頭隔離」兩條合約一致,round 1 判定的邊界沒有被 round 2 破壞。
