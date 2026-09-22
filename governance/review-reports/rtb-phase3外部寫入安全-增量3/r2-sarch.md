severity: major

### 1. 單一執行者鎖引入專案裡原本沒有的檔案鎖並行控制,未交代與既有 SQLite 交易做法的取捨

severity: major
blocking: 是 這是「引入原本沒有的第二種並行控制做法」的典型情況,而且待審材料完全沒有交代為什麼既有機制不夠用,屬於本席職權內該擋的架構對齊落差。

引句:「啟動時對資料庫旁的鎖檔取得不等待的獨佔鎖,拿不到(已有另一個執行迴圈在跑)就以非零代碼結束」

說明:這句話要的語意是「不等待、拿不到就立刻失敗」的獨佔鎖,用來擋住第二個執行迴圈同時跑重啟恢復。但專案裡處理「並行寫入互斥」向來只有一套做法——`sqlitekit.begin_immediate`/`immediate_transaction`(`BEGIN IMMEDIATE`,`busy_timeout_seconds` 可設,含 0 即「不等待」),DSP、收件口、嘗試表、增量 1/2 的補欄位遷移全部沿用同一套(見下方 file 引用);全專案（含測試）目前完全沒有任何檔案鎖(`flock`/`fcntl`/lockfile)的先例。要達成設計要的「不等待、拿不到就失敗、程序活著就一直鎖著、程序死了自動釋放」這組語意,用一條專用連線對執行行程資料庫開一個不提交的 `BEGIN IMMEDIATE`(busy_timeout 設 0)就做得到,而且會自動繼承既有機制「連線關掉/行程當機就釋放鎖」的好處,不必自己處理鎖檔的殘留清理。快照裡對這個選擇沒有寫 PRIOR-ART/RETIRE-IF,也沒有像其他新機制(例如增量 2 的憑證共用格式模組)那樣講清楚「為什麼不能複用既有的忙碌逾時/BEGIN IMMEDIATE,兩者怎麼並存」——目前寫法是憑空多出一套獨立的、專案沒驗證過的並行語意,且負責收斂這類「怎麼做」共同做法的共用模組完全沒提到它。

file: `/Users/enzo/rtb-production-agent-demo/src/rtb/sqlitekit.py:43-50`(既有的 `begin_immediate`,已提供「鎖不到就丟例外、不隱性等待」的建構元件)
file: `/Users/enzo/rtb-production-agent-demo/src/rtb/dsp/store.py:192-214`(既有並行寫入互斥/補欄位一律走 `begin_immediate`+`BEGIN IMMEDIATE`,無例外)
file: `/Users/enzo/rtb-production-agent-demo/docs/rtb-production-agent-demo-knowledge/Systems/共用行程基礎.md:6`(此節點宣告的職責是收斂 HTTP 與 SQLite 的共同做法「只綁回送位址、Host 檢查…WAL、忙碌逾時、BEGIN IMMEDIATE」,不含檔案鎖這類新並行原語)

---

其餘已核對、判斷對齊、不列為發現的部分:時間單一來源與協定注入(比照 `src/rtb/analyzer/flow.py` 的 `now` 參數與 `Protocol` 注入,寫法一致)、DSP 讀寫用戶端邊界(比照 `src/rtb/analyzer/dsp_client.py` 只做「打 HTTP、轉結果」、經共用 HTTP 用戶端、不碰儲存)、收件表補欄位遷移(比照 `src/rtb/analyzer/task_store.py`、`src/rtb/dsp/store.py` 的「每次連線檢查、缺才在交易內補、拿到鎖後再查一次」)、取件與開始一筆同一交易(比照 `src/rtb/executor/inbox_store.py` 已有的交易入口與 `src/rtb/executor/attempt_store.py` 的交易物件邊界)、就緒訊號(比照 `src/rtb/executor/inbox_server.py:150` 的「印一行、測試等這行而非固定等待」慣例,執行迴圈本身無埠可印 PORT= 屬合理調整,非另立一套)——這些第 2 版的寫法都與既有做法一致,沒有帶進新的不一致。
