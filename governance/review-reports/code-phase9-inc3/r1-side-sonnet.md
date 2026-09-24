severity: major

### 1. DSP 舊操作缺 expected_version 時,授權範圍核對誤判成「已證實違規」而非「無法核對」
severity: major
blocking: 是
引句:「if entry.expected_version != proposal.campaign_version_observed:」

`expected_version` 跟 `policy_version` 是同一次資料庫遷移加進 DSP `operations` 表的欄位(`src/rtb/dsp/store.py:259`:`{"tenant", "name"} <= columns and {"policy_version", "expected_version"} <= op_columns`),遷移前的舊操作兩者皆補空值(`store.py` 註解「舊操作沒有記,誠實留空值」)。但 `_scope_violations`(`src/rtb/ops/side_effects.py:107`)對兩者的空值處理不一致:`policy_version` 有明確的空值分支(`side_effects.py:112-115`:先判 `is None` 才歸類 `missing`,否則才比對),`expected_version` 卻直接 `entry.expected_version != proposal.campaign_version_observed`,完全沒有 `is None` 判斷。

觸發情境:一筆執行端格式(`k1-...`)的 DSP 舊操作,是在 `expected_version` 欄位還不存在時寫入的(遷移補值為 `NULL`),其餘欄位(廣告、動作、租戶、政策版本)都跟第一列提案吻合。因為 `proposal.campaign_version_observed` 一定是整數,`None != <整數>` 恆為真,`check_write` 會把它判成 `Verdict.BAD`、理由 `expected_version`,而不是 `Verdict.UNVERIFIABLE`(無法核對)。

實測驗證(用材料裡完全相同的判斷邏輯,分別餵 `expected_version=None` 與 `policy_version=None`,其餘資料一致):
```
verdict: bad reasons: ('expected_version',)
policy_version=None -> (<Verdict.UNVERIFIABLE: 'unverifiable'>, ('policy_version',))
```
同一種「遷移前欄位缺值」的情況,一個判壞、一個判無法核對,不對稱。

影響:`unauthorized_side_effects` 是目標為零的服務水準指標,週期內只要出現一個壞事件就整個週期判「違規」直到它滑出週期([S661])。任何跨過這次遷移仍在窗內的舊 DSP 操作,只要恰好是合法寫入,也會被誤判成「未授權副作用」,觸發假警報,且違反規格「無法核對:...第一列沒有這個增量新加的四欄、或 DSP 那一筆沒記政策版本」這一類「缺材料判無法核對」的設計意圖(`expected_version` 跟 `policy_version` 缺值的性質完全相同)。

建議修法:比照 `policy_version` 的寫法,在 `_scope_violations` 對 `entry.expected_version is None` 另外歸類進 `missing`,只有非空時才比對是否相符。

file: `src/rtb/ops/side_effects.py:107`

