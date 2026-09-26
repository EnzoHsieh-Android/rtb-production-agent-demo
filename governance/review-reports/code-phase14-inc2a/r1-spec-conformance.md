severity: minor

# Phase14 增量2a 對答案審查（spec-conformance）

審查範圍：`RTB_Phase14正式規則照九條判斷_計劃.md` 的 [S1414][S1415][S1422][S1423][S1425][S1426]（單元那半）、
「不改正式決策、AI 收據與錄製鍵不變」、[S1113] 讀取次數。對照 diff：
`governance/review-reports/code-phase14-inc2a/r1-snapshot.patch`。逐條列於下，最後列出偏差。

## 逐條裁定

### [S1414]（DSP 跨窗 Fraction 精確核對、整數分金額）—— 已實作

- 引句（spec）：「模擬 DSP 應先將逐日、視窗金額以整數分存算並回固定兩位小數字串，`dsp_client` 讀取層以同一 `Fraction` 精確核對，把真不一致記 `invalid`」
- `_cents`/`_money` 把金額換算成整數分存放、對外回固定兩位小數字串：file: `src/rtb/dsp/store.py:247`、`src/rtb/dsp/store.py:265`
- `dsp_client.read_query_options`/`_daily_matches` 在同一步已有長窗（1d/7d）時，用 `Fraction` 核對逐日與長窗，不合即回 `invalid`，且不另打 DSP：file: `src/rtb/analyzer/dsp_client.py:355`、`src/rtb/analyzer/dsp_client.py:477`
- 綁定測試：file: `tests/analyzer/test_dsp_client.py:309`（`test_daily_rows_must_match_the_longer_windows`）、`tests/dsp/test_store.py:496`（`test_amounts_are_stored_as_integer_cents_and_returned_as_fixed_decimal_strings`）

### [S1415]（讀取層那半：唯一調整紀錄、最近一次加額、D±3、後段未滿留空）—— 已實作

- 引句（spec）：「端點不以查詢時鐘篩三天，回最近一次加額的 `committed_at`、前後預算與 D 前後各三個完整日成效，D 當天排除，後段未滿三個完整日則 `after_conversions=null`，不回空列；後來五筆減額不擠掉它」
- `_apply` 在同一交易記下 `budget_before`（僅 `update_budget` 有值）：file: `src/rtb/dsp/store.py:788`（原始碼行號另見 patch 行 2525-2551）
- `_latest_raise` 只挑「後 > 前」的加額、按 `operation_id` 由新到舊找第一筆，減額不影響：file: `src/rtb/dsp/store.py:622`
- `get_past_adjustments` 用 `_three_days` 算 D±3（`n in (1,2,3)`，不含 D 本身），`today > day+3` 才算後段，否則整包回 `None` 欄位：file: `src/rtb/dsp/store.py:653`、`src/rtb/dsp/store.py:674`
- `server._get_adjustments` 不再按查詢時鐘篩三天：file: `src/rtb/dsp/server.py:180`
- `dsp_client.ADJUSTMENT_ROW_FIELDS` 移除 `MIN_ADJUSTMENT_AGE_DAYS` 拒收，`days_ago` 改收 `>=0`：file: `src/rtb/analyzer/dsp_client.py:253`
- 綁定測試：file: `tests/dsp/test_investigation_data.py:247`（`test_past_adjustments_include_normal_budget_operations`）
- 決策時鐘的三天／D+3 切點按計劃明訂留給後續子增量（`Verification/Phase14增量2a離線驗證.md`：「正式規則的三天切點留待後續子增量」），本次不判未實作。

### [S1422]（歷史 50 列上限 + 完整七日摘要）—— 縮水（見下方發現 1、2）

