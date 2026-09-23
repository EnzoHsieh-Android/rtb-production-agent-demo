severity: major

## F1 DSP 新端點跟既有路由分派機制的形狀不合(單一路徑參數假設被打破)

severity: major
blocking: 是 — 照字面規格接進 `src/rtb/dsp/server.py` 現有的路由分派會直接壞掉,不是風格問題,是另一套分派邏輯

引句:「DSP 加一個唯讀端點:依提交時間的窗列出操作(冪等鍵、廣告、動作、新預算、提交時間、政策版本),窗長不得超過 24 小時,單次最多回 5000 筆、多的用「上一頁最後一筆的操作編號」往下翻」

`src/rtb/dsp/server.py` 現有的路由表與分派函式是這個形狀(`_route`)：

```python
ROUTES = [
    ("GET", re.compile(r"^/campaigns/([^/]+)$"), "get_campaign"),
    ("GET", re.compile(r"^/campaigns/([^/]+)/history$"), "get_history"),
    ("GET", re.compile(r"^/campaigns/([^/]+)/metrics$"), "get_metrics"),
    ("GET", re.compile(r"^/operations/([^/]+)$"), "get_operation"),
    ...
]

def _route(self, method: str) -> tuple[str, str]:
    for route_method, pattern, name in ROUTES:
        match = pattern.match(urlsplit(self.path).path)
        if route_method == method and match:
            return name, match.group(1)
    raise RequestRejected(404, "not_found")
```

每一條既有路由的正規表達式**恰好一個捕捉群組**(廣告編號或冪等鍵),`_route` 無條件呼叫 `match.group(1)`,`handle_request` 再把這個值當唯一的業務參數傳給對應的 `_xxx(store, campaign_or_key, fault)` 方法。設計要加的是「依提交時間的窗列出」,天然沒有一個像廣告編號或冪等鍵那樣的單一路徑參數——起、迄時間與往下翻的操作編號是查詢字串(既有的 `_get_metrics` 就是這樣讀 `window` 查詢參數的),不是路徑的一段。

照字面把這個端點接進現有 `ROUTES`/`_route`/`handle_request` 三件會出兩種錯:
- 若给它一個沒有捕捉群組的正規表達式(例如 `^/operations$`),`match.group(1)` 會丟 `IndexError`,不是業務例外,會被當成未預期例外回 500,而不是回時間窗訊息。
- 若硬塞一個假的捕捉群組來湊形狀,傳進 handler 的「業務參數」變成一段沒有意義的路徑片段,跟現有「這個位置永遠是廣告編號或冪等鍵」的隱含假設衝突,之後任何人讀 `_route` 都會被這一條誤導。

這代表要嘛在 `_route`/`handle_request` 旁邊另開一條「零或多參數、查詢字串驅動」的分派路徑(等於引入第二種路由機制跟原有的單參數路徑機制並存),要嘛把查詢參數硬塞進現有的單參數形狀。兩條都是設計沒交代、而且都跟現有寫法不同的做法。設計裡完全沒有提到這個端點怎麼跟既有的 `ROUTES`/`_route` 共存,只寫了行為(窗長、上限、分頁),沒寫接口形狀。

例:實作者照現有 `_get_operation` 的樣子抄一條 `("GET", re.compile(r"^/operations$"), "list_operations")` 進 `ROUTES`,`_route` 執行到 `match.group(1)` 時因為這個 pattern 沒有群組直接炸例外,這個唯讀端點連基本回應都出不來,S665 的窗長/分頁/索引合約都測不到。

## F2 執行端「批量查第一列嘗試、核可使用紀錄」沒有可批量的既有介面,字面實作會變成逐筆查或跨層直開資料庫

severity: major
blocking: 是 — 跟「不逐筆查」的字面規格互相矛盾,而且唯一能批量的路徑是繞過既有模組介面直接拼 SQL(跨層直呼)

引句:「執行端那一邊:把這一頁的冪等鍵一次批量查第一列嘗試、核可使用紀錄(每批不超過 SQLite 參數上限),不逐筆查」

讀 `src/rtb/executor/attempt_store.py` 與 `src/rtb/executor/inbox_store.py` 目前的讀取介面：

