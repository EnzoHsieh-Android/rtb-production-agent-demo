severity: major
# 審查報告:Phase 9 增量 3 測試殺傷力(governance/review-reports/code-phase9-inc3/r1-snapshot-*.patch)

severity: major

我在 `/private/tmp/.../scratchpad/p9i3-mut/repo`(複製自 /Users/enzo/rtb-p9i3,未動原始 repo)對照 patch 逐一做變異實驗:拿掉或改鬆實作關鍵一行,跑 `tests/ops/test_slo.py tests/ops/test_side_effects.py tests/executor/test_first_row_materials.py tests/dsp/test_commit_order.py tests/executor/test_read_only.py tests/ops/test_ops_boundaries.py tests/ops/test_window_readers.py tests/executor/test_attempt_store.py` 這組共 98 個測試,看會不會翻紅。多數邊界(14.4 門檻、MIN_SAMPLES=10、子窗 24 小時切點、DSP 翻頁 50 筆、DSP 窗口含首不含尾、max_budget/ratio_allowance/aggregate_used 剛好等於門檻、讀 DSP 先於開執行端快照、批量讀取不逐筆查)都被對應測試準確咬住,守得住。但有 4 處變異完全沒被任何測試發現,以下逐條列出。

### 1. 燒損評估器真正會被呼叫的命令列入口從沒被跑過,接線錯了也不會紅
severity: major
blocking: 是
引句:「counter=lambda name, since, until: sli.count(」

`src/rtb/ops/slo.py:245-246` 的 `run()` 是這個增量唯一會被人或排程實際執行的進入點,production 用的計數器就是這個 lambda。我把它的參數順序改成 `sli.count(name, until, since, sources)`(since/until 對調,等同任何一種「傳錯參數位置」的接線錯誤),跑完整組 98 個測試全數通過、無一翻紅。

進一步查證:全庫除了 `src/rtb/ops/slo.py` 自己以外,沒有任何檔案匯入或呼叫這個模組(`grep -rln "ops.slo" .` 只命中它自己),`tests/ops/test_slo.py` 也沒有任何一行呼叫 `slo.run(...)` 或 `slo.main(...)`。對照增量 2 的同類命令列入口,`tests/ops/test_metrics.py:254,495-503` 是直接呼叫 `m.run([...])` 驗證 `EXIT_OK`/`EXIT_NO_DATABASE`/`EXIT_WINDOW_TOO_LONG` 等真實出口;增量 3 的 `slo.py` 完全沒有對應測試,`_parse`、`run()` 內的參數繫結、`FileNotFoundError`/`DatabaseNotUpgraded` 兩個出口代碼全部零覆蓋。

現有測試(如 [S662])雖然用了真的 `sli.count`,但都是測試自己另外組一個 `def counter(name, since, until): return sli.count(name, since, until, sources(rows))` 閉包,不是呼叫 production 那一行 lambda;燒損數學的邊界測試([S656]-[S661])則清一色餵 `Planned` 假計數函式。三層測試互不重疊,production 真正會跑的那一行程式碼沒有任何測試觸碰過。

建議:在 `tests/ops/test_slo.py` 補一支呼叫 `slo.run([...])`(比照 `test_metrics.py` 的寫法)、用真資料庫與真 `sli.count` 走一次,斷言至少一條 SLI 的數字正確、且 `EXIT_OK`/`EXIT_NO_DATABASE`/`EXIT_NOT_UPGRADED` 三個出口都被驗到。

### 2. DSP 讀不到時「資料來源缺」與「零個違規」的關鍵區分完全沒測,靜默改成假綠燈也不會紅
severity: major
blocking: 是
引句:「return Tally(0, 0, missing=True)」

規格明講(計劃書「實作時的解讀」段):「DSP 讀不到時,兩條目標為零的指標回『資料來源缺』、違規欄為空,不當成 0 個事件(當成 0 會把看不到說成沒違規)。」`src/rtb/ops/side_effects.py:929`(`unauthorized`)與 `:957`(`duplicates`)各有一行 `except DspUnreadable: return Tally(0, 0, missing=True)` 正是實作這條規則。我把其中 `unauthorized()` 那一行改成 `return Tally(0, 0)`(拿掉 `missing=True`,等同「看不到就當沒違規」的那個規格明文禁止的錯誤行為),跑 `test_slo.py` + `test_side_effects.py` 共 17 個測試全數通過。

查證:`grep -n "missing" tests/ops/test_side_effects.py tests/ops/test_slo.py` 沒有任何命中——整個增量 3 的測試檔沒有任何一處建構「DSP 打不通/回應讀不懂」的情境,`DspUnreadable` 這個例外路徑、`Tally.missing` 欄位、以及 `slo.evaluate()` 裡 `(period.bad > 0 if not period.missing else None)` 這段「資料來源缺時違規欄要回 None 而不是 False」的邏輯全部零覆蓋。這兩條是目標為零的安全性指標(未授權副作用、重複有害副作用),假綠燈的代價是「DSP 完全連不上時系統回報『沒有違規』」,風險等級高。

建議:在 `test_side_effects.py` 補一支測試(關掉 DSP server 或指向不存在的埠),斷言 `unauthorized`/`duplicates` 回 `missing=True` 而非 0 個事件;`test_slo.py` 補一支斷言 `slo.evaluate()` 對缺資料來源的目標為零指標回 `violating is None`。

### 3. 「同一把冪等鍵對應到兩筆以上 DSP 操作」的防禦性核對([S653])沒有任何測試觸發
severity: major
blocking: 是
引句:「if entry.key in seen_keys:  # 同一把鍵第二筆操作」

規格明講([S653] 對應段落):「另外核對『同一把冪等鍵對應到兩筆以上 DSP 操作』:DSP 的冪等鍵有唯一限制,正常路徑不會發生,是防禦性核對。」`src/rtb/ops/side_effects.py:310-313` 正是這段防禦邏輯的實作。我把這三行(`if entry.key in seen_keys / bad_at.append / continue`,只留 `seen_keys.add`)整段拿掉,跑 `test_side_effects.py` 的 5 個測試全數通過。

查證:`grep -n "seen_keys" tests/ops/test_side_effects.py` 沒有任何命中。`tests/ops/test_side_effects.py::test_a_second_commit_of_the_same_proposal_is_a_harmful_duplicate` 用的三把鍵(keys a/b/c)分別提交一次,從未讓「同一把鍵」在 DSP 操作表出現兩筆(這在正常路徑下也確實只能用測試直接寫原始 SQL 繞過 DSP 的 UNIQUE 限制才能模擬,而測試檔案本身已經在用 `conn.execute("INSERT INTO operations ...")` 直接寫列——具備繞過能力卻沒有用它測到這個分支)。

建議:在 `test_side_effects.py` 用直接寫 `operations` 表的方式,讓同一個 `idempotency_key` 出現兩筆委託不同 `operation_id`,斷言第二筆被計成壞事件。

### 4. 端到端交給執行([S655])剛好 2 分鐘的邊界完全沒測
severity: major
blocking: 是
引句:「spent <= END_TO_END_LIMIT.total_seconds()」

計劃書第 295 行明確把這條的邊界寫法類比到「結果不明對帳」那條:「剛好在第 10 分鐘那一刻結案也算好(跟端到端『不超過 2 分鐘』同一種寫法)」——也就是說「剛好 120 秒」應該算好事件。`src/rtb/ops/sli.py:222` 就是這個判斷式。我把 `<=` 改成 `<`(剛好 120 秒會被誤判成壞事件),跑 `test_slo.py` + `test_side_effects.py` 共 17 個測試全數通過。

查證:`tests/ops/test_slo.py::test_end_to_end_handoff_sli_is_event_based` 用的樣本是 100 秒(好)、350−300=50 秒(好)、200 秒(壞),沒有任何一筆卡在 120 秒整。對照同一支測試檔案裡 [S654] 的 `test_deadline_based_slis_are_fixed_once_the_window_has_passed` 明確測了「剛好第 10 分鐘」「剛好第 30 秒」兩個精確邊界(我用同樣手法變異這兩處都被準確咬住),[S655] 少了對稱的邊界樣本,是這個測試檔內部覆蓋不一致的缺口。

建議:在 `test_end_to_end_handoff_sli_is_event_based` 加一筆恰好 120 秒交給執行的任務(扣掉等待後剛好等於上限),斷言算好事件。

### 5.(次要)DSP 翻頁剛好 50 筆與同時間戳疊在一起未被任何測試涵蓋,但功能上未見錯誤
severity: minor
blocking: 否
引句:「OPERATION_PAGE = 50」

我在 `/tmp` 額外直接呼叫 `read_dsp_window`,手動構造兩筆 DSP 操作共用同一個 `committed_at`、分別落在一頁 50 筆的分頁邊界前後與窗口起訖邊界上,驗證現有實作(`src/rtb/dsp/store.py` 的 `operation_cursor`/`operations_after` 與 `src/rtb/ops/side_effects.py` 的 `read_dsp_window`)對這個組合邊界處理正確(兩筆同時間戳的操作都落在該落的窗、翻頁不漏不重)。但 `tests/ops/test_side_effects.py::test_the_dsp_operation_window_reader_is_bounded_and_read_first` 裡所有寫入的 `committed_at` 都是各自遞增的獨立時間戳,没有任何一筆與相鄰筆同時間戳,也沒有讓這個同時間戳恰好落在 `OPERATION_PAGE`(50)分頁邊界上。這屬於「目前行為正確、但沒有回歸測試守住」的缺口(這也是 DSP 提交時間有 `_not_before_last` 墊高機制、正常運作下確有機會出現同時間戳的場景),不是資料錯誤或合約違反,列為 minor。

建議:比照現有測試手法補一筆情境:在第 50/51 筆操作之間插入一組同 `committed_at` 的操作,斷言分頁與窗口切分都正確。

---

## 其餘查證(未發現問題之處,一併記錄)

- **假計數函式與真計數函式的接線**:`test_slo.py` 的燒損數學([S656]-[S661])全部餵 `Planned` 假計數函式,但六條 SLI 的「名稱 → 真正計數函式」這段接線各自有獨立測試用真資料驗證(`test_slo.py` 的 [S651][S654] 用 `sli.count("safe_completion"/"unknown_reconciled_in_time"/"queue_wait", ...)`,`test_side_effects.py` 用 `sli.count("harmful_duplicates"/"unauthorized_side_effects", ...)`,[S655] 用 `sli.count("end_to_end_handoff", ...)`)。我把 `src/rtb/ops/sli.py` 的 `COUNTERS` 字典故意對調 `safe_completion`/`queue_wait` 兩個名稱對應的函式,這個接線錯誤被準確咬住(2 個測試翻紅)。真正沒被咬住的只有「production 那顆 lambda」本身(見發現 1),不是「真計數函式與燒損數學之間」廣泛地測不到。
- **測試竄改輔助函式(`_tamper_snapshot` 多插四個 NULL)**:核對過 `attempts` 表目前總欄位數(基本 16 欄 + `ADDED_COLUMNS` 新增 9 欄 = 25 欄),`tests/executor/test_attempt_store.py` 裡 `_tamper_snapshot` 的 `INSERT INTO attempts VALUES (...)` 精確帶了 25 個值,與新增量 3 四欄(`ratio_allowance, max_budget, aggregate_limit, aggregate_used`)、增量 1 三欄(`source, actor, program_version`)都對得上,是必要的同步調整,沒有放寬或掩蓋既有的毀損列偵測邏輯。
- **唯讀守衛與窗口讀取登記(`test_read_only.py`、`test_window_readers.py`)**:兩支都是機械化清單比對(`mechanical == set(samples)`、`_readers() == set(EXPECTED_INDEX)`),新讀取函式不登記就會直接翻紅,不是人工維護、無法悄悄漏登記;新登記的 `approval_uses_for`/`first_rows_for`/`first_rows_for_proposal`/`unknown_rows_between` 都用真實 `tx` vs `rtx` 資料核對過,不是空殼註冊。
- **邊界掃描白名單(`test_ops_boundaries.py`)**:新加入 `READ_WHITELIST` 的四個名稱(`unknown_rows_between, first_rows_for, approval_uses_for, first_rows_for_proposal`)逐一核對過不在對應模組的 `WRITE_FUNCTIONS` 裡,且該測試本身用 `classify()` 機械驗證白名單裡每個名字在 `SCANNED` 的五支檔案中都只被判定為非寫入函式,沒有夾帶會寫入的函式進白名單。
- **為避開變數名比對而改名**:這個顧慮對應的是計劃書「增量 4」段落裡「拼回後的字串」與既有「拼回字串」函式重名互蓋的問題,增量 4(調查實演、稽核守衛)尚未併入這份 patch,本次 [S650]-[S667] 範圍內沒有發現這類改名。

## 材料
- governance/review-reports/code-phase9-inc3/r1-snapshot-tests.patch
- governance/review-reports/code-phase9-inc3/r1-snapshot-src.patch
- docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase9可觀測與SLO_計劃.md(「增量 3」節,合約 [S650]-[S667])
- 變異實驗:`/private/tmp/claude-501/-Users-enzo/36c33b75-fd17-4bff-aa49-20f97a550e58/scratchpad/p9i3-mut/repo`(複製自 /Users/enzo/rtb-p9i3,未修改原始 repo 任何檔案)
