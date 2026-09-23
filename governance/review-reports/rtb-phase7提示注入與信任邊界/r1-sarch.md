severity: minor

**問 1:分層與依賴方向**
對齊。廣告文字的欄位白名單留在分析端 DSP 用戶端(既有家,不進領域層),領域層(`evidence.py`)只加型別層的通用自我檢查,不需要知道 DSP 欄位名(`governance/review-reports/rtb-phase7提示注入與信任邊界/r1-snapshot.md:77`,對照既有 `src/rtb/analyzer/dsp_client.py:58-90` 同一支檔已經是「打 HTTP、轉成 Evidence」的家)。對抗性素材刻意只能放測試目錄,理由是「既有的原始碼掃描測試只掃分析行程的正式程式目錄」,這句話跟實際程式一致(`r1-snapshot.md:158`,對照 `tests/analyzer/test_boundaries.py:108` 的 `analyzer = ... / "analyzer"`)。沒有發現跨層直呼。

**問 2:命名與錯誤處理**
對齊。可信欄位壞掉時「整個讀取丟出例外,跟『請求失敗』走同一條路」,沿用既有 `DspRequestFailed` 這條唯一的失敗路徑,不另開新例外類型(`r1-snapshot.md:81`,對照 `src/rtb/analyzer/dsp_client.py:29-55`)。增量 5 選擇在標準錯誤寫一行固定格式紀錄(`r1-snapshot.md:246`),跟專案現有的日誌寫法完全一致——專案沒有用 `logging` 模組,`inbox_server.py`、`runner.py` 都是直接 `sys.stderr.write(f"...\n")`(對照 `src/rtb/executor/inbox_server.py:149`、`src/rtb/executor/runner.py:93-151`)。

**問 3:第二種做法**
大致對齊,一條 minor。分析端 DSP 用戶端的欄位白名單與執行端 DSP 用戶端既有的逐欄驗證(`_positive_int` 等)屬同一威脅模型的延伸,不算第二種做法;短代號格式明確「跟識別碼同一個格式」,對應既有 `_checks.ID_PATTERN`(`r1-snapshot.md:70`,對照 `src/rtb/domain/_checks.py:12`);Mock-DSP 補欄位遷移完全比照既有 `_migrate_columns`(`r1-snapshot.md:126`,對照 `src/rtb/dsp/store.py:217-245`)。落差只在「已截斷」布林旗標,見下方 finding。

**問 4:落點合不合理**
對齊。五筆 `lands_in` 全部對到既有 Systems 節點已登記的 `about_code`(`r1-snapshot.md:287-291`):證據型別/自我檢查→任務流程領域模型(已列 `evidence.py`、`_checks.py`);DSP 白名單/決策規則/F5→分析行程流程與檢查點(已列 `dsp_client.py`、`policy.py`,且該篇已有 F5 預告合約 `★INVARIANT-PLANNED★`);名稱欄位→Mock-DSP(已列 `store.py`);結構測試說明→執行迴圈(已列 `execution.py`);拒收紀錄→提案收件口(已列 `inbox_server.py`,且該篇既有 RULE 明講「格式不合法的請求不碰資料庫、不記事件」,增量 5 的設計正是延續這條 RULE,不是推翻它)。各篇現況 60~97 行,沒有超載或該另開一篇卻塞進去的跡象。

### 1. 廣告名稱截斷新增「已截斷」旗標,跟既有靜默截斷慣例不同
severity: minor
blocking: 否(只是資料表示方式跟既有慣例不同,驗證結構、分層都沒問題)
引句:「當名稱超過不可信文字上限,DSP 用戶端應截斷並標記已截斷」
敘述:專案目前對超長不可信字串的既有處理都是靜默截斷、不留旗標(`error_detail` 進歷史表前截斷到 2000 字元、提案解析的 `unknown_field` 鍵名截斷到 64 字元),沒有任何一處記錄「這個值被截斷過」。這份設計為廣告名稱新增一個「已截斷」布林(S206),是專案裡第一個帶截斷旗標的欄位,屬於一種新的資料表示做法,雖有其道理(Phase 6 要顯示給人看)但目前沒有既有慣例可對照,只能算跟鄰居不一致。
佐證:file: `src/rtb/domain/proposal.py:234`

不對齊共 1 條,其中 major 0 條。
