severity: major
# 審查報告

severity: major

### 1. 稽核表守衛引入第二種比對邏輯,舊死信守衛未同步升級,九張表防護力不對等
severity: major
blocking: 是
引句:「SQLite 的衝突時更新把 UPDATE 跟表名隔開,UPDATE OR REPLACE 也不是動詞緊接表名(第 1 輪兩席)。」

觸發情境:有人在既有兩張死信稽核表(`dead_letters`、`dead_letter_ops`)上寫一句 `UPDATE OR REPLACE dead_letters SET status = 1` 或 `INSERT INTO dead_letters (...) ON CONFLICT (...) DO UPDATE SET ...`。

會出的錯:`tests/executor/test_dead_letter.py:447` 的 `TABLE_WRITE` 仍是本增量之前「動詞緊接表名」的比法(`\b(UPDATE|DELETE\s+FROM|...)\s+(dead_letters|dead_letter_ops)\b`)。已用 patch 裡逐字的正則實測驗證,這兩句都回傳 `False`——舊守衛完全偵測不到。而同一份提交在 `tests/executor/test_audit_tables.py:28` 為另外七張表引入了不同的比法:`REWRITES` 只要求改寫語句與表名「同一段拼回來的文字裡共現」,不必緊鄰,設計筆記自己講明這正是為了抓「UPDATE OR REPLACE」「ON CONFLICT … DO UPDATE」這類舊比法抓不到的寫法。等於同一次提交裡,概念完全相同的「稽核表只增不改」不變量被拆成兩套偵測邏輯共存於同一個測試套件,而較舊、仍在服役、保護真正正式環境資料表(死信)的那一套,反而是防護力較弱的那一套——這正是引入第二種守衛寫法,而不是把既有守衛跟著補強。

建議修法:把 `test_dead_letter.py` 的 `TABLE_WRITE` 比法統一換成 `test_audit_tables.py` 的「表名與改寫語句同段共現」邏輯(或乾脆把 `dead_letters`、`dead_letter_ops` 併入 `GUARDED` 常數、共用同一個 `audit_violations`/`REWRITES`),讓九張稽核表用同一套判定與同一套殺傷力配方驗證,不要留一套明知較弱的舊邏輯繼續守正式資料。

### 2. 調查實演自建 VirtualClock,跟同目錄既有共用虛擬時鐘語意不同
severity: major
blocking: 是
引句:「每讀一次前進 1 毫秒,讓同一輪裡依序發生的事時間都不同」

觸發情境:同一個 `tests/ops/` 目錄下,`tests/ops/conftest.py`(`World.__init__`)一直是直接 import 並重用 `tests.executor.conftest.Clock`——固定常數 `NOW` 起跳、只有顯式呼叫 `.advance(**kwargs)` 才前進,`tests/executor/test_f7_end_to_end.py`、`test_f4_end_to_end.py`(用 `_now()` 固定函式)也都是同一套「不呼叫就不動」的慣例。`tests/ops/test_investigation_drill.py:43` 卻另外定義了一個 `VirtualClock`:起點是真實系統時間 `datetime.now(UTC)`(不是專案慣用的固定常數)、每次 `__call__()` 自動前進 1 毫秒(不必顯式呼叫)、還帶 `threading.Lock`(既有 `Clock` 從未需要鎖)。

會出的錯:既有 `Clock` 本身已支援 `advance(milliseconds=1)`,要讓同一輪內依序發生的事件時間互不相同,在既有 `Clock` 上包一層「呼叫時自動 advance」就能達到同樣效果,不必另開一個語意完全不同的平行類別。現在的寫法讓同一測試目錄同時存在「手動推進」與「自動 tick」兩種時鐘語意;之後有人比照目錄裡的舊慣例去讀或沿用這支新時鐘,以為它跟 `Clock` 一樣「不呼叫就不動」,會被自動前進的副作用誤導(例如斷言前後分別呼叫一次 `clock()`,中間已經不知不覺跳了 1 毫秒),而且用真實系統時間起跳也跟專案其他地方一律釘死常數時間(`NOW`)以求可重現的做法不一致。

建議修法:讓 `VirtualClock` 繼承或包裝 `tests/executor/conftest.py` 的 `Clock`,只加一個「呼叫時自動 advance(milliseconds=1)」的開關(或在既有 `Clock` 上直接加這個可選參數),而不是另開一個獨立、語意不同的時鐘類別;起點也改用專案慣用的固定常數而非系統時間。
