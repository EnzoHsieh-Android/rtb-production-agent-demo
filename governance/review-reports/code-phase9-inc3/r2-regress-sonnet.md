severity: major
# code-phase9-inc3 第 2 輪驗收報告(r2,2026-09-24)

severity: major

## 逐條驗收(讀碼,不只看測試綠;已修的不留 severity 字樣)

**spec-1(端到端交給執行三輪不穩定沒標)**:已修。`src/rtb/ops/sli.py:233-245` 的 `end_to_end_handoff` 現在三輪相同才 `stable=True`、跑滿 `MAX_ROUNDS` 仍不同時 `stable=False`;`Tally`(`src/rtb/ops/side_effects.py:80`)、`SloStatus`(`src/rtb/ops/slo.py:191`)都加了 `stable` 欄並沿週期統計往上傳(`slo.py:153` 的 `period_tally` 做 `all(p.stable for p in parts)`)。command line 入口在 `slo.py:289-292` 會檢查 `all(s.stable for s in statuses)`,不穩定回 `EXIT_UNSTABLE`(5)。測試 `tests/ops/test_slo.py::test_an_unstable_cross_database_read_is_reported` 從 `sli.count` 一路驗到 `slo.run()` 回 5,涵蓋完整。

**arch-1(副作用核對單輪讀取沒有穩定旗標)**:已修,採「合併進 Tally 加穩定欄,理由寫進說明」的折衷。`src/rtb/ops/side_effects.py:12-15` 把因果順序論證寫清楚(DSP 窗一定在執行端第一列之後讀,重讀答案不變),`Tally.stable` 預設 `True` 且 `unauthorized()`/`duplicates()` 從未覆寫這個欄位。我核對過這個因果論證在 `duplicates()` 第二段(對手足鍵逐一查 `_operation`)仍然成立:手足鍵的提交時間是既定歷史事實,重跑同一個過去窗不會因為之後才發生的新提交而改變「有沒有更早的提交」這個判斷,沒有找到反例。

**spec-2 / x1-5(子窗缺資料把已證實的違規蓋成不知道)**:已修。`src/rtb/ops/slo.py:206-211` 的 `_violating()` 先看 `period.bad > 0`(已證實優先),只有 `bad == 0` 且 `missing` 才回 `None`。`tests/ops/test_slo.py` 新增的斷言(`partly`/`blind` 兩組)覆蓋了「有壞事件+缺資料」與「無壞事件+缺資料」兩種組合。

**資安-1(committed_at 裸呼叫 fromisoformat)**:已修。新函式 `commit_moment()`(`src/rtb/ops/side_effects.py:207-215`)把 `ValueError` 與「沒帶時區」都轉成 `DspUnreadable`;`_write()`(`side_effects.py:218-229`)在建構 `DspWrite` 之後立刻呼叫它驗過一次,`read_dsp_window()` 裡後續的 `datetime.fromisoformat(entry.committed_at)`(`side_effects.py:249,251`)因此保證不會再炸。測試 `test_a_malformed_commit_time_from_the_dsp_is_unreadable_not_a_crash` 餵了非法字串、無時區字串、整數三種輸入都驗到 `DspUnreadable`/`missing=True`。

**資安-2 / side-2(duplicates 第二段呼叫 DSP 沒接例外)**:已修。`_tally_duplicates()` 現在被搬進 `duplicates()` 的 `try` 區塊內執行(`side_effects.py:305-311`),`_operation()` 內部丟出的 `DspUnreadable`(含 `commit_moment` 驗提交時間失敗的情形)會被同一個 `except DspUnreadable` 接住回 `missing=True`。測試 `test_a_failed_lookup_of_a_sibling_key_makes_duplicates_missing` 分別餵「503」與「committed_at 讀不懂」兩種手足鍵查詢失敗都驗到 `missing=True`。

**資安-3(兩支新端點不驗身分)**:已修(代使用者裁定的方案:另開唯讀稽核金鑰,不沿用寫入憑證)。`_require_audit_key()`(`src/rtb/dsp/server.py:166-181`)在 `_get_operation_cursor`/`_get_operations_after` 一開頭就擋,沒設金鑰回 503、沒帶標頭回 401、帶錯回 403(`hmac.compare_digest` 固定時間比較),既有依鍵/依廣告端點不受影響。測試 `test_the_operation_list_endpoints_require_the_audit_key` 涵蓋四種狀態,且驗了「沒設稽核金鑰的裸伺服器」一律拒收。

