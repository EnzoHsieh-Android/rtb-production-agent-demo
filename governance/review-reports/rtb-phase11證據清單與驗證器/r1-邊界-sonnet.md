severity: blocker

### F1 巨大整數會讓 json.loads 在驗證器自己的邏輯跑之前就丟出未分類的 ValueError

severity: blocker
blocking: 是(不是 JSONDecodeError,規格宣稱的「結束代碼 2」與「列出原因」機制接不住,退化成未預期的當機/雜訊)
引句:「清單讀不懂;結束代碼 0 是全通過、1 是有擋下、2 是清單讀不懂」
file: `/tmp` 實測(見下)——`json.loads('{"n": ' + '9'*5000 + '}')` 丟出 `ValueError: Exceeds the limit (4300 digits) for integer string conversion`,`isinstance(e, json.JSONDecodeError)` 為 `False`。
這不是格式錯誤,是 Python 3.11+ 內建的整數字串轉換位數上限(`sys.set_int_max_str_digits`),跟 spec 設想的「JSON 語法錯誤才印結束代碼 2」完全是兩回事。若驗證器只 `except json.JSONDecodeError` 來對應「清單讀不懂」,manifest_version 或任何欄位塞一個上千位數的整數會讓程式以未捕捉例外中止,印出 Python traceback 而不是 spec 承諺的「列出原因」,退出碼也不保證落在 0/1/2 三者之一(未捕捉例外預設是 1,會被誤讀成「1 是有擋下」)。

### F2 JSON 重複鍵在解析階段就被靜默吃掉,清單原始檔跟驗證結果會對不上

severity: major
blocking: 是(違反「審查員看得到差異」的設計前提)
引句:「清單不帶任何結果;結果一律由驗證器算」
file: `/tmp` 實測——`json.loads('{"a": 1, "a": 2}')` 回傳 `{'a': 2}`,前一個鍵值在 `dict` 建好之前就消失,驗證器完全看不到「這個檔案曾經寫過兩次同一個鍵」。
spec 全篇沒有提到重複鍵的處理,而白名單檢查(1 格式)是在 `json.loads` 之後的 `dict` 上做,那時候重複鍵已經被 Python 合併成最後一個值。一個人手改 `scope` 兩次(例如複製貼上沒刪舊區塊),審查員讀原始檔案的 diff 會看到兩段 `scope`,但驗證器只認最後一段——這正好打破「守衛面」段落自己承認的天花板依賴:「唯一的效果是逼那次改動在同一個提交裡讓清單出現差異、讓審查員看得到」,重複鍵讓「看得到」這個假設在解析層就先失效。

### F3 檔名大小寫在 macOS 本機與 Linux CI 行為不一致,直接牴觸「本機與 CI 同一個指令、結果可重現」

severity: blocker
blocking: 是
引句:「本機用同一條指令;README 或 Systems 家寫清楚」
file: `/tmp/case_test` 實測——本機(Darwin/APFS)`os.path.exists('lowercase.py')` 與 `os.path.exists('LOWERCASE.PY')` 對同一個實體檔案都回傳 `True`;`diskutil info /` 確認 `File System Personality: APFS`(預設不分大小寫、保留大小寫)。GitHub Actions 的 `ubuntu-latest` 跑者用 ext4,是分大小寫的檔案系統。
如果 `scope`/`harness`/`symbols` 裡的路徑大小寫跟磁碟上實際檔名有一兩個字母對不上(例如複製貼上打錯大小寫、或原檔案曾經被大小寫重新命名過),本機開發者跑驗證器會過(macOS 靜默容忍大小寫差異),推到 CI 卻因為「存在」檢查(`os.path.exists`/開檔讀 sha256)在 ext4 上找不到檔案而擋下——這正是 spec 第 19 行要求的「驗證結果在本機與 CI 可重現」的反例,而且是本機通過、CI 擋下的方向,等於本機測不出這個地雷。

### F4 scope/harness/symbols 沒有限制在 repo 樹內,`../`、絕對路徑、符號連結都能通過「存在」檢查

