severity: major

### 1. 「處理一筆」沒有交代時間單一來源與可替換協作者,跟分析行程 advance() 的既有做法不符
severity: major
blocking: 是 分析行程曾經真的因為「協作者自己讀系統時鐘」撞出過坑(見下引 PITFALL),同一份設計裡「處理一筆」至少有五個地方要用到時間(提案過期判斷、DSP 重讀時間戳、開始一筆的 now、簽發憑證的 now、寫結果/記查證逾時的 now),卻完全沒交代這個 now 從哪來、要不要單一來源往下傳;實作前應在設計裡定案,不然實作時很容易各自讀系統時鐘,重演同一種偏差。
引句:「一次處理一筆(執行行程的一個同步函式)」
說明:分析行程的 `advance()` 把呼叫端拿到的 `now` 當唯一時間來源,往下傳給 `EvidenceSource`、`Decide` 兩個用 `typing.Protocol` 定義、可替換的協作介面,見 `src/rtb/analyzer/flow.py:114-139`(尤其 49-61 行的 Protocol 定義);知識圖譜把這條訂成專案級規則,不是 flow.py 自己的偏好:
file: `docs/rtb-production-agent-demo-knowledge/Systems/分析行程流程與檢查點.md:44`(RULE:時間只有一個來源……比照專案「由呼叫端把時間往下傳」的既有做法)
執行行程自己既有的下游函式也都照做——拿到寫入鎖才讀 clock 的 `InboxStore.accept`:
file: `src/rtb/executor/inbox_store.py:172-189`
一律要求呼叫端傳 `now` 的 `attempt_store.begin/transition/record_verification_timeout`:
file: `src/rtb/executor/attempt_store.py:229,288-309,312-324`
連簽章都要 `now: int` 參數的 `capability_signer.sign`:
file: `src/rtb/executor/capability_signer.py:109`
但「處理一筆」這個同步函式本身,整段設計沒有一句提到 now 從哪來,也沒提介面替換(協定注入)這件事,跟分析行程用 Protocol 注入協作者、時間唯一來源的既有做法不同。這正是知識圖譜記下的舊坑:
file: `docs/rtb-production-agent-demo-knowledge/Systems/分析行程流程與檢查點.md:44`(PITFALL:推進者整批共用一個時間時,證據來源若自己讀時鐘,年齡會變負而被誤判過期)

### 2. 收件表遷移改用整表重建,跟既有「補欄位」遷移做法不同,卻寫成「沿用既有做法」
severity: major
blocking: 是 遷移機制是「安全地收、去重」這整個系統的地基;整表重建(建新表、複製、刪舊表、改名)比既有的 `ALTER TABLE ADD COLUMN` 多好幾個中途失敗的風險點,而且專案自己在同一批增量(增量 1)剛示範過不需要重建表的替代做法,設計應該先比較兩者再定案,不是直接把重建寫成「沿用既有做法」帶過。
引句:「在一個交易裡重建收件表」
說明:專案目前僅有的兩個「補欄位」遷移都只用 `PRAGMA table_info` 檢查 + `ALTER TABLE ... ADD COLUMN`,從未整表重建:
file: `src/rtb/analyzer/task_store.py:141-150`(`_migrate_evidence_payload_column`)
file: `src/rtb/dsp/store.py:192-213`(`_migrate_columns`)
收件表的 `state` 欄位帶 `CHECK (state IN ('pending','superseded','expired'))`:
file: `src/rtb/executor/inbox_store.py:40`
SQLite 無法直接 ALTER 這個 CHECK,設計因此選擇整表重建。但同一批增量剛建立的 `attempts` 表反而完全不對 `state` 欄位加 CHECK 限制,只用 `TEXT NOT NULL`,把封閉列舉的驗證留給應用層(`domain/attempt.py` 的 `can_transition`/`code_fits`):
file: `src/rtb/executor/attempt_store.py:44-56`
也就是專案自己已經在這個增量裡示範過「用應用層驗證取代 DB CHECK,遷移時就不用重建表」這條路,而增量 3 設計沒有走這條路、也沒交代為什麼不走,直接把整表重建包裝成「沿用既有『每次連線檢查、缺才補』的遷移做法」——機制上(整表重建 vs 補欄位)是第二種做法。

### 3. 執行行程的 DSP 讀寫用戶端沒交代放在哪、要不要照分析行程 dsp_client.py 的職責邊界
severity: minor
blocking: 否 S55 已經用測試擋住「匯入 DSP 內部模組」這條紅線,核心正確性不受影響;但實作時容易把 HTTP 呼叫散寫進「處理一筆」裡,建議設計補一句放置位置與邊界再進實作,不必因此卡住整份設計。
引句:「用執行行程自己的 DSP 讀取用戶端(沿用共用 HTTP 用戶端)」
說明:分析行程的 dsp_client.py 明確定位「只做『打 HTTP、轉成 Evidence』,不碰 TaskStore、不做任何持久化」,獨立成一支檔案、掛在 analyzer 行程自己的目錄下:
file: `src/rtb/analyzer/dsp_client.py:1-9`
增量 3 設計提到「執行行程自己的 DSP 讀取用戶端」與後面步驟 5 的「呼叫 DSP」(等於一支讀+寫都做的用戶端),但全篇沒交代要放在 `src/rtb/executor/` 底下哪一支檔、要不要跟分析行程一樣維持「純轉譯、不碰儲存層」的邊界;目前 `src/rtb/executor/` 底下也還沒有任何 DSP 用戶端檔案可以類比(只有 `inbox_server.py`、`inbox_store.py`、`attempt_store.py`、`capability_signer.py`)。

---
另外也具體核對了以下三項,沒有發現問題:啟動程式的命令列參數風格、失敗時非零結束、先跑重啟恢復再進迴圈,跟 `src/rtb/executor/inbox_server.py:135-155` 的 `main()` 做法(argparse、`SystemExit(2)`、建物件失敗即退出)一致;「取件並開始一筆」用同一個交易涵蓋 `inbox_store` 與 `attempt_store` 兩張表,正好對應 `InboxStore.transaction()` 已經建好的交易入口(`src/rtb/executor/inbox_store.py:156-170`),沒有跨層直呼;S55 已把「不匯入 DSP 內部模組」寫成合約,擋住了最明顯的跨層呼叫風險。
