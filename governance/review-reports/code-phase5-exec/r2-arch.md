severity: clean

### 回應合併邏輯放在收件口伺服器模組,與既有分層一致

`inbox_server.py` 本身的文件字串已明講這支檔只做「安全地收、去重、記帳」「不做政策與租戶檢查」,但這次新增的 `_answered_block_code` 只是**回應本文的字串轉換**(細分代碼 → `not_permitted`),不是政策判斷本身——政策判斷(`over_budget_cap`/`campaign_not_allowed` 怎麼判定)仍留在 `execution.py`/`inbox_store.py`,收件口只是把「已經算好的字串」在組裝回應時再映射一次。這跟同檔既有的 `_accepted_body`(把 `Accepted` 轉成回應 dict)、`_log_invalid_proposal`(把錯誤列表轉成固定格式字串再輸出)是同一種「模組層小函式做資料轉換/格式化,不碰資料庫、不做業務判斷」的既有寫法,沒有引入第二種做法或跨層直呼。

引句:「只做「安全地收、去重、記帳」:不執行提案、不呼叫 DSP、不做政策與租戶檢查。」

file: `src/rtb/executor/inbox_server.py:3`

另外確認 `BlockCode` 是單一定義(`inbox_store.py:73` 的 `class BlockCode(StrEnum)`),`execution.py` 與 `inbox_server.py` 都是從同一處 import,沒有各自重複定義出第二份列舉造成分岔。`Accepted.block_code` 型別是 `str | None`(DB 讀出來的字串),`_PERMISSION_BLOCKS` 用 `.value` 建 frozenset 去比對字串,跟既有的型別流(字串進、字串出)一致,不是把列舉物件跨層滲進伺服器層。

引句:「block_code: str | None = None  # 只有處置是已擋下時有值:分析端據此決定要不要重新規劃」

file: `src/rtb/executor/inbox_store.py:229`

### 執行迴圈把擋下原因用可選參數往下傳,跟既有關鍵字慣例吻合

`_ack_terminal` 與 `_void_then_fail` 各新增一個尾端可選參數 `block_code: BlockCode | None = None`,寫成一般帶預設值的位置參數,沒有用 `*,` 逼成關鍵字專屬。乍看跟 `_write` 用 `*,` 把多個可選參數都逼成關鍵字專屬的寫法不同,但同檔本來就有「只加一個尾端可選參數時用一般預設值」的先例——`_sign(self, proposal, key: str | None = None)`(改動前就存在,execution.py:371)同樣是單一可選參數、一般預設值、沒有 `*`。`_write` 用 `*,` 是因為它原本就有三個以上可選參數要互相區分;這次 `_ack_terminal`/`_void_then_fail` 各只加一個,跟到的是「單一可選參數」那條既有先例,不是新開一種寫法。

引句:「def _sign(self, proposal: Proposal, key: str | None = None) -> _Signed | BlockCode:」

file: `src/rtb/executor/execution.py:371`

引句:「now: datetime, block_code: BlockCode | None = None,」

file: `src/rtb/executor/execution.py:435`

`_write` 這次多塞的第 4 個可選參數同樣維持在 `*,` 之後、跟既有三個放一起,並沿用同一支檔案裡既有的 `# noqa: PLR0913` 加中文理由的慣例(`InboxServer.__init__` 已有先例:「啟動參數,全部有預設值」),這次寫成「關鍵字參數都是這次寫入要記的欄位,各有預設值」,是同一套慣例的延續,不是新開的例外處理方式。

引句:「啟動參數,全部有預設值,測試與命令列各用一部分」

file: `src/rtb/executor/inbox_server.py:135`

### 新增的小函式放在模組層,符合同檔既有慣例

`_version_changed_or_none`(execution.py)與 `_answered_block_code`(inbox_server.py)都是模組層的自由函式,不是類別方法,這跟兩支檔案裡既有的模組層小函式(`precheck`、`intent_holds`、`record_matches`、`_log_invalid_proposal`)放在同一層次、簽名單純(輸入算好的值、回傳算好的值、不碰 self/資料庫)一致。沒有出現「同一種轉換邏輯有的寫成方法、有的寫成模組函式」的分岔。

引句:「passed = proposal.decision_expires_at > self.clock() and checked is None」

file: `src/rtb/executor/execution.py:492`

## 結論

本輪三處改動(收件口回應合併、執行迴圈可選參數傳遞、新增模組層小函式)都沿用同層既有寫法,沒有發現引入第二種做法或跨層直呼,架構對齊角度無 major 發現。
