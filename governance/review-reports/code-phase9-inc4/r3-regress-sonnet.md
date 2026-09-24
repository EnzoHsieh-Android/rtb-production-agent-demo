severity: major
Verification complete. Full report text below (格式已照 `/private/tmp/claude-501/-Users-enzo/36c33b75-fd17-4bff-aa49-20f97a550e58/scratchpad/p9i4-fmt.md` 撰寫)。

---

severity: major

## 逐條驗收(對 r2-intake.md 的 regress-1、regress-2、arch-1、arch-2)

1. **regress-1(`+` 串接在空白處切開會黏字)**:讀 `tests/executor/write_scan.py:191-211` 的 `_raw_text`,壓空白已經從遞迴的每一層搬到 `text_of`(`tests/executor/write_scan.py:214-221`)只做一次。在 `/private/tmp/claude-501/-Users-enzo/36c33b75-fd17-4bff-aa49-20f97a550e58/scratchpad/rtb-review-r3` 的副本裡實測 `'x = "UPDATE OR REPLACE " + "dead_letters SET a = 1"'` 與 `'x = "ALTER TABLE operations ADD COLUMN " + "flag INTEGER NOT NULL DEFAULT 1"'`,拼回結果都正確留著分隔空白(`'UPDATE OR REPLACE dead_letters SET a = 1'`、`'ALTER TABLE operations ADD COLUMN flag INTEGER NOT NULL DEFAULT 1'`),`audit_violations` 都正確翻紅。**此條已修。**

2. **regress-2(具名 `.format`、`%` 字典/元組、第二處動態補欄位)**:讀 `tests/executor/write_scan.py:140-188` 的 `_format_call`/`_percent`/`_substitute`。實測 `'"UPDATE {table} SET status = 1".format(table="operations")'`、`'"UPDATE %(table)s SET status = 1" % {"table": "operations"}'`、`'"UPDATE %s SET status = 1" % ("operations",)'` 三種寫法都正確拼回 `'UPDATE operations SET status = 1'` 並翻紅;第二處動態補欄位的具名版本 `'"ALTER TABLE {table} ADD COLUMN {ddl}".format(table=t, ddl=d)'` 與 `'"ALTER TABLE %(table)s ADD COLUMN %(ddl)s" % {"table": t, "ddl": d}'` 都被 `dynamic_add_column_sites` 正確數成第 2 處(`ALTER TABLE {} ADD COLUMN {}`、`ALTER TABLE %s ADD COLUMN %s`)。基本款(全常數、全變數)**此條已修**,但用「一個欄位是常數、另一個欄位是變數」的混合變異測出新洞,見下方發現 1。

3. **arch-1(稽核政策搬進 `write_scan.py`、頭部宣告未更新)**:讀 `tests/executor/write_scan.py:1-4`(現在明寫「只放兩種通用讀法…稽核表只增不改的判定…不在這裡,在同目錄的稽核守衛模組」)與新檔 `tests/executor/audit_guard.py:1-3`(承接 `AUDITED`、`NO_ALTER`、`_REWRITES`、`_ADD_COLUMN`、`_LOOSENING`、`_DYNAMIC_ADD_COLUMN`、`audit_violations`、`registry_violations`、`dynamic_add_column_sites`)。逐行比對搬移前後(r3-delta.patch 裡的 `-`/`+` 對照),邏輯與常數完全逐字相同,只多了 `from tests.executor.write_scan import reconstructed_strings` 這條匯入。`test_audit_tables.py`、`test_dead_letter.py` 都已改成從 `audit_guard` 匯入政策函式、從 `write_scan` 只匯入 `classify`/`reconstructed_strings`。知識圖譜 `docs/rtb-production-agent-demo-knowledge/Systems/稽核表只增不改守衛.md` 也同步把 `tests/executor/audit_guard.py` 加進 `about_code` 並改寫分工說明。**此條已修**,職責分離乾淨,沒有找到頭部宣告與內容不符的殘留。

