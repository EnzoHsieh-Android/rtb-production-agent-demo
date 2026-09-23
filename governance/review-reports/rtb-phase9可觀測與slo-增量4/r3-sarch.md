severity: blocker

### F1 稽核守衛「拼回字串」函式併入增量 1 掃描模組,函式名稱與既有判準同名互撞,會悄悄改掉增量 1 的寫入判定

severity: blocker
blocking: 是
引句:「不另開第二支掃描模組,也不複製」

說明:
第 4 版把 Phase 8 死信稽核測試檔(`main:tests/executor/test_dead_letter.py`)裡「拼回字串」的兩支函式(`_text_of`、`_strings(tree)`,見該檔第 452–476 行)搬進增量 1 分支既有的 `tests/executor/write_scan.py`,理由是不要在專案裡開出第二套字串重組邏輯。但 `write_scan.py` 本身已經有一支同名的 `_strings(node)`(第 17–25 行),用途完全不同、簽名也不同:

- `write_scan._strings(node)`:只收 `ast.Constant` 字串常數,刻意排除函式/類別的文件字串,供 `_Module.own_writes()` 判斷一支函式是不是「寫入函式」(增量 1 [S621]、[S617]、[S600] 共用,`tests/ops/test_ops_boundaries.py:16` 直接 `from tests.executor.write_scan import classify` 拿它做維運套件邊界掃描)。
- `test_dead_letter.py._strings(tree)`:會把相鄰字串、`+` 串接、f-string 固定片段拼回完整語句,且**不排除文件字串**,供死信守衛比對表名。

兩者在模組層是同一個名字 `_strings`。照設計字面「搬進那一支……都從那裡匯入」直接搬運,Python 只認最後一個定義,後定義的 `_strings(tree)` 會整個蓋掉前面的 `_strings(node)`。之後 `_Module.own_writes()`(第 79–84 行)內部呼叫的其實是被覆蓋後的版本:語意變成「把整段函式拼回去的字串(含文件字串)拿去比對 SQL 關鍵字」,而專案的中文文件字串裡本來就常引用 SQL 關鍵字做說明(例如 `test_dead_letter.py` 自己的文件字串就寫了 `DROP TABLE`、`UPDATE` 等字眼)——一旦搬進 `write_scan.py`,任何函式只要文件字串提到這些字眼就會被誤判成「寫入函式」,`classify()`/`write_functions()`/`read_functions()` 的既有分類會悄悄跑偏,連帶動搖 `test_ops_boundaries.py` 靠它守的維運套件邊界不變量(S621/S617/S600),而且是**沒有拋錯的靜默錯誤**,不會在合併當下被發現。

`file: tests/executor/write_scan.py:17-25`(`git show phase9-inc1:tests/executor/write_scan.py`)
`file: tests/executor/test_dead_letter.py:452-476`(`git show main:tests/executor/test_dead_letter.py`)
`file: tests/ops/test_ops_boundaries.py:16`(`git show phase9-inc1:tests/ops/test_ops_boundaries.py`)

建議改法:設計裡明講合併時把死信那支重組函式改名(例如 `_reassemble` / `_dead_letter_strings`),`write_scan.py` 保留自己原本給 `own_writes()` 用的 `_strings(node)` 不動,兩套判準各自留一個獨立名字、都放在同一個檔案裡匯出即可,不需要也不應該共用同一個函式名。這不影響「不開第二支模組、不複製」的原意,只是補上「同檔要避免同名互撞」這一句,原作者顯然沒注意到這兩支同名函式語意不同。

---

共 1 條,blocking 1 條。
