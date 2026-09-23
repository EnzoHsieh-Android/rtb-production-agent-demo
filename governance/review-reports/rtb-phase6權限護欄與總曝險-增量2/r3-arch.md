severity: clean

本輪鏡頭只判三件事有沒有引入專案原本沒有的第二種做法或跨層:靜態匯入閉包掃描、「歷史相容代碼」分類、BLOCK_TRIGGERS 被取代的方式。三件都查過,沒有發現構成 major 的第二種做法或跨層問題,理由如下(皆一般觀察,不算發現)。

**靜態匯入閉包掃描。** 對照 `tests/analyzer/test_boundaries.py` 現有兩種手法:(1) 單一目錄內的 AST 掃描,不跟隨匯入(`test_the_analyzer_reaches_the_network_only_through_the_shared_client`、`test_the_analyzer_can_neither_read_the_signing_key_nor_import_the_signer`,兩支都只 `analyzer.rglob("*.py")`,不追出 analyzer 目錄外);(2) 在乾淨子行程載入分析行程模組、讀執行後的 `sys.modules`(`test_importing_the_analyzer_does_not_load_the_capability_module`)。用 `grep -rln "closure\|遞迴\|transitive"` 掃過 `src/`、`tests/` 全庫,確認專案裡目前沒有第三種「靜態追匯入閉包(從一批檔案出發,讀出任何位置含函式內部的匯入敘述、遇到本專案模組就展開繼續讀)」的手法,增量 2 的 [S408] 確實是新技法。但這不是無理由的第二套做法:設計本文與審計修正紀錄(第 1、2 輪)已把三種手法的覆蓋缺口寫清楚——單目錄 AST 掃描看不到領域層等共用模組(領域層是兩側共用的,只掃分析行程目錄看不到);子行程 `sys.modules` 檢查只看「模組載入時真的執行到的匯入」,函式內部、只在特定分支才觸發的匯入(寫在函式內部的匯入)不會被載入動作觸發,因此看不到。這剛好補了現有兩種手法各自的洞,而不是重複建一套已有的東西;而且專案本身在 S30 這條防線上就已經同時掛靠 AST 掃描與子行程動態檢查兩種手法作互補(見上面兩支測試同時存在,一支管靜態、一支管間接載入),新增第三種手法去補「函式內部匯入」這個兩者都補不到的洞,跟專案自己「同一件事多層手法互補」的既有慣例一致,不是跨層,也不是無正當理由多開一條路。逐字引句(凍結快照原文):

引句:「改用「載入後看 sys.modules」會漏掃寫在函式裡、執行到那一步才匯入的模組」

**「歷史相容代碼」分類。** 對照擋下原因列舉(`src/rtb/executor/inbox_store.py:73-74` 的 `class BlockCode(StrEnum)`,說明寫「每一種都有真實觸發路徑」)與既有其他封閉列舉(`Disposition`、`DeadLetterReason`、`LastFailure`)的說明手法,專案裡目前沒有任何列舉把成員標成「保留給讀舊資料、不會再被寫入」這種狀態。但這個分類只用在完整性測試(`test_the_guardrail_table_covers_every_block_code`,[S404])與模組說明文字的措辭調整上,production 端的 `BlockCode` 仍是同一個 `StrEnum` + 資料庫 `CHECK` 限制,運作機制完全沒變,只是多寫一句「回退後這個成員暫時不會再被產生,但仍要留著讓舊資料讀得回來」。這是同一層(測試/文件層)對既有機制的語意補註,不是另開一條技術路徑,也不牽動 production 的判斷邏輯,不構成第二種做法或跨層。逐字引句:

引句:「「歷史相容代碼」是規則已拿掉、只為了讀舊資料而留在列舉裡的代碼,現在是空的」

**BLOCK_TRIGGERS 被取代的方式。** 對照 `tests/executor/test_execution.py:152-179` 現有寫法:`BLOCK_TRIGGERS` 是「擋下原因 → 觸發函式」的字典,配合 `@pytest.mark.parametrize("code", list(BlockCode))` 與 `assert set(BLOCK_TRIGGERS) == set(BlockCode)` 斷言鍵等於整個列舉。增量 2 設計把這條「鍵等於整個列舉」的斷言,換成「三份明列清單聯集等於列舉、兩兩交集為空」的完整性斷言([S404]),而且明講原本掛在 `BLOCK_TRIGGERS` 上的 `OPERATION_PREVIOUSLY_FAILED` 觸發要「搬成邊界表之外的一支單獨測試,不丟覆蓋」。這仍是同一種表驅動參數化測試手法的延伸(字典/表格 + 逐列斷言),沒有引入第二種測試技術,也沒有讓覆蓋憑空消失。逐字引句:

引句:「同一操作先前已失敗原本在那張表的觸發,搬成邊界表之外的一支單獨測試,不丟覆蓋」

file: `tests/analyzer/test_boundaries.py:1-160`(既有 AST 掃描與子行程兩種手法)
file: `tests/executor/test_execution.py:152-179`(BLOCK_TRIGGERS 現況)
file: `src/rtb/executor/inbox_store.py:44-80`(既有列舉說明手法對照)
