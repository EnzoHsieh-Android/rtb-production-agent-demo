severity: major

# 資安審查報告(增量 3 第 1 輪修正覆核)

範圍:`governance/review-reports/code-phase9-inc3/r2-delta-src.patch`,對照 `r1-資安-sonnet.md` 的 4 條發現逐一覆核,並檢查修正本身有沒有帶出新的可利用面。

## 結論摘要

r1 的 4 條(committed_at 解析炸評估器、duplicates() 第二段沒接例外、/operations/after 無憑證跨租戶列舉、cursor 驗證漏 isdecimal/上限)在這版修正裡都**真的封住了**,而且封的位置正確(在 `read_dsp_window`/`_write`/`duplicates`/`_get_operations_after` 這幾個直接處理不可信輸入的地方擋下,不是只靠外層兜底),也都有對應的白箱測試覆蓋(`tests/ops/test_side_effects.py::test_a_malformed_commit_time_from_the_dsp_is_unreadable_not_a_crash`、`test_a_failed_lookup_of_a_sibling_key_makes_duplicates_missing`、`test_the_operation_list_endpoints_require_the_audit_key`、`test_a_cursor_must_be_a_plain_decimal_within_the_integer_range`)。唯讀稽核金鑰本身的比對是固定時間(`hmac.compare_digest`)、金鑰太短或沒設一律當「沒設」(503)拒收、跟寫入憑證是不同的環境變數與不同的位元組值,審過沒有找到能把寫入憑證跟稽核金鑰兩個標頭混用來繞過的路徑,錯誤回應也只回代碼字串、不回金鑰內容或長度提示。

但這版修正把「DSP 讀不到就整支評估器當掉」改成「這一條指標標記讀不到、其他照算」的同時,順手把**結束代碼的意義也弱化了**——而稽核金鑰正是這版新加的、會讓「讀不到」變得更容易發生的機制。這條在下面詳列。

---

### 1. 兩條「目標為零」指標讀不到 DSP(含稽核金鑰沒設、設錯,或惡意 DSP 故意讓這兩支端點失敗)時,命令列入口的結束代碼照樣是 0,监控腳本看不出稽核其實沒跑到

severity: major
blocking: 是

引句:「if not all(s.stable for s in statuses):」

觸發情境:`RTB_DSP_AUDIT_KEY` 沒設定、設錯,或(更值得注意的)一個惡意/被滲透的 DSP——`side_effects.py` 模組說明自己列的「DSP 回應是不可信輸入」——在自己剛好做了未授權寫入或重複提交的同一個稽核窗裡,故意讓 `/operations/since` 或 `/operations/after` 回非 200、格式不對、或 `committed_at` 讀不懂。這些情況經這輪修正後都會被 `read_dsp_window`/`_write`/`commit_moment` 轉成 `DspUnreadable`,`unauthorized()`/`duplicates()`(`src/rtb/ops/side_effects.py:277-311`)回 `Tally(0, 0, missing=True)`,`_violating()`(`src/rtb/ops/slo.py:206-210`)因為 `period.bad == 0` 又 `period.missing` 為真,回傳 `None`(不知道,不是「沒有違規」)。

會出什麼錯的行為:`run()`(`src/rtb/ops/slo.py:267-292`)印完 JSON 之後,只用 `if not all(s.stable for s in statuses):` 這一行判斷要不要回非 0 的 `EXIT_UNSTABLE`,完全沒有檢查 `missing`/`error` 欄位;`evaluate()` 裡新加的 `except Exception as exc:`(`src/rtb/ops/slo.py:237`)把單一指標算到一半的例外轉成 `SloStatus(..., error=..., missing=True)` 後,同樣不影響結束代碼。也就是說:「未授權副作用」「重複有害副作用」這兩條唯一目的就是抓 DSP 端惡意寫入的指標,只要讓它們讀不到 DSP(不管是稽核金鑰沒對好,還是 DSP 自己搞壞回應),`slo.run()` 的行程結束代碼仍然是 `EXIT_OK`(0)。`tests/ops/test_slo.py:382-386` 的 `_printed()` 輔助函式本身就斷言「不管環境變數帶了什麼,`slo.run(...)` 一定等於 `EXIT_OK`」;`tests/ops/test_slo.py:389-400` 的 `test_the_command_line_reports_fixed_exit_codes` 用這支輔助函式在**沒帶稽核金鑰**時呼叫(第 399 行:`printed = _printed(rows, dsp_url, {})`),斷言 `printed["unauthorized_side_effects"]["missing"] is True`(第 400 行)——這就是「稽核明確沒讀到、指標明確標記缺資料,但入口回的還是成功結束代碼」的第一手證據。任何只看行程結束代碼決定要不要告警的維運腳本(這正是 `EXIT_NO_DATABASE`/`EXIT_NOT_UPGRADED`/`EXIT_UNSTABLE` 這組固定代碼設計出來要給的用法,`src/rtb/ops/slo.py` module docstring:「只有找不到資料庫檔、資料庫還沒升級這兩種整份評估的設定錯誤往外丟,給命令列入口回固定結束代碼」)完全看不出這一次窗口的未授權/重複寫入根本沒被稽核到——而這正好是攻擊者(掌控或滲透 DSP 的一方)最有動機去觸發的狀態:讓稽核端在自己做壞事的窗口裡「安靜地」讀不到資料,而不是被抓到「違規」。

