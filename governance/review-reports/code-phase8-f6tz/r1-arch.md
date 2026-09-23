severity: major

## F1 死信結構檢查測試改用了跟既有慣例不同的兩種掃描手法(全庫字串比對 + 純 regex),不是既有的單模組 import-AST 白名單掃描
severity: major
blocking: 是 — 引入第二種結構檢查做法,跟本檔要求對照的既有慣例(`tests/executor/test_inbox_server.py` 的 ast 掃收件口模組匯入)不一致

既有慣例(`tests/executor/test_inbox_server.py:367-377`)是:對「單一模組自己的原始碼」`ast.parse(inspect.getsource(module))`,只挑 `ast.ImportFrom`/`ast.Import` 節點,拿匯入名單去比對白名單,判準是「這個模組匯入了誰」。

這批新增的兩支結構檢查測試做的是完全不同的兩件事:

引句:「for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))」

`test_only_the_replay_method_revives_a_dead_letter`(`tests/executor/test_dead_letter.py:460-475`)不是掃「某模組匯入了誰」,而是掃**全部** `src/rtb/**/*.py`,對每個檔案的 AST 找出「函式體裡含有某段字串常數(`"dead_letter_reason = NULL"`)」的函式,拿函式清單去比對「只能是 `inbox_store.py::_replay`」。判準從「匯入白名單」換成「誰的原始碼裡藏著這句 SQL 片段」,是用 AST 找字串常數字面內容,不是查匯入節點或呼叫圖。

引句:「writes = re.compile(r"\b(UPDATE|DELETE\s+FROM|REPLACE\s+INTO|INSERT\s+OR\s+REPLACE\s+INTO|"」

`test_the_dead_letter_tables_are_only_ever_inserted_into`(`tests/executor/test_dead_letter.py:478-486`)更進一步,完全不用 AST,直接對原始碼文字跑 regex 掃 SQL 關鍵字(`UPDATE`/`DELETE FROM`/…)是否鄰接 `dead_letters`/`dead_letter_ops` 兩個表名。這是本檔比較清單裡明確要對照的「掃原始碼的結構檢查測試」項目,但手法從 AST 換成了裸字串 regex——比對到多行 SQL、字串接續、或表名出現在註解/字串常量裡都可能誤判或漏判,跟既有 AST 掃描(對語法樹节點型別做結構化判斷,不受文字排版影響)的可靠性不是同一等級。

這兩支測試都不是風格偏好上的差異,而是在同一個「掃原始碼防回歸」的功能類別裡,新增了兩種跟現有慣例不同的實作策略(全庫 AST 字串常數搜尋、純文字 regex),既有慣例(單模組 import 節點白名單)沒有被沿用也沒有被取代,形成三種並存的結構檢查手法。

## 收件口時間轉換改呼叫嘗試紀錄模組,方向與既有依賴一致,不算新增依賴方向

`src/rtb/executor/inbox_store.py` 早就大量匯入並呼叫 `attempt_store`(如 `attempt_store.iso(since)`、`attempt_store.transaction()` 等,`inbox_store.py:41` 匯入、多達數十處呼叫),讀取路徑本來就已經呼叫 `attempt_store.iso` 做時區檢查(如 `inbox_store.py:1161,1202-1203,1225-1226`)。這次改動只是把 `_iso()` 內部實作也改成呼叫同一支 `attempt_store.iso`:

引句:「return attempt_store.iso(moment)」

這是既有依賴方向(收件口模組依賴嘗試紀錄模組,不是反過來)的延伸,沒有引入新方向,也沒有跨層直呼,不構成 major。

## 事故 F6 合約行的措辭與欄位寫法跟 F7 合約一致

比對 `docs/rtb-production-agent-demo-knowledge/Systems/執行迴圈.md` 的事故 F7 合約行:單行 `KEY:★INVARIANT★ 事故 F7:...` 後接 `[test:...] [kill:recipes] [audit:sonnet/2026-09-24]`,並在後續 `WHY:` 行記錄轉正經過、審計輪數與卷證路徑;`kill_recipes` 欄位是 JSON 陣列,每筆物件含 `invariant`/`test`/`file`/`old`/`new`/`note` 六個鍵。

F6 合約行(`docs/rtb-production-agent-demo-knowledge/Systems/提案收件口.md`)用同一種結構:`KEY:★INVARIANT★ 事故 F6:...` 後接同樣的 `[test:...] [kill:recipes] [audit:sonnet/2026-09-24]`;`WHY:[2026-09-24]事故 F6 轉正:...` 記錄改寫理由、審計次數與 `governance/review-reports/code-phase8/f6-audit-1.md`、`f6-audit-2.md` 卷證路徑;`kill_recipes` 新增的 25 筆項目同樣是 `invariant`/`test`/`file`/`old`/`new`/`note` 六鍵物件,直接併進既有陣列。措辭風格(先講宣稱、括號補條件、分號分句)也跟 F1–F4、F7 一致。這部分沒有偏離既有寫法。

## 未能核對之處

未逐字核對 `tests/executor/test_dead_letter.py` 除已引用行號外的其餘新增測試與 `tests/executor/test_inbox_timezone.py` 全文跟既有 fixture 命名慣例(`h`、`conftest.NOW` 等)是否完全一致;抽樣比對(`tests/executor/test_inbox_timezone.py:1-58`)看到的 fixture 與命名跟同目錄其他測試檔慣例相符,未見架構層級的落差。
