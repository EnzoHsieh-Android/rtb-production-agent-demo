severity: major
我已完成驗收與回歸測試。以下是完整報告全文(依 `/private/tmp/claude-501/-Users-enzo/36c33b75-fd17-4bff-aa49-20f97a550e58/scratchpad/p9i4-fmt.md` 的格式規則撰寫)。

---

severity: major

## 逐條驗收(對第 1 輪四條,r1-intake.md 的 arch-1、arch-2、tests-1、tests-2)

1. **死信兩張表併入九張表共用判定**(`tests/executor/write_scan.py:216-232` 的 `audit_violations`,由 `tests/executor/test_dead_letter.py:462-465` 的 `_dead_letter_violations` 呼叫):原本兩份探針(UPDATE OR REPLACE、ON CONFLICT…DO UPDATE,都用「動詞跟表名中間切字」的拆法)已改用「同段共現」判定,實測 `tests/executor/test_dead_letter.py:478-483` 五條殺傷力配方全部正確翻紅;死信兩張表現在連可為空的補欄位都不准(`NO_ALTER`),比舊版更嚴。**此條已修**,沒有找到意外放寬(見下方「回歸檢查」)。
2. **調查實演改用執行端共用 `Clock`**(加 `start`/`tick`/鎖,`tests/executor/conftest.py:66-86`),不再另開 `VirtualClock`。`tests/ops/test_investigation_drill.py:603-607` 的新測試,與全套既有直接用 `Clock()`/改 `.now` 的測試(`test_stale_decision.py`、`test_inbox_server.py`、`test_multi_worker.py`、`test_inbox_disposition.py` 等)都沒有受影響,全套 1666 條通過。**此條已修,沒有找到回歸。**
3. **`text_of` 認得字面模板 `.format(參數)` 與 `%` 組字串**(`tests/executor/write_scan.py:162-173`),r1 原探針(全字面參數、含變數保守回退)都如期翻紅(`tests/executor/test_audit_tables.py:76-86`)。**原探針已修**,但額外變異測試找到兩個「忘記」型漏洞,見下方發現 1、2。
4. **動態補欄位只准一處**,守衛改用「全庫掃 `ALTER TABLE {}`/`%s` `ADD COLUMN` 語法形狀」而不是寫死唯一路徑(`tests/executor/write_scan.py:242-247`,`tests/executor/test_audit_tables.py:89-100`)。r1 原探針(f-string 版第二個登記表)確認會讓 `dynamic_add_column_sites` 從 1 處變 2 處、測試炸開。**原探針已修**,但額外變異測試找到同一根因的漏洞讓這條防線失效,見下方發現 2。

## 回歸檢查

- 全套測試:`/Users/enzo/rtb-p9i4` 下 `PYTHONPATH=src /Users/enzo/rtb-p9i1/.venv/bin/python -m pytest` → 1666 passed,沒有新掛。
- `text_of` 認得 `.format`/`%` 之後回頭跑全套,沒有對既有正式程式(如 `src/rtb/executor/attempt_store.py:673-676` 用 `.format()` 組 SELECT)誤報。
- 死信守衛的比法從「動詞緊貼表名」換成「同段共現」是嚴格超集(舊版抓得到,代表同一段裡動詞與表名必然同時出現,新版一定也抓得到),加上死信兩張表現在連補欄位都不准——只變嚴,沒有找到意外放寬的路徑。
- `Clock` 加 `start`/`tick`/鎖之後,`tick` 預設 `None`(不自動走),舊的零參數呼叫 `Clock()` 語意不變;全庫沒有任何地方用位置參數呼叫 `Clock(...)` 或依賴無鎖時序,全套跑過沒有受影響。

## 新發現(額外變異測試,超出第 1 輪四條原本描述的範圍)

### 1. 用 `+` 串接時若切點剛好落在空白上,`text_of` 會把分隔用的空白吃掉,九張表與死信兩張表整段偵測失效
severity: major
blocking: 是
引句:「相鄰字串、用 + 串起來的字串、f-string 的固定片段(代入的值記成 {})、」

`text_of` 對 `ast.BinOp`(`+`)的處理是先分別對左右兩段各自呼叫 `text_of`(每次結尾都做 `" ".join(text.split())` 正規化,壓平頭尾空白),再把兩段結果直接相加(`tests/executor/write_scan.py:157-161`)。如果原始碼把一句 SQL 從「空白處」切成兩段(例如把動詞跟表名分開兩行、前一段結尾留一個空格當分隔),各自正規化時那個結尾空格被當成頭尾空白吃掉,兩段接起來後動詞跟表名之間完全沒有分隔——不是「拼不回來就跳過」,而是拼回一句語意上仍是同一句 SQL 的字串,但這個字串因為缺了空格,正好躲過需要獨立單字邊界的比對。

觸發情境:在 `src/rtb` 任一支正式程式加一句自然拆行寫法,例如
```python
conn.execute("UPDATE OR REPLACE " + "dead_letters SET status = 1")
```
或針對七張表任一張:
```python
conn.execute("ALTER TABLE operations ADD COLUMN " + "flag INTEGER NOT NULL DEFAULT 1")
```
這跟本專案既有「拆開字串」的慣例同一類寫法,不是刻意繞過。已在 `/private/tmp/claude-501/-Users-enzo/36c33b75-fd17-4bff-aa49-20f97a550e58/scratchpad/rtb-review` 的副本裡實測:兩句加入原始碼後,`test_audit_tables_are_only_ever_inserted_into` 與 `test_the_dead_letter_tables_are_only_ever_inserted_into` 都維持全綠——拼回結果分別是 `'UPDATE OR REPLACEdead_letters SET status = 1'`、`'ALTER TABLE operations ADD COLUMNflag INTEGER NOT NULL DEFAULT 1'`,動詞跟表名/欄位定義黏在一起,兩道守衛都比對不到。這段 `text_of` 邏輯本次代碼審沒有改動(Phase 8 舊版 `TABLE_WRITE` 正則也一樣中招),不是這次修法新引入的問題,但代表第 1 輪判定「九張表/死信共用判定已修」的範圍,遇到這種常見拆行寫法時仍是恆綠。