4. **arch-2(文件字串新舊句自相矛盾)**:讀 `tests/executor/test_audit_tables.py:1-13`,原本互斥的「跟 Phase 8 那支不同的是比法」整句已拿掉,改成統一敘述「Phase 8 死信兩張表跟它們共用同一套判定…判定本身在同目錄的稽核守衛模組,Phase 8 死信那支也從那裡匯入」,不再跟前一句「共用同一套判定」打架。**此條已修。**

## 回歸檢查(全套 + 三個指定風險點)

- 全套測試:`/Users/enzo/rtb-p9i4` 下 `PYTHONPATH=src /Users/enzo/rtb-p9i1/.venv/bin/python -m pytest` → **1682 passed**(比 r2 的 1666 多 16 條,對應本輪新增的殺傷力配方),沒有新掛。
- `_raw_text` 不壓空白的風險:`test_audit_tables_are_only_ever_inserted_into`、`test_the_dead_letter_tables_are_only_ever_inserted_into` 這兩條每次都全庫掃 `src/rtb` 實際原始碼,全套通過已經涵蓋「對既有正式程式誤報」的檢查;另外複製到 `/tmp` 副本讀邏輯確認壓空白從「每層都壓」改成「只在 `text_of` 最外層壓一次」,不影響 `reconstructed_strings` 判斷「是不是拼得回來」的 `_raw_text(node) is not None` 這一步(那一步不看空白),沒有找到新的誤報路徑。
- 守衛搬家(`tests/executor/audit_guard.py`)風險:逐行比對搬移前後程式碼,`AUDITED`/`NO_ALTER`/`_REWRITES`/`_ADD_COLUMN`/`_LOOSENING`/`_DYNAMIC_ADD_COLUMN` 與三個函式的邏輯完全沒變,`test_dead_letter.py`(7+2 條殺傷力配方)、`test_audit_tables.py`(全部既有斷言,含 `GUARDED`、九張表建表語句各出現一次的檢查、`registry_violations`、`test_the_shared_scanner_keeps_both_string_readings`)都還在原檔且指向新匯入路徑,沒有找到掉配方或掉斷言。
- 格式規格代入風險:找到兩個新洞,見下方發現 1、2(都在本輪新寫的 `_format_call`/`_percent` 裡)。

## 新發現(額外變異測試,超出第 2 輪四條原本描述的範圍)

### 1. `%` 字典/元組只要有一個欄位解不出常數,整個模板連已經解出的常數表名都一起被抹平成裸佔位
severity: major
blocking: 是
引句:「mapping = {k: v for k, v in zip(keys, values, strict=True) if k is not None}」

`tests/executor/write_scan.py:170-180` 的 `_percent`:字典分支算出 `complete = None not in keys and None not in values`(`write_scan.py:176`)——只要字典裡有任何一個值解不出常數(例如欄位名是變數),`complete` 就是 `False`,整段回退成 `_substitute` 的保底路徑,把**模板裡所有**百分比佔位(包含已經解出常數、其實不用回退的那個)一律用正則正規化成裸 `%s`(`_PERCENT_FIELD.sub("%s", template)`,`write_scan.py:188`),不是只正規化解不出來的那一個。這跟 `.format` 的逐欄位處理(`_format_call`,`write_scan.py:140-167`——每個佔位獨立判斷、解不出來的那個才變成 `{}`,其他解得出來的照樣代入)行為不一致,而且是本輪新增的字典分支才有的洞(元組分支本來就有同樣的全有全無問題,但那是舊碼延續下來的,不是這輪新引入)。

觸發情境(在 `/tmp` 副本實測,均未改動 `/Users/enzo/rtb-p9i4`):
```python
def f(conn, col):
    conn.execute("UPDATE %(table)s SET %(col)s = 1" % {"table": "operations", "col": col})
```
表名 `"operations"` 是字面常數、只有欄位名 `col` 是變數——這是很自然的寫法(表名固定、欄位動態)。實測拼回結果是 `'UPDATE %s SET %s = 1'`,已經解出的 `"operations"` 完全消失,`audit_violations` 回傳 `[]`,對七張表任一張套用同樣寫法都測不出來,`test_audit_tables_are_only_ever_inserted_into` 對這種寫法會恆綠放行一次真正的 UPDATE。

