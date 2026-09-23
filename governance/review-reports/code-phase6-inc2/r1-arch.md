severity: clean

比例上限的常數與 `_increase_too_large` 放在 `src/rtb/executor/execution.py`,寫法跟同專案既有的模組級常數慣例一致:`capability_signer.py`、`attempt_store.py`、`inbox_store.py`、`runner.py` 都是「全大寫常數 + 行內中文註解」擺在模組頂端,例如：

引句:「LIFETIME_SECONDS = 120  # 簽發端用的有效期;DSP 端的上限是 300 秒」

新增的三個常數

引句:「MAX_INCREASE_NUMERATOR = 1
MAX_INCREASE_DENOMINATOR = 2
MIN_INCREASE_STEP = 1」

跟這個慣例同一個寫法,而且放在執行迴圈模組(而不是另開設定模組)也照設計筆記裡已經定案的位置寫。`_increase_too_large` 這種小型私有輔助函式(底線開頭、給 `precheck` 內部用)也跟同檔既有的 `_version_changed_or_none` 是同一種切法:一條規則配一支小函式,`precheck` 本體只留組裝與順序。

擋下原因的合併清單也是照既有寫法擴充,不是新起一套機制。`inbox_server.py` 的 `_PERMISSION_BLOCKS` 本來就是一個 `frozenset`,這次只是多塞一個成員：

引句:「BlockCode.CAMPAIGN_NOT_ALLOWED.value,
                                BlockCode.BUDGET_INCREASE_TOO_LARGE.value})  # Phase 6 增量 2」

`inbox_store.py` 的 `BlockCode` 列舉新增一個成員的寫法(底線開頭中文註解說明是什麼、哪個 Phase 加的)跟原本其他成員的風格相同,沒有另開第二套列舉或旁路清單。

`tests/executor/test_guardrails.py` 的表驅動寫法跟同專案既有的 `tests/executor/test_execution.py` 裡的 `BLOCK_TRIGGERS` 是同一個「決策表測試」路線,只是資料結構不同:`BLOCK_TRIGGERS` 是「一個代碼對一支觸發函式」的 dict(因為那邊只需要每種代碼各一個觸發範例),`GUARDRAILS` 是「規則、環境設定、輸入、預期結果」的四元組列表,因為護欄表要測邊界(同一個代碼要有「剛好擋下」與「剛好通過」兩列)。這個差異是設計筆記裡本來就定案的「規則表加表格驅動測試」(PRIOR-ART 段已經寫明是決策表測試的常見做法),不是另外發明了一套跟 `BLOCK_TRIGGERS` 打架的機制;兩者在各自檔案裡各司其職,`BLOCK_TRIGGERS` 沒被取代也沒被繞過,新增的一格(`BUDGET_INCREASE_TOO_LARGE: _over_ratio`)照樣塞進原本的 dict。

`tests/analyzer/test_boundaries.py` 新增的靜態匯入閉包掃描,跟同檔既有的兩種邊界檢查不是同一種做法,但也不是重複做同一件事,判斷下來不算引入競爭的第二種做法:

- 既有的 S53(`test_the_analyzer_package_never_imports_dsp_internals`)是呼叫外部 linter(ruff 的 TID251 banned-import 規則),管的是「分析行程的模組不准匯入 `rtb.dsp`/`rtb.executor`」這一條模組邊界。
- 既有的另一支(`test_importing_the_analyzer_does_not_load_the_capability_module`)是用子行程跑 `importlib` 真的載入模組、事後檢查 `sys.modules`,管的是「載入分析行程套件不會連帶把執行端模組拉進記憶體」。
- 新增的 `analyzer_import_closure` / `write_call_offenders`(手刻 `ast.walk` 掃描相對匯入、逐一檢查呼叫點的方法字面值與標頭)管的是 [S408] 這一條全新的要求:「分析行程除了送提案那一個 POST,不能有第二個寫入呼叫」。這件事 ruff 的匯入規則測不出來(合法匯入了 `request_json` 之後怎麼呼叫它,TID251 管不到呼叫點),`importlib` 那支也測不出來(它只看模組有沒有被載入,不看呼叫方法與參數)。全專案(`src/`、`tests/`)搜尋不到 `ast.walk`/`ast.parse`/`import ast` 的其他出現,這是這個檔案裡第一次出現手刻靜態掃描——但它解的是既有兩支測試都解不了的新問題,不是把已經有答案的問題再做一套競爭寫法,所以不落在「引入第二種做法」的 major 判準裡。這一支測試本身另外配了 `test_the_write_call_scan_catches_forgetful_variants`(十一種不小心的寫法各跑一次掃描),對照設計筆記 [S408] 的「複製一份原始碼放進十一種不小心的寫法,掃描都要抓到」,自我驗證的方式跟設計要求對得上。

三支既有測試改動(`test_execution.py` 的 `BLOCK_TRIGGERS` 多一格、`test_inbox_disposition.py` 的封閉列舉斷言多一個值、`test_multi_worker.py` 把並行測試第二份提案的新預算從 160 改成 140)都只是把資料改成落在新規則(比例上限)允許的範圍內,或者把新列舉成員加進既有的完整性斷言,沒有改動作測邏輯或斷言方式,跟原有寫法一致,不是為了避開測試失敗而更換驗證手法。

沒有發現跨層直呼:新程式碼與新測試都留在各自原有的層級(執行迴圈模組內部呼叫、分析行程邊界測試對 `src/` 做靜態讀取而不是執行期呼叫),沒有出現分析行程直接呼叫執行端內部、或執行端反過來呼叫分析行程的情形。