**資安-4 / x1-7 / side-3(cursor isdigit 收不住 SQLite 整數上限)**:已修。`isdigit()` 換成 `isdecimal()`(排除上標/圈碼數字),轉整數後再比 `SQLITE_INTEGER_MAX`(`server.py:203-207`)。測試 `test_a_cursor_must_be_a_plain_decimal_within_the_integer_range` 實際送了上標 2 的原始位元組、19 位超界值、負號、科學記號,全部驗到 400 `invalid_cursor`,`2**63-1` 剛好放行。

**side-1(expected_version 為空直接比較判壞)**:已修。`_scope_violations()`(`src/rtb/ops/side_effects.py:100-125`)把 `expected_version`/`tenant`/`policy_version` 三項改成同一個迴圈,任一邊為空歸 `missing`,兩邊都有才比對。測試新增 `verdict(write(expected_version=None)) is U` 與帶 `campaign_id="c2"` 的對照組。

**x1-1(提交時間非 UTC 正規化,游標查詢字串比較失真)**:已修。新函式 `commit_text()`(`src/rtb/dsp/store.py:78-84`)把提交時間統一轉成 UTC isoformat 再存、再查(`operation_cursor_query`、`_not_before_last` 都改呼叫它)。我驗過它宣稱的「省略微秒的寫法用 `+` 開頭、帶微秒的用 `.` 開頭,`+` < `.` 剛好也是時間序」這個排序論證是對的(用 Python 實際跑過 `isoformat()` 輸出確認)。至於「既有資料不用搬」的前提——我查過 `CampaignStore` 的預設時鐘 `_utc_now()`(`store.py:69-70`)一直回傳 UTC isoformat,唯一能注入非 UTC 偏移的路徑是 `CampaignStore(..., clock=...)` 這個建構參數,而 production 進入點(`server.py` 的 `main()`)從未傳自訂 `clock`,只有測試會注入;所以這個前提在目前程式裡成立,不影響既有正式資料庫。測試 `test_dsp_commit_times_are_stored_in_one_utc_form` 用 `-05:00` 偏移注入驗證游標查詢正確跨過。

**x1-3(期限當刻轉人工誤判成好事件)**:已修。`_reconciled()`(`src/rtb/ops/sli.py:91-108`)把 `ESCALATED` 拆成「期限前排除」與「期限當刻或之後回壞、記在期限那一刻」兩支,且迴圈用 `break` 保證之後同刻或更晚的結案不會覆寫。測試新增 `kE`(期限當刻轉人工、同刻又結案)驗證仍判壞。

**x1-4(批量讀取沒沿用快照鍵一致性檢查)**:已修。`_parsed_snapshot()`/`_snapshot_matches_key()`(`src/rtb/executor/attempt_store.py:469-497`)被 `snapshot()` 與批量讀的 `_first_row()` 共用;`FirstRow` 多了 `snapshot_matches_key` 欄(`attempt_store.py:978`)。`side_effects.py` 的 `_tally_duplicates()`/`_scope_violations()` 都改吃這個旗標,快照對不上鍵時絕不算好事件(細節見下方 [S653] 討論)。測試 `test_a_first_row_whose_snapshot_does_not_match_its_key_is_never_good` 與 `test_the_same_key_committed_twice_is_a_harmful_duplicate` 分別驗證。

**x1-6(核可使用批量查詢每批全表掃)**:已修。新增索引 `approval_uses_by_key`(`src/rtb/executor/inbox_store.py:249`),`approval_uses_for_query()`(`inbox_store.py:1602-1608`)取代原本手刻的 IN 子句。實測見下方 EXPLAIN QUERY PLAN 小節,確認沒有動到 Phase 6 釘住的查詢計畫。