### 2. 重複有害副作用核對時,DSP 中途讀不到會讓整個燒損評估器崩潰,而不是照規格回「資料來源缺」
severity: major
blocking: 是
引句:「found = [_operation(dsp_url, key, timeout) for key in siblings.get(ident, [])」

`duplicates()`(`src/rtb/ops/side_effects.py:270-292`)只用 `try/except DspUnreadable` 包住第一段的 `read_dsp_window`(讀窗內寫入);讀到窗之後才呼叫的 `_tally_duplicates`(`side_effects.py:295-326`)在窗外用 `_operation(dsp_url, key, timeout)` 逐一查每個「同一份提案」的其他鍵在 DSP 的提交時間(`side_effects.py:318`),這段呼叫完全沒有被任何 `except DspUnreadable` 包住。`_operation` 一旦連不上 DSP 或讀到非預期狀態碼,會拋出 `DspUnreadable`(`side_effects.py:236-243`、`187-192`),這個例外會一路往上炸穿 `duplicates()`、`sli.harmful_duplicates()`、`slo.period_tally()`/`slo._alerting()`,直到 `slo.evaluate()`——而 `evaluate()` 對六條指標是同一個迴圈跑完才回傳,沒有逐條隔離例外,也不在 `except` 清單裡([S652]/[S665] 規格與 `slo.run()` 只接 `FileNotFoundError`、`DatabaseNotUpgraded`)。

觸發情境:窗內有一份提案存在多把鍵(規格明講的常見案例:「鍵算法改過版」),核對其中一把鍵是不是重複時,需要另外打 DSP 查詢其他鍵各自的提交時間;若這次查詢剛好遇上 DSP 逾時、斷線或回應格式異常(即使第一次讀窗成功),就會拋出未接住的例外。

實測重現(讓 `/operations/since|after` 正常、只讓單鍵查詢端點 `/operations/<key>` 失敗):
```
CRASHED with uncaught DspUnreadable: simulated DSP outage for single-key lookup
```
既有測試(`tests/ops/test_side_effects.py`)全數通過,顯示這個路徑目前完全沒有測試覆蓋。

影響:違反規格「DSP 讀不到時,兩條目標為零的指標回『資料來源缺』、違規欄為空,不當成 0 個事件」的設計(`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase9可觀測與SLO_計劃.md` 增量 3 節);而且因為 `evaluate()` 沒有逐條隔離,這一條指標讀不到會讓整個 `lumos`/命令列燒損報告(其餘四條正常指標)全部拿不到結果,而不是像規格期望的那樣只有這一條回「missing」。

建議修法:`_tally_duplicates` 內對 `_operation` 的呼叫(或包住它的迴圈)補上 `except DspUnreadable`,讓整批往回查失敗時回傳 `Tally(0, 0, missing=True)`,跟 `read_dsp_window` 失敗時的處理一致;或在 `evaluate()` 層對每條指標的計算做例外隔離。

file: `src/rtb/ops/side_effects.py:318`

### 3. 游標長度上限沒有真正卡住 SQLite 整數範圍,超界游標回 500 而非設計的 400
severity: minor
blocking: 否
引句:「MAX_CURSOR_DIGITS = 19  # 游標是 SQLite 整數(最大 19 位數);更長的不收」

`_get_operations_after`(`src/rtb/dsp/server.py:171-178`)只檢查游標字串是否全為數字、且長度不超過 19 位(`server.py:175`),但 19 位數字的範圍是 0~9999999999999999999,遠大於 SQLite/Python `sqlite3` 綁定整數的上限 `2**63-1`(9223372036854775807,同檔 `SQLITE_INTEGER_MAX` 已定義於 `src/rtb/dsp/store.py:52`)。當呼叫端(或探測者)傳入一個恰好 19 位、但數值超過 `2**63-1` 的游標時,`int(cursor)` 轉換成功,交給 `store.operations_after(int(cursor))` 綁定 SQL 參數時會拋出 `OverflowError`,不是預期的 `RequestRejected(400, "invalid_cursor")`,而是被 `JsonHandler` 的例外保底吃成 `500 internal_error`(`src/rtb/httpkit.py:158-175`)。

實測驗證:
```
OverflowError: Python int too large to convert to SQLite INTEGER
```
（用等價的 `sqlite3` 綁定操作重現,行為與 `MAX_CURSOR_DIGITS` 檢查放行的輸入一致。）

這不會造成連線中斷或資料錯誤(伺服器本身有「絕不無聲切斷連線」的保底,仍回應合法 JSON),只是把本該辨識成「輸入不合法」的請求誤判成「內部錯誤」,不影響核對正確性,故列為 minor。

建議修法:把長度檢查換成直接比較數值上限(`int(cursor) <= SQLITE_INTEGER_MAX`),或在轉換時包 `try/except (OverflowError, ValueError)` 轉成 `RequestRejected(400, "invalid_cursor")`。

file: `src/rtb/dsp/server.py:175`、`src/rtb/dsp/store.py:52`