同一個根因還會反過來造成**誤報**:如果表名是常數、只有補欄位的 DDL 是變數,例如
```python
def migrate(conn, ddl):
    conn.execute("ALTER TABLE %(table)s ADD COLUMN %(ddl)s" % {"table": "operations", "ddl": ddl})
```
拼回結果是 `'ALTER TABLE %s ADD COLUMN %s'`,連本來是常數的表名位置都被抹成 `%s`,`dynamic_add_column_sites` 會把這種「表名固定、只有欄位定義動態」的合法補欄位寫法,誤判成第二處「用變數組表名」,讓 `test_only_one_place_adds_columns_by_a_variable_table_name` 對這種合法寫法恆紅。已在 `/tmp` 副本用 `dynamic_add_column_sites` 實測,回傳 `[('fixed_table.py', 'ALTER TABLE %s ADD COLUMN %s')]`。

file: `tests/executor/write_scan.py:170-180`

建議修法:字典分支比照 `.format` 逐欄位處理——`_PERCENT_FIELD.sub` 只正規化「解不出常數」的那個具名佔位,解得出來的維持代入結果;或者退一步,只有在「一個值都解不出來」時才整段回退成正規化佔位,部分解出時逐一替換能替換的、其餘的才變成 `%s`。

### 2. `.format()` 帶精度(precision)格式規格會截斷常數字串,受稽核表名可能被截短躲過偵測
severity: major
blocking: 是
引句:「out.append("{}" if value is None else format(value, spec or ""))」

`tests/executor/write_scan.py:140-167` 的 `_format_call`,對每個解得出常數的佔位一律直接呼叫 `format(value, spec or "")`(`write_scan.py:164`)代入格式規格,沒有排除「精度(precision)套用在字串上會截斷」這個 Python 內建行為。寬度/對齊(如 `{:>10}`、`{:*>10}`)實測沒事(填充只加空白或非文字字元,`text_of` 最後 `" ".join(raw.split())` 會把多餘空白壓掉,而 `_table_pattern` 用 `\b`/`(?<![\w.])` 判邊界,非文字填充字元不影響比對),但**精度**是真的會刪掉字元。

觸發情境(在 `/tmp` 副本實測):
```python
query = "UPDATE {:.3} SET status = 1".format("operations")
```
`format("operations", ".3")` 回傳 `"ope"`(precision 對字串是截斷,不是像數字那樣四捨五入),拼回結果是 `'UPDATE ope SET status = 1'`,`operations` 這個受稽核表名被截成 `ope`,`_table_pattern` 比對不到任何一張受守護表名,`audit_violations` 回傳 `[]`。這不是刻意繞過表名字面值(表名本身沒被拿掉或換成變數),而是套用了一個常見於「固定寬度欄位」場景、但作者可能沒意識到「精度對字串是截斷」這個 Python 語意細節的格式規格,屬於「忘記」會踩到的寫法。

file: `tests/executor/write_scan.py:164`

建議修法:代入前解析 `spec` 裡的精度部分(例如用 `re` 抓 `\.\d+` 那一段),對字串值的佔位一律拿掉精度只留寬度/對齊/填充,或者更保守地——只要 `spec` 含 `.` 精度符號,就當作「代不進去」直接退回裸 `{}`,不要真的呼叫 `format()`。

---

跑測試指令與結果:`cd /Users/enzo/rtb-p9i4 && PYTHONPATH=src /Users/enzo/rtb-p9i1/.venv/bin/python -m pytest` → **1682 passed in 95.09s**,沒有新掛、沒有改動工作樹(所有變異測試都在 `/private/tmp/claude-501/-Users-enzo/36c33b75-fd17-4bff-aa49-20f97a550e58/scratchpad/rtb-review-r3` 的副本裡進行,測試後已清除)。