severity: major
blocking: 是
引句:「scope、harness、symbols 的檔案都存在」
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11證據清單與驗證器_計劃.md`(對照 /tmp 工作副本同一段)全文搜尋「相對路徑」「絕對路徑」「repo」「根目錄」皆 0 筆命中。
spec 對「存在」的定義只有「檔案都存在」,沒有要求路徑必須是相對於 repo 根目錄、也沒有禁止 `../` 跳出樹外或直接寫絕對路徑;symlink 的話 `open()`/`hashlib` 預設會跟隨連結讀目標內容再算雜湊,一樣通過存在檢查。scope 的定義是「這條宣稱涵蓋的正式程式檔清單」,但機械檢查完全沒有把「正式程式檔」這個語意限制成「repo 底下的原始碼」,一個絕對路徑指到 repo 外(甚至系統檔案)只要 sha256 對得上清單裡寫的值就會放行。這個天花板沒有被列進「實務隱患」段落,應該至少像雜湊重貼那條一樣被記下來,交給審查員肉眼看 diff。

### F5 symbols 的「找得到定義」若用語法樹全樹搜尋,巢狀函式、條件式定義(死碼)、類別方法、被重新指派的名字都會被誤判為存在

severity: major
blocking: 是
引句:「symbols 的名稱用語法樹在該檔找得到定義」
file: `/tmp/sample_symbols.py` 實測——對含有「`if False:` 裡的 `def dead_code_fn`」「`outer()` 內部的巢狀 `def inner`」「`class Foo` 底下的方法 `bar`」「先 `def reassigned(): pass` 後又 `reassigned = None` 重新指派」的檔案做 `ast.walk` 掃 `FunctionDef`/`ClassDef`,四種名字全部「找得到定義」:`['Foo', 'outer', 'reassigned', 'bar', 'inner', 'dead_code_fn']`。
spec 只寫「用語法樹在該檔找得到定義」,沒有排除條件式區塊裡的死碼、沒有要求巢狀函式必須可從模組層存取、沒有規定類別方法要用「類別.方法」而不是裸名比對、也沒處理「定義完馬上被重新指派成別的東西」這種名字實際上已經不是函式的情況。這四種都會讓「存在」檢查在字面上通過,但實際上程式執行時那個名字要嘛根本執行不到(`if False`)、要嘛已經不是原本那個函式(被重新指派)——跟 F4 相反,這裡是「該擋不擋」的方向,比 F4(該擋擋錯位置)更貼近安全漏洞:一份清單可以宣稱依賴一個其實已死或已被覆蓋的符號,機械檢查仍然放行。

### F6 enumerations 的常數若寫成 `X: T = (...)`(帶型別註記)這種寫法,若實作只掃 `ast.Assign` 不掃 `ast.AnnAssign` 會被誤判成「讀不出字面常數」

severity: minor
blocking: 否(方向安全:多擋而不是少擋,失敗模式是擋住合法清單而非放行造假清單)
引句:「驗證器用語法樹讀出字面常數,得到應涵蓋的項目」
file: `/tmp` 實測——`ast.parse("X: tuple[str, ...] = ('a', 'b')")` 產生的節點是 `ast.AnnAssign`,不是 `ast.Assign`;若實作沿用「找 `ast.Assign` 且 target 是 `ast.Name`」的寫法(這是最直覺、也最貼近 spec 字面「讀出字面常數」的實作方式),會漏掉這個賦值,落入 spec 第 54 行自己講的「登錄表讀不出字面常數(動態組出來的)也擋,不放行」分支,把一個完全靜態的型別註記常數誤判成「動態組出來的」而擋下。目前實際程式碼 `src/rtb/dsp/server.py:110` 的 `CAMPAIGN_WRITE_ACTIONS = ("update_budget", "pause_campaign")` 是普通 `ast.Assign`,五條正式清單不受影響,故列 minor 而非 major。

### F7 pytest 節點編號帶非 ASCII 字元(例如中文 parametrize id)時,collect 出來的實際 id 是 `\uXXXX` 逃逸過的字面字串,不是真正的中文字元;字面照抄會讓合法測試被誤判「找不到」

severity: minor
blocking: 否(方向安全:spec 第 58 行本來就把「找不到」設計成擋下並指名是哪個編號,不是靜默放行)
引句:「跳過、預期失敗、沒收集到、被取消選取都算擋」
file: `/Users/enzo/rtb-production-agent-demo/.venv/lib/python3.14/site-packages/_pytest/python.py:1041,1571`(`_ascii_escaped_by_config` 呼叫 `ascii_escaped`)。實測:`@pytest.mark.parametrize("x", ["中文"])` 產生的節點編號經 `--collect-only -q` 印出是 `test_param[中文]`(反斜線加 u 加十六進位碼點的**字面文字**,不是解碼後的中文);拿真正的中文字元 `test_sample.py::test_param[中文]` 去選測會得到 `ERROR: not found ... no tests ran`、結束代碼 4;换成字面反斜線逃逸字串 `test_sample.py::test_param[中文]` 才能選中並通過(exit 0)。
這代表:如果撰寫清單的人是照著意圖手打中文 id(而不是照抄 `pytest --collect-only` 印出來的逃逸字串),驗證器會如 spec 設計般正確擋下並回報「找不到」,行為對——但 spec 完全沒提醒過這個 pytest 逃逸規則,容易讓作者誤以為驗證器壞掉、去改測試 id 而不是改清單寫法。列 minor 是因為失敗方向正確(擋下),只是可用性陷阱、不是安全缺口。

### F8 covers 標了列舉範圍以外的項目(拼字錯誤/舊項目名)不會被拒絕,只是被靜默忽略

severity: minor
blocking: 否(不放行:多出的 covers 項目本身不影響「列舉裡的每一項都要被標到」這條檢查,真正該覆蓋的項目沒被標到時仍然照常被擋)
引句:「都要被至少一項證據的 covers 標到,少一項就擋並印出是哪一項」
spec 第 54 行只規定「列舉裡的每一項都要被 covers 標到」的單向覆蓋檢查,沒有反向規定「covers 裡的每一項都必須落在列舉範圍內」。這表示 covers 打錯字(例如把 `update_budget` 打成 `update_bugdet`)不會有任何獨立的錯誤訊息指出「這個 covers 值沒有對應到任何列舉項目」——會不會被擋純粹取決於正確項目 `update_budget` 是否**另外**被某一項證據標到;若巧合被別的證據標到,這個打錯字的 covers 值就完全沒人發現、白白留著。不擋是因為它不影響安全結論,只是清單裡的一段死文字,列 minor。

### F9 同一支檔案同時列在 scope 與 harness(同一份或跨份清單)沒有機械上的衝突檢查

severity: minor
blocking: 否(沒有找到會被機械檢查誤放行或誤擋下的具體場景)
引句:「另帶 harness:證據依賴的測試基礎設施檔...清單,每一項也帶 sha256」
scope 定義是「正式程式檔」、harness 定義是「測試基礎設施檔」,語意上互斥,但 spec 沒有寫驗證器要檢查兩份清單不重疊。這在機械上沒有壞處(兩份清單各自獨立算 sha256、各自比對,同一個檔案出現兩次只是被雜湊比對兩次,不會誤放行或誤擋),純粹是清單語意分類被作者寫錯不會被抓到,列 minor 且不 blocking——找不到讓它變成安全問題的具體失敗場景。⚠

### F10 空 claims/ 目錄、缺五條宣稱之一 —— spec 已明確涵蓋,已讀無 finding

引句:「沒有任何清單、或少了五條必要宣稱之一也擋」
S807 對應測試 `test_a_missing_required_claim_is_blocked` 已把這個情境列為合約候選,字面寫法直接涵蓋空目錄與缺項兩種情況,無 finding。

### F11 claim_id 跟檔名大小寫不一致(含跨檔重複 claim_id)—— 被「claim_id 跟檔名一致」的檢查連帶排除,已讀無 finding

引句:「claim_id(跟檔名一致)」
只要這條檢查逐檔嚴格比對 `claim_id == 檔名(不含副檔名)`,兩個不同檔名的清單就不可能同時通過並宣稱同一個 `claim_id`(至少有一個會因為跟自己檔名對不上而被擋);在大小寫不分的檔案系統(macOS)上兩個只有大小寫不同的檔名甚至無法同時存在。查證後排除,不成立為獨立 finding。

### manifest_version 是 bool(true)——已判斷但降級說明

severity: major
blocking: 是
引句:「manifest_version(整數,目前 1)」
file: `/tmp` 實測——`json.loads('{"manifest_version": true}')` 得到 `True`,`isinstance(True, int)` 為 `True`,且 `True == 1` 為 `True`。
spec 第 40 行只寫「整數」,沒有寫清楚型別檢查要用 `type(v) is int`(嚴格排除 bool)還是 `isinstance(v, int)`(bool 是 int 子類,會通過)。Python 的 `bool` 是 `int` 的子類這個語言層陷阱是老生常談,但 spec 對「型別」這條檢查的实作方式沒有明講,若實作者用最自然的 `isinstance` 寫法,`"manifest_version": true` 會被當成合法的版本 1 放行——這正好對上 spec 第 84 行「白名單以外的鍵就擋(所以 result、passed 這些欄位自然被擋)」想達到的「靠格式檢查擋掉造假」的精神,型別檢查沒把這個路徑講清楚就是同一個精神下的漏洞,故列 major。

## 總結

最嚴重等級:blocker;blocking 條數:6(blocker 2 條:F1、F3;major 4 條:F2、F4、F5、manifest_version 是 bool 那條)。minor 且非 blocking 4 條:F6、F7、F8、F9。F10、F11 已讀無 finding。
