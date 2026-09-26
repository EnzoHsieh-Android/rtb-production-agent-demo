severity: minor

# 第 2 輪 鏡頭B:讀取層白名單、金額單一來源、收據與錄製鍵相容、評估集

審材:`r2-delta-reader.patch`(逐 hunk 讀完);需要時對照 `r2-snapshot.patch` 與 HEAD 原始碼。

## 先講已查證、沒出事的部分

- **測試子集全綠**:
  - `tests/analyzer/test_dsp_client.py`、`test_investigation_review.py`、`test_investigation_reads.py`、`tests/domain`、`tests/eval`:517 passed。其中包含入庫錄製重播 `test_the_investigation_eval_in_ci_replays_only_and_misses_nothing`(入庫目錄存在,有實際跑)。
  - `tests/demo`(兩支瀏覽器測試除外):596 passed,展示錄製重播沒有漏鍵。
- **收據與錄製鍵**:
  - 72 筆收據摘要雜湊 `0f2e89c3…` 與 SYSTEM_PROMPT 雜湊維持基準。
  - `prompt()` 在 origin/main..HEAD 沒有改。
  - 沒截斷時歷史收據仍然只由列與決策 now 算。
  - 新欄位 `history_truncated` 只在截斷(>50 筆)時出現,這是本增量新開的路徑,不會碰到既有錄製。
- **金額轉浮點不差一分**:
  - 對整數部分 0 到 13 位的固定兩位小數字串隨機抽 30 萬筆,驗 `receipt_amount(fixed_amount_float(s)) == s`,0 筆不符。
  - 數學上也成立:15 位以內有效數字的十進位值,轉雙精度再取最短 repr,只會回到同值。
- **評估集跟新白名單相容**:
  - 72 筆逐筆跑 `check_daily`、`check_history`、`check_adjustments`、`check_longer_window`、`_daily_matches`,全部通過。
  - 生產讀取層(現在經 `read_query_options` 做跨窗核對)不會把評估集判成 invalid。
  - 過去調整的 `days_ago` 分布是 5 到 12,近期加額在 1 到 2 天前,離 3 天界線都超過一天。歷史列改成同一時刻後,收據不變。
- **claims 雜湊**:十一支檔的 sha256 都跟 HEAD 實檔一致,policy 文字沒動。
- **`instrumented.fetch` 改走 `read_query_options`**:
  - 讀取順序、讀取次數、tool_calls 記法都沒變。
  - 5xx 照樣往外丟,不寫入。
  - 只是多了「同一次 fetch 同時有逐日和長窗時做核對」這一步。

## 發現 1:DSP 的 7 天加總可以超過讀取層的 13 位上限,每天都能存的金額,讀較長時間窗卻整份 invalid
severity: minor
blocking: 否

引句:「模擬 DSP 自己的整數分上限也是這個數(rtb.dsp 刻意不依賴這裡,另有一份同值的上限)。」

引句:「+    "spend": is_amount_or_none,」

- **設計意圖**:這次把讀取白名單定成整數部分最多 13 位,註解說 DSP 的上限是同一個數。
- **實際狀況**:DSP 只對單一日桶設上限。1d/7d 視窗是讀取時用 Python 整數把七天加總,再經 `money_text` 輸出,加總的位數沒有上限。圖譜 Mock-DSP 也寫「七天加總遠低於 SQLite 上限」,只考慮了資料庫,沒考慮分析端白名單(file: `src/rtb/dsp/store.py:87`)。
- **結果**:七天加總最多可到 14 位整數,分析端的 `METRICS_FIELDS["spend"]` 不收,整個 check_longer_window 記 invalid。過去調整前後各三天的合計也用同一種方式加總,推論會同樣出事(沒另外重現)。

最小重現(已跑):

```
$ PYTHONPATH=src .venv/bin/python scratchpad/repro_7d.py
7d spend from DSP: 69999999999999.93
reader whitelist accepts 7d spend: False
daily row spend accepted: True
```

- 腳本內容:每天花費 `"9999999999999.99"` 種 7 天(DSP 收下),讀 `get_metrics("c1","7d")`,再丟給讀取層白名單判。
- 本輪改寫的防回歸測試 `test_an_extreme_amount_never_leaves_the_task_stuck_collecting_evidence` 用的正是這個最大值。那支測試只走逐日,所以沒踩到。
- **影響**:方向保守,只會變成「證據不足」,不會錯提案;而且要極端金額才會觸發,所以列 minor。但「DSP 上限與讀取白名單同一個數」這句宣稱,對視窗與前後對照不成立,是這次修正自己帶進來的新邊界。
- **建議**(擇一):
  - DSP 在推算視窗或前後對照時,加總超過 `MAX_CENTS` 就回缺值(null),跟「存不下記缺值」的遷移做法同一套;
  - 或把日桶上限降到「七天加總仍在 13 位內」。
  - 另外把 Mock-DSP 那句「七天加總遠低於 SQLite 上限」補上分析端上限。

