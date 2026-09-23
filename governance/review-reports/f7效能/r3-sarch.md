severity: major

### F1 溢位偵測要處理的「分辨 SQLite 錯誤」是全案唯一由 `sqlitekit.py` 統一做的事,這份設計把它另外散在 `attempt_store.py` 用訊息文字判斷

severity: major
blocking: 是
引句:「其他資料庫錯誤一律照舊往外丟,不退回——不然查詢寫錯、欄位打錯這類錯誤會被當成溢位悄悄吞掉」

說明:

專案裡「分辨 SQLite 例外是哪一種」只有一個家:`src/rtb/sqlitekit.py`。它開宗明義寫「DSP 與提案收件口都用它,不各寫一套」(`file: `src/rtb/sqlitekit.py:3``),而且已經示範了唯一的分辨手法——`_is_lock_contention` 只認 `exc.sqlite_errorcode`(擴充碼的低 8 位)是不是 `SQLITE_BUSY`/`SQLITE_LOCKED`(`file: `src/rtb/sqlitekit.py:27-28``),`begin_immediate`/`connect` 都是抓到 `sqlite3.OperationalError` 之後轉呼叫這支函式,符不符合才決定要不要往外丟(`file: `src/rtb/sqlitekit.py:41-45,56-61``)。

這份設計要新增的「只認資料庫回報的整數溢位,其他一律往外丟」是同一種工作(分辨 `OperationalError` 是不是某個特定原因),但:

1. 落點不同:新的判斷邏輯要寫進 `attempt_store.py`(逐列計入函式旁的已用額度快路徑),但這支模組現在完全不 import `sqlitekit`、也沒有任何一處 `except sqlite3.OperationalError`(`file: `src/rtb/executor/attempt_store.py:26-35``——只 import 了裸的 `sqlite3` 型別)。等於是繞過專案唯一的「SQLite 錯誤分辨」中心,自己在別的模組另開一份,正好違反 `sqlitekit.py` 自己講的「不各寫一套」。
2. 判法不同:`_is_lock_contention` 用的是 SQLite 官方的擴充錯誤碼(穩定、跨版本語意固定);設計裡的溢位判斷因為 SQLite 對整數溢位只回泛用的 `SQLITE_ERROR`(我在本機用 sqlite 3.53.3 實測過:`sum()` 溢位時 `sqlite_errorcode` 是 1、`sqlite_errorname` 是 `SQLITE_ERROR`,跟語法寫錯、欄位打錯是同一個碼,분不開),所以只能退而求其次比對錯誤訊息文字。這是專案目前唯一一處要用「訊息文字」而不是「錯誤碼」來分辨 SQLite 例外種類的地方,跟既有唯一慣例的判法不同調,而且訊息文字比對比錯誤碼比對脆弱(不同 SQLite 版本或建置選項措辭可能不同,写法一改就悄悄從「溢位退回」變成「攔不到、直接把例外丟出去」或反過來「什麼錯誤都被吞成溢位」——這正是快照自己點名要防的那種「查詢寫錯被誤判」)。

字面實作下去,會出現兩個問題疊在一起的情況:程式裡多了一個 `sqlitekit.py` 管不到的 SQLite 例外分類點,而且這個分類點用的是全案獨一份、沒有測試證明過在目標 SQLite 版本上穩定的「文字比對」手法。

建議改法:把「這個 `OperationalError` 是不是整數溢位」封裝成 `sqlitekit.py` 裡新增的一支函式(比照 `_is_lock_contention` 的形式與所在位置),`attempt_store.py` 只呼叫它、不自己解析訊息字串;順便讓這支新函式的判法(碼或訊息)有一個地方統一測、以後 SQLite 版本變了只改一處。

共 1 條,blocking 1 條。