- 引句（spec）：「歷史端點應只回最多 50 列並提供以完整七日集合計算的近期預算旗標與計數；第 3 條不得因被截斷的舊列不在回應就判沒有近期調整」（此為驗收條款本體，已達成）
- `history_limited` 只回最近 50 列，`summary` 由完整（未截斷）SQL 聚合算出：file: `src/rtb/dsp/store.py:721`
- 綁定測試：file: `tests/dsp/test_investigation_data.py:312`（`test_bounded_history_preserves_recent_budget_changes`，含 64 KiB 上限與收據核對）
- 驗收條款本體達成；但支撐同一條款的「設計」段落另有兩項承諾未落實，列為發現 1、發現 2（均非阻斷性）。

### [S1423]（最近加額 D±3 桶不被 30 日滾動保留淘汰）—— 已實作

- 引句（spec）：「DSP 應保留該次 D 前後六日桶，端點仍能計算前後轉換，不因 30 日滾動淘汰而永久缺證據」
- `_prune_daily` 算出最近加額 D 的前後六個 UTC 日期，排除在刪除條件之外：file: `src/rtb/dsp/store.py:548`
- 綁定測試：file: `tests/dsp/test_investigation_data.py:267`（`test_latest_raise_daily_buckets_survive_rolling_retention`，40 天前加額仍可算出 D±3）

### [S1425]（過去調整驗 `committed_at`、不再拒收 `days_ago<3`、72 筆重生）—— 已實作

- 引句（spec）：「讀取層應驗帶時區時間戳但不得再以 `days_ago<3` 拒收；生成器按固定評估 `NOW−days_ago` 補舊案例時間戳、重新 render 72 筆，逐筆格與答案不變」
- `ADJUSTMENT_ROW_FIELDS` 要求 `committed_at` 為帶時區字串，`days_ago` 不再有下限拒收：file: `src/rtb/analyzer/dsp_client.py:253`
- `investigation_cases._adjustment` 加入 `committed_at = (NOW - timedelta(days=days_ago)).isoformat()`：file: `src/rtb/eval/investigation_cases.py:304`
- 收據不含時間戳（`receipt_payload` 未讀 `committed_at`）：file: `src/rtb/analyzer/investigation.py:243`
- 綁定測試：file: `tests/analyzer/test_dsp_client.py:346`（`test_adjustment_timestamp_preserves_recorded_receipts`，收據不含 `committed`、缺時區拒收）、`tests/eval/test_investigation_eval.py:1056`（`test_adjustment_timestamp_preserves_recorded_receipts_for_all_72_cases`，72 筆格/答案/收據雜湊與 `SYSTEM_PROMPT` 雜湊凍結）

### [S1426]（單元那半：跨 UTC 午夜冪等物化日桶、同交易更新 1d/7d）—— 已實作（單元層級）

- 引句（spec）：「模擬 DSP 應在讀取前冪等物化剛完成的 UTC 日桶、與 1d／7d 窗同交易更新」
- `_materialize_daily` 在讀取前補齊跨日桶，並在同一交易呼叫 `_refresh_windows`/`_prune_daily`：file: `src/rtb/dsp/store.py:579`
- 綁定測試：file: `tests/dsp/test_investigation_data.py:290`（`test_demo_daily_rollover_preserves_full_windows`，午夜前後兩次讀取確認 7 個完整日與 1d/7d 滑動）
- 完整 F1–F7 跨午夜情境需要能綁本機埠的環境，`Verification/Phase14增量2a離線驗證.md` 已如實標「本環境未跑」，本次任務範圍明定只查「單元那半」，不判未實作。

### 不改正式決策、AI 收據與錄製鍵不變 —— 已實作

- diff 未觸及 `src/rtb/analyzer/policy.py`、`src/rtb/analyzer/flow.py`、`src/rtb/analyzer/runner.py`、`src/rtb/analyzer/ai_judge.py`（`diff --git` 清單中無此四檔）。
- `SYSTEM_PROMPT` 位元組雜湊凍結、72 筆格/答案/收據雜湊凍結：file: `tests/eval/test_investigation_eval.py:1056`