- 嘗試紀錄第一列現在只有「單把鍵查一列」的介面(`WHERE key = ?`,`file: src/rtb/executor/attempt_store.py:241`),批量統計函式如 `aggregate_holdings`(`file: src/rtb/executor/attempt_store.py:386-415`)刻意避開逐鍵 IN-list,靠的是「整批依租戶篩選 + `NOT EXISTS` 子查詢」這種跟這裡完全不同的查法(它的註解直接寫「鍵的數量沒有上限,逐一當查詢參數會超過 SQLite 的參數上限」,`file: src/rtb/executor/attempt_store.py:413-414`)。給定一頁「任意冪等鍵清單」(不是「這個租戶全部」)要批量取第一列,現有模組沒有這種介面。
- 核可使用紀錄的唯一介面是 `used_approvals(tx, proposal)`(`file: src/rtb/executor/inbox_store.py:966-976`),鍵是「任務、修訂、內容雜湊」三元組,一次只吃一份提案物件,內部 `WHERE u.task_id = ? AND u.revision = ? AND u.content_hash = ?`。冪等鍵不在這張表的查詢鍵裡;要從冪等鍵批量查核可使用紀錄,得先批量查出每把鍵對應的(任務、修訂、內容雜湊),再拿這些三元組去比對 `approval_uses`——這是「多欄位組合批量比對」,現有程式庫裡沒有這種寫法的先例(`grep` 全庫找不到第二個用 IN-list 或多欄位批量比對這兩張表的地方)。

設計沒交代這支批量查詢住在哪個模組、走哪支既有函式、用哪個交易類型(增量 1 立的規矩是維運套件只准呼叫「機械算出的非寫入函式白名單」,而且必須是唯讀交易,見增量 1 設計裡「維運套件只准用唯讀開法、只准呼叫一份明列的讀取函式白名單」那一段,`Systems/提案收件口` 與 `Systems/外部寫入嘗試紀錄` 的家目前也還沒有這種批量介面)。照字面實作,只有兩條路:

1. 補一支新的批量函式,但因為 `used_approvals` 天生是三元組鍵、不是冪等鍵鍵,這支新函式勢必要先在同一批裡對每個冪等鍵各查一次(或至少對「核可使用」那一段各查一次)才能拼出三元組去比對——變成事實上的逐筆查,跟「不逐筆查」的字面規格矛盾。
2. 為了真的做到一次批量,直接在副作用核對函式裡對 `attempts`、`approval_uses` 兩張表手拼跨模組的組合查詢,不透過 `attempt_store`/`inbox_store` 各自的介面——這正是「跨層直呼」,而且維運套件現在的邊界規則(增量 1 的匯入禁令加白名單測試)理論上會擋下這種直接開表的寫法。

兩條路都不是設計文字表面看起來的「一次批量查兩張表,不逐筆」,而是新增一種現有架構沒有的存取形狀,或違反維運套件既有的邊界合約。

例:實作者把「批量查核可使用紀錄」直接寫成「對這一頁 500 把冪等鍵,逐把鍵呼叫一次 `used_approvals`」,S665 要求的「執行端的查詢應批量不逐筆」在核可使用紀錄這一段其實沒有做到,測試若只斷言「呼叫次數少於冪等鍵數的某個比例」可能矇混過去,但實際上是 N 次查詢對 1 次查詢的差別,事故量大時(如燒損評估器每次呼叫要看 5000 筆操作)會在讀取交易裡待更久,跟增量 3 自己在實務隱患欄承認「在唯讀交易下跑,不取寫入鎖」但沒有把這個 N+1 成本算進去。

以下項目讀碼比對後沒有發現架構脈絡衝突,列在這裡以免漏審：

- 維運套件用共用 HTTP 用戶端打 DSP 新端點:跟增量 1 已經定的規則一致(「維運套件照增量 1 的規則用共用 HTTP 用戶端打這個唯讀端點,不直開 DSP 資料庫」),沒有引入第二種對外呼叫方式;DSP 既有的四支 GET 端點也全部不驗憑證,新端點同樣不驗憑證,跟既有讀取端點的授權慣例一致。
- DSP 操作表時間索引、24 小時窗長上限、單次最多 5000 筆:跟增量 2 已定的「窗口讀取函式配時間索引、窗長不超過 24 小時」與收件表「總列數上限 5000」的既有節流做法是同一套形狀,不是另立門戶。
- 嘗試紀錄第一列多四欄「開始時的核對材料」,舊資料庫開啟時補欄位、舊列為空:跟 `src/rtb/executor/inbox_store.py` 現有的補欄位機制(`_migrate_columns`,「每次連線檢查,缺才在交易內補」)是同一套做法,`src/rtb/dsp/store.py` 的 `_migrate_columns` 也是同一個模式,三處一致。
- 讀取順序「先讀 DSP 的窗,再開執行端的唯讀快照」:這是就時序推出的正確性論證(執行端在呼叫 DSP 之前就已提交第一列),不是新架構層,審過的推論本身站得住(執行端先寫、DSP 才收到請求,所以反過來讀一定看得到)。