值得指出:這個結束代碼的落差不是這輪修正憑空造出來的全新程式路徑,而是這輪修正**新加的稽核金鑰機制**把「讀不到」的觸發面從「純網路/格式異常」擴大成「金鑰設定或 DSP 端惡意行為都能觸發」,卻沒有同步把 `missing`/`error` 接進結束代碼的判斷裡,讓 finding 3 的修法(要求稽核金鑰)在「防止跨租戶批次列舉」這件事上是對的,但同時放大了「稽核失效可以被靜默壓過去」這個既有缺口的可觸發程度,而且正好是命中兩條目標為零指標本身。

建議修法:至少在 `run()` 裡比照 `EXIT_UNSTABLE` 的作法,新增一個固定結束代碼(例如 `EXIT_INCOMPLETE`),當 `any(s.missing or s.error is not None for s in statuses)`(尤其是 `unauthorized_side_effects`/`harmful_duplicates` 這兩條)為真時回傳它,讓仰賴結束代碼的維運或告警管線至少能分辨「這次稽核真的跑完、兩條為零指標乾淨」跟「這次稽核有一段時間根本沒讀到 DSP」。

---

## 逐項覆核 r1 的 4 條(已封住,列出證據)

- **finding 1(committed_at 解析崩潰整支評估器)**:`_write()` 在建好 `DspWrite` 之後立刻呼叫 `commit_moment(entry["committed_at"])`(`src/rtb/ops/side_effects.py:222-231`),型別不對、格式解不開、沒帶時區一律轉 `DspUnreadable`,不會再讓裸的 `ValueError`/`TypeError` 穿出 `read_dsp_window`。實測見 `tests/ops/test_side_effects.py:301-309`:餵 `"yesterday"`、無時區字串、整數 `12` 三種壞值,`read_dsp_window` 丟 `DspUnreadable`,而 `sli.count("unauthorized_side_effects"/"harmful_duplicates", ...)` 回 `missing=True, valid=0`,沒有例外往上穿。
- **finding 2(duplicates() 第二段沒接例外)**:`duplicates()` 現在把 `read_dsp_window` 到 `_tally_duplicates`(內含逐鍵呼叫 `_operation()`)整段包進同一個 `try/except DspUnreadable`(`src/rtb/ops/side_effects.py:298-311`)。實測見 `tests/ops/test_side_effects.py:312-322`:讓第二段逐鍵查詢回 503 或 `committed_at` 是 `"garbage"`,兩種都讓 `harmful_duplicates` 回 `missing=True, valid=0`,不是例外或誤判成好事件。
- **finding 3(/operations/after 無憑證跨租戶列舉)**:`_get_operation_cursor`/`_get_operations_after` 都在最前面呼叫 `_require_audit_key()`(`src/rtb/dsp/server.py:187,202`),沒金鑰配置回 503、沒帶標頭回 401、帶錯回 403,比對用 `hmac.compare_digest` 固定時間;既有的 `/operations/<key>` 端點不需要金鑰但也确认不回 `tenant` 欄位(`OperationResult` 資料類沒有 `tenant` 欄位,`src/rtb/dsp/store.py:119-128`),所以沒有變成新的跨租戶外洩面。實測見 `tests/ops/test_side_effects.py:409-428`:401/403/200/404/503 五種狀態都覆蓋到,而且明確驗過「拿長度不對的字串」「拿 AUDIT+多一個字元」都拒收,沒有找到用寫入憑證的 `X-Capability` 值去打這兩支端點會被接受的路徑(比對的是稽核金鑰原始位元組,跟簽章驗證完全獨立)。
- **finding 4(cursor 只驗 isdigit() 驗不住 int()/SQLite 綁定例外)**:改成 `cursor.isdecimal()` 加轉 `int` 後比 `SQLITE_INTEGER_MAX`(`src/rtb/dsp/server.py:203-208`)。實測見 `tests/ops/test_side_effects.py:441-449`:上標 `²`(位元組 `\xb2`)回 400 而不是 500,`"9999999999999999999"`、`"9223372036854775808"`(剛好超界 1)、`"-1"`、`"1e3"` 都回 400 `invalid_cursor`,邊界值 `"9223372036854775807"`(SQLite INTEGER 上限)正常回 200。

另外依題目指定查過的「提交時間墊高」:`commit_text()`(`src/rtb/dsp/store.py:250-256`)把所有寫入路徑統一換算成 UTC 的固定寫法後才落地,`_not_before_last()` 用它取代直接回傳原始讀數,修掉了「時鐘讀數帶非 UTC 偏移時字串序跟時間序對不上」這個潛在資料完整性問題;`committed_at` 仍然全程只來自伺服器 `_clock()`,沒有找到 HTTP 路徑、標頭或提案內容能寫進這個值的路徑,維持 r1 的「clean」結論。
