severity: major
# 資安審查報告

severity: major

### 1. 惡意或異常的 DSP 回應可讓副作用核對整支評估器當掉,六條服務水準指標全部停擺
severity: major
blocking: 是
引句:「if datetime.fromisoformat(entry.committed_at) >= until:」

觸發情境:DSP 回應是明確列在不可信清單裡的輸入。`/operations/since/<t>` 或 `/operations/after/<cursor>` 回應裡任何一筆操作的 `committed_at` 不是合法 ISO 格式字串(壞掉的第三方 DSP、或有能力偽造 GET 回應的一方),`read_dsp_window()` 對每一筆都直接呼叫 `datetime.fromisoformat(entry.committed_at)`(`src/rtb/ops/side_effects.py:223,225`),沒有任何 try/except。`_write()`(`side_effects.py:195-204`)只驗 `operation_id`/`idempotency_key` 等欄位型別,完全沒驗 `committed_at` 是否為合法時間,所以壞掉的時間字串會順利包成 `DspWrite` 一路傳到這裡才炸。

會出什麼錯的行為:拋出的 `ValueError` 不是 `DspUnreadable`,`unauthorized()`(`side_effects.py:247-252`)與 `duplicates()`(`side_effects.py:270-279`)都只 `except DspUnreadable`,接不住它,一路往上穿過 `sli.count()`、`slo.evaluate()`,而 `slo.run()`(`src/rtb/ops/slo.py:238-256`)只捕捉 `FileNotFoundError` 與 `DatabaseNotUpgraded`(第 247、250 行),於是整支評估器行程直接以未捕捉例外崩潰、連 JSON 都印不出來——不只「未授權副作用」「重複有害寫入」這兩條目標為零的告警瞎眼,連同一次呼叫裡的其餘四條 SLI(安全完成率、對帳時效、端到端交付、佇列等待)也一起拿不到數字。等於被稽核的一方(DSP)自己就能讓稽核機制罷工。

建議修法:在 `_write()` 內就用同一支 `datetime.fromisoformat` 驗過 `committed_at`,失敗就併入既有的 `except (KeyError, TypeError, ValueError)` 一起轉成 `DspUnreadable`;或至少把 `read_dsp_window()` 裡這兩處裸呼叫包一層 try/except 轉成 `DspUnreadable`,讓上層既有的 `missing=True` 邏輯真正接得住。

### 2. duplicates() 的第二階段呼叫 DSP 完全沒接例外,同一個洞可以繞過第一層修補再炸一次
severity: major
blocking: 是
引句:「return _tally_duplicates(writes, firsts, siblings, dsp_url, timeout)」

觸發情境:`duplicates()` 只把 `try/except DspUnreadable` 包在第一次 `read_dsp_window()` 呼叫外面(`side_effects.py:275-279`),但緊接著在 try 區塊外呼叫的 `_tally_duplicates()`(第 292 行)內部,對每一份提案的手足鍵呼叫 `_operation()`(`side_effects.py:236-243`)——打的是既有唯讀端點 `/operations/<key>`。這個呼叫既可能因端點回非 200 而丟 `DspUnreadable`(第 242 行),也可能因為 `body["committed_at"]` 格式不對讓 `iso(str(body["committed_at"]))` 丟 `ValueError`(第 243 行)。

會出什麼錯的行為:兩種例外在 `duplicates()` 裡完全沒有任何 except 涵蓋(連 `DspUnreadable` 都沒接,不只是 `ValueError`),同樣一路往上把整支評估器炸穿。也就是說即使把發現 1 的 `read_dsp_window` 修好,`duplicates()` 這條「重複有害寫入」SLI 仍有第二條完全裸露的路徑,只要在稽核視窗內恰好有一次 DSP 逾時、瞬斷或壞回應就能複製同樣的崩潰。

建議修法:把 `try/except DspUnreadable`(建議也接住第 1 點會用到的解析例外)的涵蓋範圍擴大到整個 `duplicates()` 函式主體,包住 `_tally_duplicates()` 內對 DSP 的所有呼叫,而不是只包最前面那一次 `read_dsp_window`。

### 3. 新端點 /operations/after 不驗身分也不分租戶,任何本機呼叫可批次撈出全部租戶的操作紀錄
severity: major
blocking: 是
引句:「"tenant": r[3], "action": r[4],」