### [S1113]（讀取次數，維持單步 ≤60 秒租約）—— 已實作

- 引句（增量說明）：「AI 單查逐日只讀 1 次；同一步已有長窗 1d/7d 才在 dsp_client 共用讀取層做精確核對……MAX_COLLECT_READS=6 時預設逾時單步最壞 58 秒，小於 60 秒租約」
- `read_query_options` 對 `check_daily_trend` 單獨選取時只呼叫逐日端點一次，跨窗核對只在同一步也選了 `check_longer_window` 時重用其已讀資料，不另打 DSP：file: `src/rtb/analyzer/dsp_client.py:477`
- `READS_PER_OPTION`／`MAX_COLLECT_READS`（=6）未被此次改動放寬：file: `src/rtb/analyzer/investigation.py:96`、`src/rtb/analyzer/investigation.py:103`
- 綁定測試：file: `tests/analyzer/test_investigation_reads.py:28`（`test_every_query_combination_uses_its_declared_dsp_reads_within_the_lease`，枚舉所有查詢組合核對實際讀數，並斷言 `collect_step_worst_seconds(3.0, 6) < 60`）

## 發現 1：歷史端點永遠附帶 `summary`，未依「未截斷維持既有形狀」的設計文字執行

severity: minor
blocking: 否
引句:「未截斷時維持既有 {"history": […]} 形狀與收據字串」（spec，`RTB_Phase14正式規則照九條判斷_計劃.md:97`）
引句:「rows, summary = store.history_limited(campaign_id)\n        return {"history": [asdict(h) for h in rows], "summary": summary}」（diff，`src/rtb/dsp/server.py:159-163`）
file: `src/rtb/dsp/server.py:159`
file: `src/rtb/dsp/store.py:721`

`_get_history` 不論操作紀錄是否超過 50 筆（是否真的被截斷），都無條件附上 `summary` 欄位；spec 設計段落明講「未截斷時維持既有 `{"history": […]}` 形狀」，即未截斷時不應多出 `summary` 鍵。目前實作把「摘要一律存在」當成常態，`check_history` 白名單也因此要同時接受 `{"history"}` 與 `{"history","summary"}` 兩種集合（`src/rtb/analyzer/dsp_client.py:318`）。經核對 `receipt_payload` 的摘要分支與舊逐列計算在數值上等價（皆為完整、未截斷集合），不影響已凍結的 72 筆收據雜湊，故不判為造成錯誤行為；但這是明確多出「設計」條文未授權的持續性線上行為變更（新 DSP 一律送出摘要，即使歷史很短）。

## 發現 2：截斷時未附帶 `truncated=true` 旗標

severity: minor
blocking: 否
引句:「超過上限時端點另帶由完整七天集合算出的 recent_budget_change、預算及暫停計數與 truncated=true」（spec，`RTB_Phase14正式規則照九條判斷_計劃.md:97`）
file: `src/rtb/analyzer/dsp_client.py:316`
file: `src/rtb/dsp/store.py:721`

`history_limited` 回傳的 `summary` 只有 `total_operations`、`total_budget_changes`、`total_pauses`、`budget_changes_7d`、`budget_changes_last_3d`、`has_recent_budget_change` 六個鍵（`src/rtb/dsp/store.py:733` 附近，patch 行 2474-2479），`check_history` 白名單同步只收這六鍵加 `history`（`src/rtb/analyzer/dsp_client.py:322-333`），全 diff 搜尋 `truncated` 不到任何一處。spec 明確要求截斷時另帶 `truncated=true` 以「避免誤導 AI」；目前讀者（含日後要接線的規則輪／AI）無法從回應本身分辨這批歷史是否被截斷，只能信任摘要一律正確。由於 [S1422] 驗收條款本體（50 列上限＋完整七日旗標與計數）已用摘要精確值滿足，此缺口屬設計段落的透明度要求未落地，不致產生錯誤決策，故列縮水、非阻斷。

## 縮水+未實作共 2 條