## 發現 2:截斷時摘要的「最近 3 天」仍可跟回傳列矛盾,收據照收,第 3 條仍可能被繞過(r1 鏡頭2 發現 2 只修了一半)
severity: minor
blocking: 否

引句:「+    """摘要自身的大小關係,以及跟回傳列的下界:摘要算的是完整集合,不能比回傳的列還少。"""」

引句:「+                and counts["total_budget_changes"] >= budget and counts["total_pauses"] >= pauses)」

- **這次加了什麼**:`_summary_agrees` 加了總筆數、加額總數、暫停總數對回傳列的下界。
- **缺了什麼**:沒有核對 `budget_changes_last_3d` 或 `budget_changes_7d` 對回傳列的下界。r1 那條發現講的「收據說最近 3 天零筆,第 3 條被繞過」這個失敗,只要把總數調成一致就還重現得出來。
- **文件宣稱過頭**:`check_history` 的說明與圖譜(`分析行程流程與檢查點` 第 287 行)都寫「摘要跟回傳的列對不上……整份不收」,但實際只擋了總數。

最小重現(已跑,`scratchpad/repro_sum.py`):49 筆 20 天前的暫停,加 1 筆 10 小時前的加額;摘要 `total_budget_changes=1`、`budget_changes_7d=0`、`budget_changes_last_3d=0`。

```
accepted: True
truncated receipt: {'budget_changes': '1', 'pauses': '59', 'budget_changes_before_3d': '1', 'budget_changes_last_3d': '0', 'history_truncated': 'true'}
rows-only receipt: {'budget_changes': '1', 'pauses': '49', 'budget_changes_before_3d': '0', 'budget_changes_last_3d': '1'}
```

- **影響**:同一份回應裡,列明明有 10 小時前的加額,收據卻寫最近 3 天 0 筆、3 天以前 1 筆。模型照 SYSTEM_PROMPT 第 3 條判,就不會停在「證據不足」。
- **為什麼列 minor**:前提是 DSP(可信欄位、自家模擬器)自己算錯。DSP 在同一個快照裡讀摘要和列,正常情況下不會發生。
- **建議**:
  - `receipt_payload` 的截斷分支已經有決策 now,可以用列算出最近 3 天的加額數,跟摘要取較大值(與「保守旗標」的定位一致);
  - 或在 `check_history` 核對 `budget_changes_7d`、`budget_changes_last_3d`,不能少於列裡 `committed_at` 在最近 3 天內的加額數(以列裡最新的時刻當上界近似)。
  - 並把說明與圖譜第 287 行的措辭改成實際核對的範圍。

## 發現 3:圖譜的讀取白名單 RULE 仍寫「花費/營收(有限數字或空值)」,跟程式與同一篇的新段落不一致
severity: minor
blocking: 否

引句:「+    # 浮點(舊 DSP 與評估案例)或 DSP 整數分的固定兩位小數字串;判準只在領域層 _checks 一份」

- **舊句還在**:`分析行程流程與檢查點` 第 69 行的 `RULE:` 仍寫指標白名單「花費/營收(有限數字或空值)」(file: `docs/rtb-production-agent-demo-knowledge/Systems/分析行程流程與檢查點.md:69`)。
- **程式已經改了**:HEAD 的 `METRICS_FIELDS` 也收固定兩位小數字串(整數 13 位、ASCII、可帶負號)。同一篇第 286 行已經照新行為描述。
- **為什麼要改**:這條 RULE 有 `[since]`、`[retire]`,沒有 `[confirmed]`,依 CLAUDE.md 只算線索、不能挑戰程式碼。但它是這份白名單的正本描述,接手的人會先讀到它。這屬於文件內部不一致。
- **建議**:改寫那一行,或註明「Phase 14 增量 2a 起另收固定兩位小數字串,見下方 2a 段」。

## 第 1 輪相關項驗收(本鏡頭範圍)

