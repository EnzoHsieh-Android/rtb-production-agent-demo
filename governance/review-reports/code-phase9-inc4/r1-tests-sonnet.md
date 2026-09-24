severity: major
# 外部審查報告

實驗環境:`/tmp/rtb-review`(從 `/Users/enzo/rtb-p9i4` 的 `src/`、`tests/` 複製,自建 git 倉庫供變異測試用回滾;全程未修改 `/Users/enzo/rtb-p9i4`)。測試指令依格式規則用 `PYTHONPATH=src /Users/enzo/rtb-p9i1/.venv/bin/python -m pytest`,在 `/tmp/rtb-review` 內執行變異版本、在 `/Users/enzo/rtb-p9i4` 內只讀重跑既有測試。

**先講兩塊查了沒事的地方:**
- 調查實演(`tests/ops/test_investigation_drill.py`):對三種探針分別測過——① 拿掉注入的 5xx 故障:在 `run_incident` 自己的送出次數斷言就先紅(`{('verified', 1)}` != `{('unknown', 1)}`),不會撐到後面步驟才發現;② 把 `A_BAD` 群組換成掛在 tenant-b(對調租戶):第 1 步「依租戶切片」的 `reconciliation_seconds.p95(tenant="tenant-a")` 斷言正確翻紅(`sum(...) == 0` 而非期望的 13);③ 把事故長度壓到對帳期限(10 分鐘)以下:被劇本自己的前提斷言 `assert sli.RECONCILE_DEADLINE < INCIDENT < timedelta(minutes=15)` 擋下,不會用不合理的參數硬跑;壓到期限邊界附近(10 分 5 秒、10 分 30 秒)仍正確反映真實量到的耗時,連跑三次不因虛擬時鐘飄動而不穩。三種探針都如預期翻紅或被前提擋下,沒有看到「恆綠」的跡象。此區判定:乾淨。
- 換名合併:`write_scan.py` 內既有的、`classify()`/`write_functions()`/`read_functions()`(供 [S621]、[S617]、[S600] 等既有邊界測試用)依賴的節點層級 `_strings()`(只取字串常數、排除文件字串)保留原名與原語意,新搬進來的整棵樹拼字串函式改名為 `reconstructed_strings()`,兩者不互撞、不覆蓋。只讀重跑 `test_dead_letter.py`、`test_lifecycle_events.py`、`test_read_only.py`、`test_ops_boundaries.py` 共 64 條全綠。此區判定:乾淨。

**稽核守衛(`tests/executor/test_audit_tables.py` + `tests/executor/write_scan.py`)變異測試發現兩個「忘記寫」就會踩過去、守衛完全看不見的洞**,兩者都不屬於守衛自己聲明的「刻意繞過」範圍(變數組表名、拆到兩個變數再串)。

### 1. `.format()` / `%` 組出來的改寫語句完全不進拼字串掃描,即使表名是死的字面值
severity: major
blocking: 是

引句:「相鄰字串、用 + 串起來的字串、f-string 的固定片段」

`reconstructed_strings()` 的 `text_of()` 只認得三種節點:`ast.Constant`、`ast.JoinedStr`(f-string)、`ast.BinOp` 的 `+`。守衛自己聲明的免責範圍是「防忘記,不防刻意繞過:用變數組表名、把關鍵字拆到兩個變數再串起來,都繞得過」——這指的是**表名本身是變數**的情況。但 `"UPDATE {} SET ...".format("operations")` 或 `"UPDATE %s SET ..." % "operations"` 裡表名是不折不扣的字面常數,只是組字串的手法用了 `.format()`/`%` 而不是 `+`/f-string,`text_of()` 遇到 `ast.Call`(`.format`)或 `ast.BinOp` 的 `Mod`(`%`)一律回傳 `None`,整段完全不會被納入掃描,連「拼不回來、判定跳過」的軌跡都沒有。

觸發情境:在 `/tmp/rtb-review/src/rtb/dsp/store.py` 加一個維運補丁函式:
```python
query = "UPDATE {} SET committed_at = ? WHERE operation_id = ?".format("operations")
conn.execute(query, (when, operation_id))
```
`test_audit_tables_are_only_ever_inserted_into` 照樣全綠(1 passed);換成 `%` 版本結果相同。