**tests-1~5**:五個測試殺傷力缺口都已補齊——`test_the_command_line_reports_fixed_exit_codes`(命令列入口真跑一次)、`test_the_dsp_being_unreachable_is_missing_not_zero`(DSP 讀不到回 `missing` 不是 0)、`test_the_same_key_committed_twice_is_a_harmful_duplicate`(一鍵兩筆)、`test_end_to_end_handoff_sli_is_event_based` 新增 `e1`(剛好 120 秒判好)、`test_a_page_boundary_between_equal_commit_times_reads_each_operation_once`(翻頁邊界疊同時間戳)。我用材料裡同樣的變異手法(改鬆各關鍵行)重新跑過這五支測試,全部準確咬住。

x1-2 依指示未重驗(已駁回)。

## 回歸排查(逐項交代)

**evaluate 逐條隔離例外會不會吞掉程式錯誤——會,是這輪新引入的缺口**

### 1. `slo.evaluate()` 的逐條例外隔離會把真正的程式錯誤跟「資料來源缺」混為一談,命令列入口不反映這個狀態
severity: major
blocking: 是
引句:「        except Exception as exc:  # 一條讀不到不拖垮另外五條;例外記在這一條的狀態裡」
引句:「    if not all(s.stable for s in statuses):」

觸發情境:六條指標任一條在計算過程中丟出「非 `FileNotFoundError`/`DatabaseNotUpgraded`」的例外——這不只包含設計要接住的 `DspUnreadable`(已經在 `side_effects.py` 內部處理掉,不會冒到這一層),還包含任何真正的程式錯誤:例如某次改動不小心讓 `attempt_store.history()`、`TaskReader` 或六條指標任一支計算函式丟出 `AttributeError`/`KeyError`/`TypeError`/`sqlite3.OperationalError` 等。`slo.py:228-240` 的 `evaluate()` 用 `except Exception as exc` 整批接住,轉成 `SloStatus(..., missing=True, error=f"{type(exc).__name__}: {exc}")`,跟 `DspUnreadable` 造成的合法「資料來源缺」用同一套欄位表示,外觀上完全分不出來。

會出什麼錯的行為:`slo.run()`(`slo.py:267-292`)印出 JSON 之後只檢查 `all(s.stable for s in statuses)` 來決定要不要回 `EXIT_UNSTABLE`(5),完全沒有檢查任何一條的 `error` 欄位;不論是哪一條、甚至六條全部同時因為程式錯誤而炸,`run()` 都回 `EXIT_OK`(0)。任何只看行程結束代碼的排程或告警管線(這正是這個模組設計給命令列/排程使用的方式)會看到「執行成功」,但其中一條甚至全部指標其實是空的、`error` 欄位藏著例外訊息,只有解析 JSON 內文才看得到。這跟這個修正本身要解決的問題(單一條讀不到不該拖垮另外五條)是兩件事:修正前,任何未預期例外會讓整個 `run()` 崩潰、行程以非零代碼結束、看得出來壞了;修正後,同一類例外被系統性地降級成「跟 DSP 暫時連不上」同一種待遇,而且沒有任何退出碼或 stderr 訊號告知有指標算到一半出錯。這個風險本身在計劃書(`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase9可觀測與SLO_計劃.md:344`)只描述了「怎麼隔離」,沒有列進「未排除」清單、也沒有依 CLAUDE.md 的規矩附上回頭條件。

查證:`tests/ops/test_slo.py::test_one_unreadable_slo_does_not_hide_the_others`(`governance/review-reports/code-phase9-inc3/r2-delta-tests.patch:625-643`)只驗證了 `slo.evaluate()` 這一層會把 `RuntimeError` 轉成 `missing=True, error=...`,但同一份 patch 裡 `test_the_command_line_reports_fixed_exit_codes`(`r2-delta-tests.patch:674-696`)只測了 `EXIT_NO_DATABASE`/`EXIT_NOT_UPGRADED`/`EXIT_UNSTABLE`/`EXIT_OK` 四種情境,完全沒有一支測試從 `slo.run()` 層級驗證「某條指標因為程式錯誤而 `error` 不為空」時的結束代碼——也就是說現在的行為(回 0)沒有被任何測試釘住,是名副其實的殺得死也沒人發現的缺口。

建議修法:讓 `run()` 額外檢查 `any(s.error for s in statuses)`,對這種情況回一個獨立的結束代碼(例如新的 `EXIT_PARTIAL_ERROR`)並把例外訊息印到 stderr,不要讓「某條指標算到一半丟了未預期例外」跟「乾淨執行完畢」共用同一個結束代碼;至少也要在 stderr 印出警告,讓看 log 的人能發現,而不是只能翻 JSON 內文。