觸發情境:`/operations/after/<cursor>`(`src/rtb/dsp/server.py:171-178`)跟寫入端點不同,`_get_operations_after` 完全沒有呼叫 `verified_claims`/`check_scope` 這類憑證檢查(對照 `_authorized_write` 在第 198 行就先驗憑證)。`store.py` 的 `operations_after()`(`src/rtb/dsp/store.py:429-444`)用 `LEFT JOIN campaigns` 把每一筆操作連同 `tenant` 欄位一起撈出,SQL 裡沒有任何 `WHERE tenant = ?` 之類的限制。

會出什麼錯的行為:任何連得到這個埠的呼叫端(只綁 loopback,但同機任何行程都連得到,且這支端點本身就是設計給稽核工具用不帶憑證打的)從 `cursor=0` 開始,每次翻 50 筆一路往下翻,就能把系統裡「所有」租戶的 `campaign_id`、租戶名稱、動作、預算異動金額、`policy_version`、冪等鍵全部撈光。這是既有唯讀端點(`get_campaign`/`get_history`/`get_operation` 都得先知道特定 `campaign_id` 或冪等鍵才查得到,而且都不回 `tenant` 欄位)做不到的「批次跨租戶列舉」能力,是這次增量新引入的資料外洩面,直接命中題目問的「會不會洩漏別的租戶的操作」。

建議修法:比照既有寫入端點的作法,這兩支新端點至少要求持有對應範圍(租戶或稽核專用)的憑證才開放讀取;若設計上必須讓稽核工具免憑證存取,也應該用一把只讀、僅供稽核用的憑證區隔,而不是完全公開、且回傳跨租戶明細。

### 4. cursor 路徑參數只驗 isdigit(),驗不住 int()/SQLite 綁定會丟的例外
severity: minor
blocking: 否
引句:「if not cursor.isdigit() or len(cursor) > MAX_CURSOR_DIGITS:」

觸發情境:送 `GET /operations/after/²`(上標 2,`U+00B2`)這種「`str.isdigit()` 為真但不是十進位數字」的字元,或送 19 位純十進位數字但數值超過 SQLite INTEGER 上限(`2**63-1` 恰好是 19 位數,19 位數不保證不超過,如 `9999999999999999999`)。前者讓 `int(cursor)`(`server.py:177`)丟 `ValueError`,後者讓 `store.operations_after(int(cursor))` 內部 sqlite3 綁定參數時丟 `OverflowError`。

會出什麼錯的行為:兩者都不在 `_get_operations_after` 的任何 try/except 範圍內,會被外層 `_dispatch` 的萬用例外處理接住變成 500 `internal_error`(而不是設計要給的 400 `invalid_cursor`),並把 traceback 印到伺服器 stderr。由於外層一律回 JSON、不會斷線,實際影響有限,純粹是這行驗證沒做到註解宣稱的效果(`MAX_CURSOR_DIGITS` 註解寫「更長的不收」,但沒說「更大的不收」),讓任何呼叫端都能隨手觸發雜訊 500。

建議修法:把 `cursor.isdigit()` 換成 `cursor.isdecimal()`(排除上標/圈碼數字等 `isdigit()` 收但 `int()` 不吃的字元),並在轉成 `int` 之後、綁進 SQL 之前額外檢查不超過 `SQLITE_INTEGER_MAX`。

---

另外依題目指定逐項查過、**未發現可被利用的洞**:
- **提交時間墊高**:`committed_at` 全程只來自 `CampaignStore._clock()`(預設 `_utc_now`,即系統真實時間),`_not_before_last()`(`src/rtb/dsp/store.py:215-222`)只取「時鐘讀數」與「上一筆 `committed_at`」兩者較大值,沒有任何 HTTP 路徑參數、提案內容或命令列參數能寫進這個值;找不到攻擊者能注入未來時間的路徑。
- **設定表載入 / 燒損告警輸出**:`slo.py` 的 `SLOS`/`BURNS` 是寫死在程式碼裡的常數表,不是從外部檔案或請求解析;告警輸出(`to_primitives`/`json.dumps`)只含計數、`Fraction`轉出的浮點數與時間字串,不含提案自由文字或密鑰,沒有注入或機密外洩風險。所有動態拼接的 SQL(`first_rows_for`、`approval_uses_for` 等帶 `noqa: S608` 的 f-string)都只是拼「等長的 `?` 佔位符數量」,實際值仍走參數化綁定,未發現 SQL injection。
