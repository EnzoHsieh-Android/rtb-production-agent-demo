severity: clean

第 2 輪只改了規則表邊界、比例算法邊界、寫入掃描的辨識方式與範圍、完整性測試的清單結構、S406/S407 的綁定寫法,這幾處逐一核對過,沒有發現引入第二種做法或跨層的問題。

**新版寫入掃描(S408):子行程載入 + AST 掃描是既有兩種做法的合法合併,不是第三種做法。**

引句:「掃描範圍:不只分析行程套件的檔案。比照既有「在乾淨子行程載入整個分析行程」那支測試,真的載入分析行程,取出這次載入進來、屬於本專案的每一個模組(分析行程自己、領域層、共用 HTTP 用戶端、資料庫工具等),每一支都掃」

核對 `tests/analyzer/test_boundaries.py`,現有確實只有兩種做法:靜態目錄掃描(`test_the_analyzer_reaches_the_network_only_through_the_shared_client`、`test_the_analyzer_can_neither_read_the_signing_key_nor_import_the_signer`,`file: tests/analyzer/test_boundaries.py:96-150`,用 `analyzer.rglob("*.py")` 取檔案清單再 `ast.walk` 解析)與子行程載入看 `sys.modules`(`test_importing_the_analyzer_does_not_load_the_capability_module`,`file: tests/analyzer/test_boundaries.py:153-171`,只斷言禁止模組沒被匯入,不解析原始碼)。r2 設計把「取檔案清單」的方式從靜態 `rglob` 換成「子行程載入後讀 `sys.modules`」,取得的模組清單之後仍然是用既有的 AST 解析手法去掃呼叫——這正是第 1 輪寫入掃描席自己在 F2 給的建議(見 `governance/review-reports/rtb-phase6權限護欄與總曝險-增量2/r1-scan.md`:「比照既有 `test_importing_the_analyzer_does_not_load_the_capability_module` 的做法,額外用子行程真的把分析行程整個載入,對 `sys.modules` 裡…每一支模組都跑一次同樣的呼叫掃描」)。換句話說是把既有做法 2 的「發現範圍」接到既有做法 1 的「掃描手法」上,兩塊都是這支測試檔本來就有的技術,沒有新增第三種機制(沒有掛 lint 外掛、沒有執行期攔截、沒有 monkeypatch)。

呼叫辨識方式(不靠函式名稱字面比對、改追「共用 HTTP 用戶端的請求函式被綁定到哪些名字」,把別名賦值、當參數傳出去、偏函式包裝一律當違規)同樣是延續既有 AST 解析的粒度,只是解析目標從「有沒有 import 某模組」換成「怎麼取得並使用某個匯出物件」,仍在同一支測試檔、同一種靜態解析範式內,不是換成語意/型別層級的新分析技術。

**完整性測試的兩份明列清單(S404),是測試資料層級的新分類,不是新的產線機制或第二套完整性判準。**

引句:「完整性測試用兩份明列的清單,不用整個列舉當全集…斷言:兩份清單的聯集等於整個列舉、交集是空的」

既有 `BLOCK_TRIGGERS`(`file: tests/executor/test_execution.py:152-167`)斷言 `set(BLOCK_TRIGGERS) == set(BlockCode)`,目的是「每個代碼都至少有一種觸發方式」,universe 是整個列舉;S404 要驗的是不同的東西(「護欄表涵蓋的代碼剛好是單筆規則」),universe 天然就要排除總曝險這種非單筆代碼,所以不能沿用同一份 universe。新增「單筆規則代碼/非單筆代碼」兩份清單只是測試檔案內的資料結構,沒有新增產線程式碼、沒有讓 `precheck`/簽發器多一個第三處判斷點,跟 `BLOCK_TRIGGERS` 的關係是「另一個目的的清單」而非重複或取代,兩者可以並存,不構成架構上的分岔。

**S406(舊收件表能寫入新代碼)沿用既有「手造舊 schema 資料庫」測試手法,不是新技術。**

`tests/executor/test_queue.py` 已有 `test_an_old_inbox_database_migrates_to_four_dispositions_without_losing_data`(`file: tests/executor/test_queue.py:106`)與 `test_only_an_outdated_constraint_also_triggers_a_rebuild`(`file: tests/executor/test_queue.py:128`),都是用原生 `sqlite3` 連線手造帶舊 CHECK 限制的資料庫,再驗證開啟時的重建行為。S406 用「增量 1 版列舉(含總曝險代碼、不含比例代碼)建舊庫」驗證,是同一種手法換一組列舉版本,沒有引入新的資料庫測試機制。

**增量 1 修法是否通用、增量 2 是否需要自己改判斷,設計本身已用條件句處理,不構成「另立一份」。**

查證 `/Users/enzo/rtb-3b` 目前 `src/rtb/executor/inbox_store.py:290-296`(`_proposals_outdated`)只核對 `Disposition.DEAD_LETTER` 是否出現在 `proposals` 表的建表 SQL 裡,不看 `block_code` 欄位的 CHECK 限制,跟設計「現況」小節描述的「只看死信這個處置在不在限制裡,不看擋下原因」一致;而增量 1 的實作程式碼在這個分支裡尚未寫(只有 `0bfd852`/`d5b5368` 兩個 docs-only 提交,沒有對應的 `src` 改動)。設計原文用「若增量 1 的修法不是通用的,增量 2 改那段判斷,不另寫一份」把「先實作的增量 1 是否通用」當成待驗條件,而不是預設它通用後就另開一支平行的重建判斷——這正是題目要求的「增量 2 改那段判斷,不另寫一份」的字面落實,沒有觀察到會導向寫出第二份重建邏輯的措辭。

比例上限放進 `precheck`、不進簽發器,規則表維持「程式只有兩處、表是測試資料」的定位,回應合併沿用既有 `_PERMISSION_BLOCKS` 這唯一合併點——這幾處第 1 輪已判 clean(`governance/review-reports/rtb-phase6權限護欄與總曝險-增量2/r1-arch.md`),第 2 輪的折入(邊界列數、比例基準順序說明、回退段落)沒有動到這幾處的模組歸屬,程式碼(`src/rtb/executor/execution.py`、`capability_signer.py`、`inbox_server.py`)在這兩輪之間也沒有異動(期間只有 docs 提交),原判斷仍成立。