## 專案要求的其餘檢查項

**第一列鍵對不上時的判法對照規格 [S653] 是否合理**:合理,且有文件與測試支撐。[S653] 字面只講「找不到第一列的算無法核對」,但計劃書(`...RTB_Phase9可觀測與SLO_計劃.md:346`)明確把這個判法寫成「把快照讀不回來或對不上鍵視同找不到可信的第一列」,理由是快照跟鍵對不上本身就是資料異常,不能拿來當「證明沒有更早提交」的依據。我讀了實作:`_identity()`(`side_effects.py:293-296`)仍然用能解析出來的內容算身份、去核對「有沒有更早的同身份提交」——這一步不因為鍵不符而放棄,因為「有更早提交」是靠手足鍵的既有提交時間證明的獨立事實,不依賴這份快照本身可不可信;但只有在證實有更早提交時才判壞,證實不了時一律退到「無法核對」(`side_effects.py:361-368`),絕不會因為快照有問題就把它放行成好事件。這個設計方向對「目標為零」的安全指標來說是保守、寧可少算好事件也不錯放違規的做法,測試 `test_a_first_row_whose_snapshot_does_not_match_its_key_is_never_good` 覆蓋了「鍵算不出來」與「解析失敗」兩種資料異常,沒有發現會把異常誤判成好事件的路徑。

**穩定欄在命令列回結束代碼 5 的條件**:正確。`slo.py:289-292` 的判斷是 `not all(s.stable for s in statuses)`,而六條指標裡只有 `end_to_end_handoff` 才可能回 `stable=False`(其餘五條的 `Tally.stable` 恆為預設值 `True`,`side_effects.py:80` 的因果論證見上文),所以這個條件精確對應「有一條跨資料庫讀了三輪都不同」這個唯一場景,測試 `test_an_unstable_cross_database_read_is_reported` 從 `sli.count` 到 `slo.run()` 全程驗到位。

**提交時間統一 UTC 對既有 DSP 測試與既有資料庫的影響**:見上方 x1-1 小節,測試面(`test_dsp_commit_times_never_go_backwards`、`test_dsp_commit_times_are_stored_in_one_utc_form`)都過;既有資料庫面,只要 production 沒有注入過非 UTC 時鐘(目前程式碼裡確實沒有這條路徑),既有資料本來就是 `commit_text()` 產出的正規化格式,寫入前後兩種寫法字串序一致,不需要搬遷。

**核可使用表新索引有沒有改到 Phase 6 查詢計畫**:沒有改到。我在 `/private/tmp/.../scratchpad/p9i3-explain/repo`(複製自本工作樹,未動原始 repo)用實際的 `attempt_store.SCHEMA`/`inbox_store.SCHEMA` 建表、跑過既有 fixture 資料後,直接對 `approval_use_count_query(campaign_id="c1")` 下 `EXPLAIN QUERY PLAN`,得到:

```
SEARCH f USING INDEX attempts_first_rows (campaign_id=?)
SEARCH u USING INDEX sqlite_autoindex_approval_uses_1 (task_id=? AND revision=?)
```

跟新索引 `approval_uses_by_key` 完全無關,跟既有 `tests/executor/test_observability.py::test_applied_count_uses_its_index` 釘住的斷言(`attempts_first_rows` + `approval_uses_1`,且不得 `SCAN u`)一致,我另外跑過這支測試單獨確認通過。同時對新的批量查詢 `approval_uses_for_query(["k1","k2"])` 下 `EXPLAIN QUERY PLAN`,得到 `SEARCH approval_uses USING COVERING INDEX approval_uses_by_key (key=?)`,證實 x1-6 的全表掃問題確實解決,且沒有意外把 Phase 6 那支查詢也帶去用新索引。

## 測試

`cd /Users/enzo/rtb-p9i3 && PYTHONPATH=src /Users/enzo/rtb-p9i1/.venv/bin/python -m pytest`:1642 個測試全數通過(94.2 秒)。所有實驗(EXPLAIN QUERY PLAN、變異測試複核)均在 `/private/tmp/claude-501/.../scratchpad/p9i3-explain/` 底下的複本進行,未修改本工作樹任何檔案。