| 第 1 輪項目 | 驗收結果 |
|---|---|
| 鏡頭2-3 負數跨窗比較 | 已修。`check_longer_window` 與 `_daily_matches` 回到 `exact_value` 三態,負數不比。`test_negative_amounts_are_not_compared_across_windows` 釘住。 |
| 鏡頭2-4、鏡頭4-2、架構對齊 1、資安 1(三套金額判法、Unicode 數字、長度不限) | 已修。分析端剩 `_checks` 一份,`re.ASCII`、13 位、不收前導零。dsp_client、metrics、nine_rules 都不再 import `re`。DSP 依偏離 (b) 自留一份同值上限,屬已申報偏離。另見發現 1 的加總邊界。 |
| 架構對齊 2(`exact_value` 被拿掉) | 已修。 |
| 鏡頭2-5(評估集歷史列早 2 小時) | 已修。新測試核 48 筆同時刻、同 UTC 日;收據雜湊不變。 |
| 鏡頭2-6(跨午夜誤判 invalid) | 依偏離 (a) 接受取捨,註解與計劃第 134 行已寫明,方向保守。 |
| 鏡頭2-2、鏡頭4-1(摘要與列矛盾、暫停數無上界) | 暫停數已修;「最近 3 天」這半沒修,見發現 2。 |
| 外家 finder 4(1h 轉浮點差一分) | 已修,13 位內實測不差。 |
| 外家 finder 6([S1108] 三讀描述) | 已修,Phase13 計劃第 673 行已改。 |
| spec-conformance 1、2(未截斷也帶 summary、缺 `truncated`) | 已修。DSP 只在超過 50 筆時帶,讀取層要求 `truncated is True`、剛好 50 列、總數大於 50。 |
| 鏡頭3-3(遷移補不回前值) | 讀取層收 `budget_before: null`,收據那欄 na,有測試。 |

## 圖譜鏡頭(LUMOS-IMPACT: origin/main..HEAD)

派工尾端沒有附固定席筆記。我自己跑了 `scripts/lumos impact --diff origin/main..HEAD --ranked`,拿到固定席 9 篇,逐條判:

- **Mock-DSP(INVARIANT)**:冪等、原子寫入、F1 逾時、版本不符拒收這四條,本審材(讀取層)都沒碰。「13 位跟分析端讀取白名單同一個數」一句,對視窗加總不成立,見發現 1。
- **任務流程領域模型(INVARIANT)**:
  - `_checks` 新 import 的是 `fractions`,仍在純標準函式庫白名單內,領域 import 白名單測試綠。
  - 提案解析、證據新鮮度、狀態機三條沒碰。
- **分析行程流程與檢查點(INVARIANT)**:
  - F5 名稱注入路徑與 prompt 欄位白名單沒動。
  - 每步落地一列的不變量沒動:`instrumented` 只改成先讀齊再建收據,不寫入的時機不變。
  - 第 69 行 RULE 過時,見發現 3。
- **正式九條判斷領域規則**:`_check_amount` 改接 `_checks`,比原本多擋前導零與超過 13 位,只會更嚴。決策函式沒動,第 29 行宣稱仍成立。
- **確定性指標計算(INVARIANT)**:「算不出值 → 沒有值加原因、絕不變 0」仍成立。負的固定字串歸 INVALID_DATA、收據 na,有測試。第 65 行已補 2a 說明。
- **評估與Jev決策點**:72 筆、答案、收據雜湊、SYSTEM_PROMPT、入庫錄製重播都已實測不變。
- **提案收件口 / 共用行程基礎 / 執行迴圈(hop1–2, INVARIANT)**:都是經 claims 雜湊或 dsp_client 連過來的。本 diff 沒碰送件、冪等鍵、租約表、狀態轉移,claims 雜湊已核對一致,不影響這幾篇的宣稱。

### 表態記錄(可反駁的宣稱)

- **Mock-DSP 第 78 行「歷史查詢沒有分頁……Mock 規模可接受」**:
  - 現在有 50 列上限加截斷摘要,「無界回應」那半已不存在;「沒有分頁」仍屬實。
  - 讀取層白名單要求截斷時剛好 50 列,跟 DSP 的 `HISTORY_PAGE` 同值(兩邊各一份常數)。
  - 宣稱未被推翻。
- **[S1113] 租約讀取次數**:
  - `read_query_options` 只拿已讀到的回應做核對,不另打 DSP。
  - `test_every_query_combination_uses_its_declared_dsp_reads_within_the_lease` 綠。
  - 宣稱未被推翻。

## 總結

- 第 1 輪落在讀取層、金額單一來源、收據、評估集的項目,大多已確實修好。收據、SYSTEM_PROMPT、評估與展示的錄製重播都實測不變。
- 本輪新找到或殘留 3 條,都不擋合入:
  - DSP 視窗加總可能超出讀取白名單的 13 位上限;
  - 截斷摘要的最近 3 天筆數沒跟列核對;
  - 圖譜一條 RULE 過時。
- 前兩條的失敗方向分別是保守的證據不足,以及需要 DSP 自己算錯才會觸發。
