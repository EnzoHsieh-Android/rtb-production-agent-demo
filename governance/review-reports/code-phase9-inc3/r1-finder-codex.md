severity: major

### 1. 提交時間未正規化，時間游標會漏掉落窗操作
severity: major
blocking: 是
引句:「+            (since.astimezone(UTC).isoformat(),))」
file: `src/rtb/dsp/store.py:78`
file: `src/rtb/dsp/store.py:408`

觸發情境：可注入時鐘先寫 `12:00+00:00`，再寫實際較晚的 `07:30-05:00`（即 `12:30Z`）。`_not_before_last` 用 datetime 正確判斷後者較晚，卻原樣保存帶 `-05:00` 的字串；游標查詢則把起點正規化成 UTC 後用 TEXT 字典序比較。查 `12:15Z` 時，實際位於窗內的第二筆因字串 `07:30...` 小於 `12:15...` 而查不到，游標回到最大操作編號，後續翻頁直接漏筆。這也違反規格對可注入 DSP 時鐘的支持。建議所有 `committed_at` 在寫入前統一轉成固定 UTC 格式，並遷移既有非正規格式資料；索引與查詢必須使用同一種可排序表示。

### 2. 時鐘倒退會把未來才提交的操作塞回已結束窗口
severity: major
blocking: 是
引句:「+        committed_at = self._not_before_last(self._clock())」
file: `src/rtb/dsp/store.py:399`
file: `src/rtb/dsp/store.py:408`

觸發情境：上一筆提交時間是 10:00，系統時鐘倒退至 09:00；10:05 已查完窗口 `[09:55,10:05)` 後，10:10 才發生的新寫入仍會被墊成 10:00。第一次查詢沒有它，稍後重查同一個已過窗口卻會多出它，兩條目標為零的 SLI 甚至可能從正常翻成違規；若它在翻頁到末尾後才提交，同一次評估也會漏掉。這破壞「過去窗口答案不再改變」的事件型合約。建議使用能隨實際經過時間前進的持久化混合邏輯時鐘，或正式引入事件時間 watermark／遲到事件政策；單純複製上一筆時間不足以封閉窗口。

### 3. 期限當刻轉人工後同刻結案會被誤算為準時完成
severity: major
blocking: 是
引句:「+        if row.state == AttemptState.ESCALATED and row.written_at < deadline:」
file: `src/rtb/ops/sli.py:88`
file: `src/rtb/executor/attempt_store.py:815`

觸發情境：一把鍵在 T 進結果不明，T+10 分鐘整轉人工，接著人工處置用同一個時鐘值寫成 VERIFIED 或 FAILED。因為轉人工判斷只用 `< deadline`，該列不會排除或定案；迴圈接著看到同刻的終點列，回傳好事件。規格的實作解讀明定「剛好在期限那一刻轉人工算壞」，因此這條會把壞事件算好。建議遇到 ESCALATED 時分三路：期限前排除、期限當刻立即回傳期限壞事件、期限後停止並保留既定壞事件；後續人工終點不得覆寫結果。

### 4. 破損或對不上鍵的提案快照會被重複副作用指標算成好事件
severity: major
blocking: 是
引句:「+        parsed = parse_proposal(json.loads(row[10] or "null")).proposal」
file: `src/rtb/executor/attempt_store.py:469`
file: `src/rtb/executor/attempt_store.py:974`
file: `src/rtb/ops/side_effects.py:309`

觸發情境：第一列存在，但 `proposal_json` 無法解析，或可解析卻算出的操作鍵不等於該列的鍵。新批量讀取沒有沿用既有 `snapshot()` 的鍵一致性檢查；解析失敗時 `_identity()` 回空，重複副作用計數器直接 `good += 1`。結果是缺少判斷重複所必需的材料反而被當成已證明不重複，可能讓目標為零的違規保持為否。建議批量讀取共用既有快照驗證，包括 `operation_key(proposal) == key`；將破損狀態明確傳給核對器並列為無法核對／資料來源異常，絕不能增加好事件。

### 5. 任一子窗缺資料會遮掉其他子窗已證實的零目標違規
severity: major
blocking: 是
引句:「+            (period.bad > 0 if not period.missing else None) if spec.zero_target else None,」
file: `src/rtb/ops/slo.py:140`
file: `src/rtb/ops/slo.py:196`

觸發情境：正式縮放倍數改成 1，30 天週期切成 30 個子窗；其中一窗已有壞事件，另一窗因 DSP 暫時讀不到而 `missing=True`。合併結果仍保留 `period_bad > 0` 與 `last_bad`，但 `violating` 被強制改成空值。目標為零是存在性判斷，已看見一個壞事件就足以確定違規，其他窗缺資料只能讓「是否還有更多」未知，不能推翻已知違規。建議改成 `True if period.bad > 0 else None if period.missing else False`，並繼續獨立回報 `missing`。

### 6. 核可使用批量查詢會反覆全表掃描，成本隨兩側資料量相乘
severity: major
blocking: 是
引句:「+                    f"SELECT key, stage FROM approval_uses WHERE key IN ({marks})", chunk):  # noqa: S608 - 只拼佔位符」
file: `src/rtb/executor/inbox_store.py:228`
file: `src/rtb/executor/inbox_store.py:247`
file: `src/rtb/executor/inbox_store.py:742`

觸發情境：窗口內有 W 把執行端鍵、歷史核可使用表有 A 列。表只有 `(tenant, at)` 與 `(at)` 索引，沒有以 `key` 開頭的索引；函式每 500 把鍵執行一次 `WHERE key IN (...)`，每批都掃 A 列，成本約為 `ceil(W/500) × A`。正式 30 天週期還會逐日重跑，歷史增長後唯讀評估器會超時或長時間佔用資源。建議新增 `approval_uses(key, stage)` 索引；若既有統計查詢需要固定舊索引，應在那支查詢明確指定或重寫，而不是讓新的熱路徑永久全掃。

### 7. 十九位游標仍可能超過 SQLite 整數上限並回 500
severity: major
blocking: 是
引句:「+        if not cursor.isdigit() or len(cursor) > MAX_CURSOR_DIGITS:」
file: `src/rtb/dsp/server.py:81`
file: `src/rtb/dsp/server.py:171`

觸發情境：請求 `/operations/after/9999999999999999999`。它恰好十九位而通過檢查，但大於 SQLite 有號整數上限 `9223372036854775807`；綁定查詢參數時會拋出 `OverflowError`，最後成為 500，而不是穩定的 `invalid_cursor` 400。建議轉成整數後再驗證 `0 <= cursor <= 2**63 - 1`，並把轉換及 SQLite 溢位統一映射成 400。