file: `tests/executor/write_scan.py:157-161`

建議修法:把「壓平空白」這一步從遞迴的每一層 `text_of` 呼叫裡拿掉,改成只在 `reconstructed_strings` 最終回傳前對整段文字做一次;或者 `+` 分支相加前,只在其中一段原本就有結尾/開頭空白時保留原樣、不要提前正規化。

### 2. `.format(具名關鍵字=…)` 與 `% 字典` 完全不進拼字串掃描,同時讓「改寫偵測」與「動態補欄位只准一處」兩道守衛一起看不見
severity: major
blocking: 是
引句:「含變數時保守回模板本身(模板裡有改寫語句與受守護」

第 1 輪補的 `.format` 辨識條件是 `isinstance(node.func, ast.Attribute) and node.func.attr == "format" and not node.keywords`(`tests/executor/write_scan.py:168-169`)——`.format()` 只要帶了任何具名關鍵字引數,整個 `ast.Call` 節點就完全落不進任何分支,`text_of` 直接回 `None`,不會像「模板含變數」那樣退回模板本身;模板字串(`ast.Constant`)雖然還是會被 `reconstructed_strings` 當獨立片段撿回來,但裡面的佔位符是 `{table}` 這種具名欄位,不是受守護表名的字面值,`_TABLE` 正則比對不到,整段等於沒被掃到。`%` 這邊也有一樣的洞:`node.right` 不是 `ast.Tuple` 時包成單一元素清單(`write_scan.py:166`),右邊是 `dict`(`%(name)s` 具名佔位的慣用寫法)時,`_constant` 對 `ast.Dict` 回 `None`,`_formatted` 照「含變數保守回模板本身」的邏輯回傳模板——但模板是 `"UPDATE %(table)s SET status = 1"`,佔位符同樣不是表名字面值,一樣比對不到,沒有文件宣稱的「模板裡有改寫語句與受守護表名就算」的兜底效果。

這個洞不只影響「改寫偵測」(`audit_violations`),連第 4 條剛修好的「動態補欄位只准一處」(`dynamic_add_column_sites`,`tests/executor/write_scan.py:242-247`)也一起中招:它認的佔位符是 `_DYNAMIC_ADD_COLUMN = r"\bALTER\s+TABLE\s+(?:\{\}|%s)\s+ADD\s+COLUMN\b"`,只認裸的 `{}` 或 `%s`;第二個登記表若跟 `inbox_store._migrate_columns` 的 f-string 寫法不同、改用具名 `.format(table=table, ddl=ddl)`,連 `ALTER TABLE {table} ADD COLUMN {ddl}` 這個字面模板都不會被 `reconstructed_strings` 撿到(理由同上:帶關鍵字的 `.format` 整個 `Call` 回 `None`),`dynamic_add_column_sites` 連「多一個地方拼字串長得像動態補欄位」的痕跡都看不到。

觸發情境(在 `/tmp` 副本實測,均未改動 `/Users/enzo/rtb-p9i4`):
```python
query = "UPDATE {table} SET status = 1".format(table="operations")
# 或
query = "UPDATE %(table)s SET status = 1" % {"table": "operations"}
```
`test_audit_tables_are_only_ever_inserted_into` 全綠。另外
```python
_NEW_COLUMNS = {"operations": [("archived", "archived INTEGER NOT NULL DEFAULT 0")]}


def apply(conn):
    for table, columns in _NEW_COLUMNS.items():
        for _name, ddl in columns:
            conn.execute("ALTER TABLE {table} ADD COLUMN {ddl}".format(table=table, ddl=ddl))
```
——跟第 1 輪測試「殺傷力配方」用的 f-string 版一模一樣的違規(給 `operations` 補一個 `NOT NULL DEFAULT` 欄位),只是把 f-string 換成具名 `.format()`,`test_only_one_place_adds_columns_by_a_variable_table_name` 照樣全綠(不會從 1 處變 2 處)。這是本專案作者已經上手的另一種常見寫法(具名 `.format()` 引數在 Python 裡比位置引數更常見、更易讀),不是刻意繞過表名比對——表名字面值本身完全沒有被拿掉或改成變數,只是換了組字串的呼叫方式。

file: `tests/executor/write_scan.py:162-173`、`tests/executor/write_scan.py:242-247`

建議修法:`.format` 分支拿掉 `not node.keywords` 的排除,改用 `string.Formatter().parse(template)` 取出每個佔位符的名稱或位置索引,能對上具名/位置引數的常數值就代入,對不上或含變數就回模板本身(而不是整個節點消失不留痕跡);`%` 那邊同理,`node.right` 是 `ast.Dict` 時也要嘗試用 `%(key)s` 對應鍵值代入常數。退一步,至少讓保守回退時把模板裡所有具名佔位符先正規化成裸的 `{}`/`%s`,讓 `_DYNAMIC_ADD_COLUMN` 那類看裸占位符的規則還能認得出「這裡有一個動態補位」的痕跡。
