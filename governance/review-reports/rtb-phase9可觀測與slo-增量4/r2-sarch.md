severity: major

### F1 稽核守衛新開的「拼字串還原掃描」共用模組,沒有對照專案已有的同類掃描機制(`write_scan.py`),形成第二套平行做法
severity: major
blocking: 是
引句:「所以增量 4 的守衛要等這條分支合進主線後才實作」

說明:
設計裁定把 Phase 8 死信測試裡的 `_text_of`/`_strings`(還原相鄰字串、`+` 串接、f-string 固定片段)搬進「測試目錄的一支共用模組」,供死信守衛與增量 4 的六表守衛共用匯入,理由是「不複製第二份」。但這個決定只對照了 Phase 8 那一支要不要複製,沒有對照專案裡**已經存在**的另一支同類掃描共用模組:`phase9-inc1` 分支上的 `tests/executor/write_scan.py`(`tests/executor/write_scan.py:1`,docstring 明講「解析原始碼…只要有寫入語句就算寫入函式」,同樣是 AST 解析字串常數判斷 SQL 行為),而且已經有跨目錄匯入的慣例先例:`tests/ops/test_ops_boundaries.py:16` 直接 `from tests.executor.write_scan import classify`。

increment 4 的設計文本本身就有讀過 `phase9-inc1` 分支(裁定裡明寫「增量 1 的兩張在增量 1 實作分支目前叫生命週期事件表與 DSP 呼叫表」),等於作者已經打開過那條分支,卻沒有注意到同一條分支的 `tests/ops/` 已經在用 `tests/executor/write_scan.py` 做「解析原始碼判斷寫入行為」這件事,而是把 Phase 8 那支獨立的正規表示式+字串還原邏輯原封搬進另一個新模組。這造成專案裡同時存在兩套彼此不知道對方存在的「還原字串判斷 SQL 語句」機制:
- `write_scan.py`:AST 解析,含跨函式呼叫圖(`callees`)、模組層常數展開,但不處理字串相鄰、`+` 串接、與同段落多語句判斷。
- 增量 4 要搬的 Phase 8 兩支函式:處理相鄰字串/`+`/f-string 還原,但沒有呼叫圖與常數展開。

具體例:未來如果 `src/rtb/executor/inbox_store.py` 或 `dsp/store.py` 裡的某個「補欄位登記表」的目標表名是透過模組層常數間接組出來的(而不是直接 f-string 佔位),`write_scan.py` 的 `own_writes` 能沿常數展開抓到,但增量 4 打算搬的 Phase 8 版函式只認相鄰字串/`+`/f-string,對「常數變數組合」看不到——兩套判準不一致,同一段程式碼可能被一套判成違規、被另一套放過,而審查者/後續維護者不會知道要同時對兩套邏輯負責,因為設計文件從未提到 `write_scan.py` 的存在。

另外,r1-intake 的 refcheck 記錄提到 s4 席引用的落點是「建議新開的檔 `tests/_sql_scan.py`」——這是**頂層** `tests/` 目錄,而專案目前頂層 `tests/` 只放 `capability_samples.py`、`adversarial_samples.py` 這類跨網域樣本模組,唯一同類「掃描 src 判斷 SQL 行為」的共用模組(`write_scan.py`)是放在網域目錄 `tests/executor/` 下,依慣例被 `tests/ops/` 匯入。若真的落在 `tests/_sql_scan.py`,等於在放置慣例上也另起一套,跟現有「掃描類共用模組放在擁有該樣式的網域目錄、被別的網域匯入」的慣例不一致。

建議改法:增量 4 折入下一輪前,先比對 `write_scan.py` 能不能直接擴充(加相鄰字串/`+`/f-string 還原、加「同段含表名且含修改動詞」的判斷)取代或包住 Phase 8 那兩支函式,只留一套;至少要在設計文件裡明講兩套並存的理由與各自的判準差異,並把新模組放進 `tests/executor/`(跟 `write_scan.py` 同層,依現有慣例命名為 `<動詞>_scan.py`),不要落在頂層 `tests/`。

file: `tests/executor/write_scan.py:1`
file: `tests/ops/test_ops_boundaries.py:16`
file: `tests/executor/test_dead_letter.py:451`(main 分支的 `_text_of`/`_strings`,即增量 4 打算搬遷的來源)

---

補充查證(未列成 finding,供折入時參考):
- 虛擬時鐘「每讀一次自動前進 1 毫秒、執行緒安全」:比對 `tests/executor/conftest.py:17` 與 `tests/dsp/test_capability.py:25` 既有的 `Clock`,均是手動 `.advance()`、無鎖、無自動遞增——這確實是專案測試裡的新種時鐘語意。但這一點在第 1 輪已由外家席(x1-F4)提出並折入(「虛擬時鐘每讀一次前進 1 毫秒」正是那輪的折法本身),此輪重審沒有找到折法本身新增的破綻,不重報。
- DSP 請求處理類別改依冪等鍵排定故障:確認是延續 `tests/executor/test_execution_e2e.py:22` 的 `PlannedHandler`/`PlannedDsp` 覆寫模式(同一種擴充手法,不是另起一套),且專案裡沒有其他「依鍵排定故障」的既有機制被繞過或重複。
- 守衛直接讀收件口通用補欄位登記表(`_ADDED_COLUMNS`,模組私有常數):查到 `tests/executor/test_multi_worker.py:314` 已有先例直接匯入 `rtb.executor.execution._no_progress` 這類模組私有名稱,測試碰觸 src 私有常數在本專案是既有慣例,不算跨層違規。
- 實演不帶分析行程直送收件口:比對 `tests/executor/test_execution_e2e.py` 的 `World.submit()` 本身就是直接呼叫 `InboxStore.accept`、不經分析行程,組法一致。

共 1 條,blocking 1 條。