關鍵的是,這不是我編出來的偏門寫法——本專案自己就已經在用這個手法改寫受守護表:`src/rtb/executor/attempt_store.py:673-676` 的 `first_rows_started_query()` 就是拿一段含 `{}` 佔位的字面樣板,用 `part.format(...)` 組出對 `attempts` 表(七張表之一)的查詢。今天這條是 `SELECT`,沒問題;但這證明「用 `.format()` 組 SQL」是這個檔案作者已經上手的自然寫法,未來同一支檔案裡有人比照這個既有寫法加一句 `UPDATE`/`DELETE` 補丁,守衛不會叫。

file: `tests/executor/write_scan.py:128-144`、`src/rtb/executor/attempt_store.py:673-676`

建議修法:`text_of()` 至少要能識別「字面模板 `.format(字面參數...)`」與 `字面 % 字面`(參數也是常數時直接代入拼回;含變數時才視為刻意繞過放行),否則在 `audit_violations()` 之外另加一條「掃到 `.format(` / ` % ` 呼叫且前一個運算元含 SQL 關鍵字」的保守觸發規則,寧可誤報也不要完全沉默。

### 2. 只要新開一個獨立的動態補欄位登記表,不接進 `inbox_store._ADDED_COLUMNS`,兩道檢查都同時失效
severity: major
blocking: 是

引句:「守衛另外直接讀那個登記表,斷言登記在七張表之下的每個欄位定義都可為空」

規格與程式碼把「動態表名補欄位」的例外處理死死綁在**唯一一個**登記表上:拼字串掃描碰到 f-string 組出的表名只剩 `{}` 佔位,看不出是哪張表(規格原話:「表名從一個登記表來、語句用 f-string 組,拼回來表名只剩占位,守衛看不出是哪張表」);於是守衛改成直接讀 `inbox_store._ADDED_COLUMNS` 這一個 Python 字典。`registry_violations()` 只吃呼叫端傳進來的 `registry` 參數,而 `test_audit_tables_are_only_ever_inserted_into` 裡唯一呼叫處寫死 `registry_violations(inbox_store._ADDED_COLUMNS)`(`tests/executor/test_audit_tables.py:49-52`、`60-65`)。

觸發情境:在 `/tmp/rtb-review/src/rtb/dsp/migrations.py` 新開一個「第二套」動態補欄位迴圈,跟 `inbox_store._migrate_columns()`(`src/rtb/executor/inbox_store.py:826-829`,857)一模一樣的寫法,只是換一個獨立登記表、不掛進 `_ADDED_COLUMNS`:
```python
_NEW_COLUMNS = {"operations": [("archived", "archived INTEGER NOT NULL DEFAULT 0")]}

def apply(conn):
    for table, columns in _NEW_COLUMNS.items():
        for _name, ddl in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
```
這給七張表之一的 `operations` 補了一個 `NOT NULL DEFAULT 0` 的欄位——正是規格自己點名「會讓舊列讀出新值,等於回頭改寫稽核意義」的那種違規(核可表補「已撤銷」欄預設 1 的例子)。`test_audit_tables_are_only_ever_inserted_into` 照樣全綠(1 passed):拼字串那道因為表名是變數看不見它,登記表那道因為它根本不在 `_ADDED_COLUMNS` 裡看不見它。這條路徑不需要用到任何「變數組表名」以外的刻意手法——它就是複製貼上程式碼裡已經存在的、唯一一個先例(`inbox_store._migrate_columns`)去做一件新功能,是最典型的「忘記守衛只認得那一個登記表」場景。

file: `tests/executor/test_audit_tables.py:49-52,60-65`、`src/rtb/executor/inbox_store.py:274,826-829`

建議修法:守衛不該寫死一個變數位址,應該全庫掃「`for` 迴圈裡對某個 dict/映射跑 `ALTER TABLE {變數} ADD COLUMN`」這種語法形狀,把所有符合這個形狀的登記來源都收集起來一起核對;退一步至少加一條測試斷言「全庫只有一處會執行 `ALTER TABLE {變數}... ADD COLUMN`」,新開第二處時先在那條測試炸開,逼人手動把新登記表接進同一份核對。
