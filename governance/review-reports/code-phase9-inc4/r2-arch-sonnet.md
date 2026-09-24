severity: major

# 審查報告

severity: major

### 1. 稽核政策(守衛本身)搬進宣稱「純掃描工具」的共用模組,write_scan.py 職責混雜、頭部宣告未更新
severity: major
blocking: 是

引句:「# ---- 稽核表只增不改(Phase 8 死信兩張表與 Phase 9 增量 4 七張表共用一套) ----」

觸發情境:專案自己的分工文件(`稽核表只增不改守衛.md`,本次修正也在改的同一篇)白紙黑字寫著「本篇管 `tests/executor/test_audit_tables.py`(守衛本身與殺傷力配方)與 `tests/executor/write_scan.py`(共用的原始碼掃描工具:判寫入函式、拼回拆開的字串;Phase 8 死信守衛、維運套件邊界測試、本守衛共用)」——即「守衛本身」(判什麼算違規)歸 `test_audit_tables.py`,`write_scan.py` 只負責通用、跟業務無關的掃描原語(判寫入函式、拼字串),而且這支原語同時被三個互不相干的用途共用(Phase 9 增量 1 的讀寫分類、Phase 8 死信守衛、本稽核守衛)。修正前 `audit_violations`、`registry_violations`、`GUARDED`/`REWRITES`/`ADD_COLUMN`/`LOOSENING` 這些「守衛本身」的判定邏輯也確實只定義在 `test_audit_tables.py` 裡(patch 裡整段用 `-` 從該檔刪掉)。

會出的錯:本輪修正把這些判定邏輯(哪 9 張表受管、什麼字樣算改寫、`NO_ALTER` 例外、動態補欄位偵測)整段搬進了 `write_scan.py`,變成該模組新的一段(`AUDITED`、`NO_ALTER`、`_REWRITES`、`_ADD_COLUMN`、`_LOOSENING`、`_DYNAMIC_ADD_COLUMN`、`audit_violations`、`registry_violations`、`dynamic_add_column_sites`,見 `tests/executor/write_scan.py:197-247`)。但 `write_scan.py` 自己檔首的宣告(這支檔案唯一該當真相的「負責什麼」聲明,本輪完全沒動,見 `tests/executor/write_scan.py:1-2`)仍只寫「機械判定哪些函式會寫資料庫」與「把拆開的字串拼回來」兩種讀法,並且明講「兩種讀法並存、名字分開」——沒有第三種。也就是說,這支被 Phase 9 增量 1(讀寫分類)、Phase 8(死信)、本守衛三方共用、原本刻意保持policy-free 的通用掃描原語,現在被塞進了一份具體業務規則(哪 9 張表、什麼算改寫動詞、哪些表連補欄位都不准)——而且檔首文件完全沒有反映這個新增的第三種職責。往後任何人只是想用 `write_scan.py` 做通用的「判寫入函式」或「拼字串」(例如未來新增第 4 個消費者),看到的檔首說明是不準的,得往下翻到第 197 行才會發現這裡其實也藏著一份特定稽核政策的具體常數;而稽核政策本身也因此離開了專案自訂分工說它該待的地方(`test_audit_tables.py`)。這正是「共用模組因合併而職責混雜」——不是把兩套判定統一成一套(那是第 1 輪要求、不重報),而是統一後選擇的落腳點違反了專案自己寫明的「掃描工具跟守衛政策分開」的既有分工,且沒有同步修正該分工聲明。

建議修法:把 `audit_violations`/`registry_violations`/`dynamic_add_column_sites` 與其專屬常數(`AUDITED`、`NO_ALTER`、`_REWRITES` 等)留在(或搬回)`test_audit_tables.py`,`test_dead_letter.py` 用一般的模組間匯入拿它;`write_scan.py` 只保留原本兩種 policy-free 的讀法。若堅持放在 `write_scan.py`,至少要把檔首文件字串(第 1-2 行)改寫,明確列出第三種職責與它只服務稽核不變量這件事,不能讓宣告跟內容脫節。

### 2. 修正加的新句子跟緊接著沒動的舊句子在同一段文件字串裡自相矛盾
severity: major
blocking: 是

引句:「Phase 8 死信兩張表跟它們共用同一套判定(代碼審第 1 輪),判定本身在共用掃描模組。」

觸發情境:`tests/executor/test_audit_tables.py` 模組文件字串這次改動加了上面這句,緊接著三行後是本輪完全沒動的舊句子(`tests/executor/test_audit_tables.py:6`,patch 裡是不帶 +/- 的原文脈絡行):「完整語句再比對。跟 Phase 8 那支不同的是比法:只要同一段拼回來的文字裡出現任一張表的表名,又出現任一種改寫語句就算違規」。

會出的錯:新句子講的是「Phase 8 死信兩張表現在跟七張表共用同一套判定」(統一了),舊句子講的卻是「跟 Phase 8 那支不同的是比法」(仍然不同)——兩句話在講同一件事上直接互相打臉。這不是無關痛癢的措辭:這段文件字串本來就是用來記「這個不變量現在的比對方法是什麼、跟 Phase 8 的關係是什麼」的設計說明(比照 CLAUDE.md 對「文件字串記脈絡」的要求),修正的重點正是「把 Phase 8 死信兩張表併進同一套判定,不要再留一套較弱的舊比法」(第 1 輪發現 1)。文件字串裡卻仍留著一句明講「跟 Phase 8 不同」的舊敘述,等於這次修正親手在自己的說明裡留下一句話,暗示「Phase 8 本來就該跟七張表用不同比法」——往後有人只讀這段文件字串(不去翻 `write_scan.py` 或 `test_dead_letter.py` 的實作),會被這句沒清掉的舊話誤導,以為現在允許甚至應該讓死信守衛的比法跟七張表不同,而重新分岔出第 1 輪剛修掉的那個「兩套判定」局面。

建議修法:把「跟 Phase 8 那支不同的是比法」整句拿掉或改成過去式加註(例如「Phase 9 增量 4 代碼審第 1 輪之前,曾跟 Phase 8 那支不同的是比法……本輪已統一」),讓同一段文件字串裡不要同時存在「已經共用」與「跟 Phase 8 不同」兩個互斥的宣稱。
