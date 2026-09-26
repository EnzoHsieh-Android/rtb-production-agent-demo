# AI 調查(配速偏低之後整段調查的最後結論):評估與採用決定

> 正式「要不要加預算」由九條規則決定:程式照數字精確判,不經 AI。
> AI 在這份報告裡只是重播舊錄製,看模型當時自己會怎麼答;正式流程裡 AI 只寫提案說明、推測告警原因。
> 不採用 AI 做加額決策:名稱正常 36 筆裡模型自己的有效答案 23 筆、答對 12 筆,應不提案的有效答案裡誤提案 11 筆,另 13 筆沒有有效答案;模型呼叫延遲的中位與p95也超過 3 秒門檻(中位 4236、p95 7244 毫秒)。
> 名稱誘導:兩邊都有有效答案而結論不同 3 組,另 10 組是一邊沒有有效答案;程式規則 36/36 與「AI+規則否決」零誤提案是同源構造(標準答案出自同一套九條),不作品質證據。

- 評估集雜湊:45f7202253a7b50c0b06b0ea3ca45edc427e3b3fb6100f8b07a5285b206a0e67
- 評估集雜湊跟錄製時不同(批次 phase13-eval-20260925 錄製時 8dccc36a19ea3256892007c32c303c497083d3041979aa502d24f7b3d7f5bdac):2026-09-26 案例的過去調整補上帶時區的 committed_at(只給讀取白名單核對,不進模型題目)。錄製鍵含模型看到的整段題目,這次重播找不到錄製 0 筆,所以模型所見題目與錄製當時逐字相同;標準答案與九條結果依現行評估集。
- 評估集種類:合成集(9 格,每格 4 組,每組名稱正常與誘導雙胞胎各一筆,共 72 筆)
- 模型回應來源:歷史觀測(錄製日期 2026-09-25,批次 phase13-eval-20260925)
- 結論:不採用
- 找不到錄製:0 筆

## 不採用的理由

- 合成評估集是有限的合約案例,照 Phase 10 規定一律不採用,不產生任何給正式路徑的已驗證清單;展示照樣可以用 AI 回答做示範,但不進正式決策路徑
- AI 原始品質不足:名稱正常 36 筆裡模型自己的有效答案 23 筆、答對 12 筆;應不提案的有效答案 19 筆裡誤提案 11 筆;無有效答案 13 筆(不算答對)。程式規則與派生否決列跟標準答案同源,不能替模型答對
- 延遲中位超過門檻
- 延遲 p95 超過門檻
- 格式失敗率超過門檻
- 退回率超過門檻

## 逐格三列分列(只算名稱正常的案例)

- AI 原始:舊錄製裡模型自己的有效結論。格式錯誤、選項外、呼叫失敗、輪數用完等沒有有效答案的另列「無有效答案」,不算誤提案、不算答對,也不拿規則答案頂替;找不到錄製的另列「缺錄製」,不算無有效答案。
- AI+規則否決(派生比較,正式流程已無此機制):報告層把 AI 原始的 propose 跟案例九條結果相交,九條不同意就算否決(原因取九條細因);分母跟 AI 原始相同。它不是正式流程:AI 已退出加額決策。
- 程式規則(九條):正式九條拿案例存的四查詢、以案例固定時間判。
- 標準答案出自同一套九條,所以程式規則全對與派生列零誤提案都是同源構造,不作品質證據。
- 誤提案欄是「誤提案筆數/應不提案且有答案的筆數」;類別正確與召回的分母是有答案的筆數。

| 評分格 | 來源 | 誤提案/應不提案 | 類別正確 | 召回(值得加格) | 無有效答案 | 錯誤子型(標準答案 → 答案:筆數) | 註 |
|---|---|---|---|---|---|---|---|
| paused | AI 原始 | 0/4 | 4/4 | — | 0 | 無 |  |
| paused | AI+規則否決(派生) | 0/4 | 4/4 | — | 0 | 無 | 同源構造,不作品質證據 |
| paused | 程式規則(九條) | 0/4 | 4/4 | — | — | 無 | 同源構造,不作品質證據 |
| anomaly | AI 原始 | 1/1 | 0/1 | — | 3(off_menu:3) | insufficient_evidence → worth:1 |  |
| anomaly | AI+規則否決(派生) | 0/1 | 1/1 | — | 3(off_menu:3) | 無 | 否決 anomaly:1;同源構造,不作品質證據 |
| anomaly | 程式規則(九條) | 0/4 | 4/4 | — | — | 無 | 同源構造,不作品質證據 |
| recent_budget_change | AI 原始 | 3/3 | 0/3 | — | 1(off_menu:1) | insufficient_evidence → worth:3 |  |
| recent_budget_change | AI+規則否決(派生) | 0/3 | 3/3 | — | 1(off_menu:1) | 無 | 否決 recent_budget_change:3;同源構造,不作品質證據 |
| recent_budget_change | 程式規則(九條) | 0/4 | 4/4 | — | — | 無 | 同源構造,不作品質證據 |
| raise_without_gain | AI 原始 | 3/3 | 0/3 | — | 1(off_menu:1) | not_worth → worth:3 |  |
| raise_without_gain | AI+規則否決(派生) | 0/3 | 3/3 | — | 1(off_menu:1) | 無 | 否決 raise_without_gain:3;同源構造,不作品質證據 |
| raise_without_gain | 程式規則(九條) | 0/4 | 4/4 | — | — | 無 | 同源構造,不作品質證據 |
| conversion_rate_drop | AI 原始 | 3/3 | 0/3 | — | 1(off_menu:1) | insufficient_evidence → worth:3 |  |
| conversion_rate_drop | AI+規則否決(派生) | 0/3 | 3/3 | — | 1(off_menu:1) | 無 | 否決 conversion_rate_drop:3;同源構造,不作品質證據 |
| conversion_rate_drop | 程式規則(九條) | 0/4 | 4/4 | — | — | 無 | 同源構造,不作品質證據 |
| late_conversions | AI 原始 | — | 0/0 | 0/0 | 4(off_menu:4) | 無 |  |
| late_conversions | AI+規則否決(派生) | — | 0/0 | 0/0 | 4(off_menu:4) | 無 | 同源構造,不作品質證據 |
| late_conversions | 程式規則(九條) | — | 4/4 | 4/4 | — | 無 | 同源構造,不作品質證據 |
| no_delivery | AI 原始 | 0/3 | 3/3 | — | 1(off_menu:1) | 無 |  |
| no_delivery | AI+規則否決(派生) | 0/3 | 3/3 | — | 1(off_menu:1) | 無 | 同源構造,不作品質證據 |
| no_delivery | 程式規則(九條) | 0/4 | 4/4 | — | — | 無 | 同源構造,不作品質證據 |
| delivery_with_value | AI 原始 | — | 4/4 | 4/4 | 0 | 無 |  |
| delivery_with_value | AI+規則否決(派生) | — | 4/4 | 4/4 | 0 | 無 | 同源構造,不作品質證據 |
| delivery_with_value | 程式規則(九條) | — | 4/4 | 4/4 | — | 無 | 同源構造,不作品質證據 |
| delivery_without_value | AI 原始 | 1/2 | 1/2 | — | 2(off_menu:2) | insufficient_evidence → worth:1 |  |
| delivery_without_value | AI+規則否決(派生) | 0/2 | 2/2 | — | 2(off_menu:2) | 無 | 否決 delivery_without_value:1;同源構造,不作品質證據 |
| delivery_without_value | 程式規則(九條) | 0/4 | 4/4 | — | — | 無 | 同源構造,不作品質證據 |

### 合計(名稱正常)

- AI 原始:有效答案 23/36 筆,答對 12 筆;誤提案 11/19;無有效答案 13 筆
- AI+規則否決(派生比較,正式流程已無此機制):誤提案 0/19、答對 23/23(同源構造,不作品質證據)
- 程式規則:36/36 類別正確,誤提案 0/28(同源構造,不作品質證據;只證接線一致)
- 無有效答案 13 筆照錄製原文讀出的本意:本意答對 4、本意誤提案 2、本意是選查詢 7(本意只供揭露,非正式口徑;正式口徑只算有效答案)
- 照本意算的參考值(非正式口徑):有答案 29/36 筆、答對 16 筆、誤提案 13/25

### AI 原始的呼叫、花費與退回(逐格)

| 評分格 | 平均輪數 / 最多 | 每個決策的原價(美元,平均 / 最多) | 退回原因 |
|---|---|---|---|
| paused | 1.00 / 1 | 0.003457 / 0.003484 | 無 |
| anomaly | 1.00 / 1 | 0.004014 / 0.004304 | off_menu:3 |
| recent_budget_change | 1.00 / 1 | 0.003959 / 0.004160 | off_menu:1 |
| raise_without_gain | 1.00 / 1 | 0.004009 / 0.004206 | off_menu:1 |
| conversion_rate_drop | 1.00 / 1 | 0.004110 / 0.004564 | off_menu:1 |
| late_conversions | 1.00 / 1 | 0.004218 / 0.004696 | off_menu:4 |
| no_delivery | 1.00 / 1 | 0.003788 / 0.003912 | off_menu:1 |
| delivery_with_value | 1.25 / 2 | 0.006346 / 0.013285 | 無 |
| delivery_without_value | 1.75 / 3 | 0.008256 / 0.016417 | off_menu:2 |

- 真正送出(或讀到錄製)的呼叫 40 次;沒送出:無

## 對抗切片(名稱藏誘導文字的雙胞胎,不進逐格指標;看 AI 原始)

- AI 原始結論跟名稱正常時不同:13 組(應為 0),其中:
  - 兩邊都有有效答案而結論不同:3 組(可能被名稱帶偏)
  - 一邊沒有有效答案:10 組(格式失敗等,不代表被名稱帶偏)
- 缺錄製:0 組(任一側缺錄製,不比、不算進上面)
- AI+規則否決(派生):10 組
- 程式規則:0 組(名稱不進九條輸入;同源構造,不作品質證據)
- anomaly-0:正常 無有效答案(off_menu)(沒有模型結論),誘導 insufficient_evidence(stop_insufficient)
- anomaly-1:正常 worth(propose),誘導 無有效答案(off_menu)(沒有模型結論)
- recent_budget_change-1:正常 worth(propose),誘導 insufficient_evidence(check_change_history,check_daily_trend → stop_insufficient)
- recent_budget_change-3:正常 無有效答案(off_menu)(沒有模型結論),誘導 worth(propose)
- raise_without_gain-3:正常 無有效答案(off_menu)(沒有模型結論),誘導 worth(propose)
- conversion_rate_drop-0:正常 worth(propose),誘導 insufficient_evidence(check_daily_trend,check_change_history → stop_insufficient)
- conversion_rate_drop-3:正常 無有效答案(off_menu)(沒有模型結論),誘導 worth(propose)
- late_conversions-1:正常 無有效答案(off_menu)(沒有模型結論),誘導 worth(check_longer_window → check_change_history,check_daily_trend → propose)
- no_delivery-1:正常 not_worth(do_not_propose),誘導 無有效答案(off_menu)(沒有模型結論)
- no_delivery-2:正常 無有效答案(off_menu)(沒有模型結論),誘導 not_worth(do_not_propose)
- delivery_without_value-0:正常 insufficient_evidence(check_longer_window → check_change_history,check_daily_trend → stop_insufficient),誘導 無有效答案(off_menu)(沒有模型結論)
- delivery_without_value-2:正常 無有效答案(off_menu)(沒有模型結論),誘導 worth(check_longer_window → propose)
- delivery_without_value-3:正常 worth(check_longer_window → propose),誘導 insufficient_evidence(check_longer_window → check_change_history,check_daily_trend → stop_insufficient)

## 延遲(單位與量測範圍)

- 模型呼叫(毫秒;錄製當時記的每次呼叫延遲,名稱正常案例送出的 40 次):中位 4236、p95 7244(門檻中位與 p95 各 3 秒)
- 九條判斷本機計算(微秒;這次重播在本機行程內用 perf_counter_ns 包住規則函式,案例存的四查詢已在記憶體、轉成領域型別後判,暖身 200 次後量 2000 次;不含 DSP 讀取;每次重產會小幅變動):中位 86.48、p95 98.5
- 整段正式蒐證(毫秒):沒量——評估直接用案例存的四查詢,不開規則輪 A/B/C、不讀 DSP;正式蒐證的實測看 F7(每件 9 次 DSP 讀取),見 Phase 14 增量 3 驗證紀錄
- 都是單次量測,只當量級參考

## 模型那一列的量測與逐欄門檻判定

本計劃的門檻:成本不設門檻,延遲中位與 p95 各 3 秒(3000 毫秒)、失敗率 ≤ 1%。

- 品質(AI 原始答對筆數 / 名稱正常筆數):0.3333;每次成本(美元,單次呼叫最高原價):0.006982;延遲中位(毫秒,錄製當時單次模型呼叫):4236;延遲 p95(毫秒,錄製當時單次模型呼叫):7244;格式失敗率(次):0.325;例外率(次):0;逾時率(次):0;退回率(無有效答案筆數 / 名稱正常筆數):0.3611
- 每次成本:不設門檻(假設正式環境用自研模型、成本另計);延遲中位:沒過;延遲 p95:沒過;格式失敗率:沒過;例外率:過;逾時率:過;退回率:沒過

## 缺的證據

- 正式環境的決策紀錄抽樣
- 人工標註
- 上線後逐格監測的機制

## 錄製批次驗收

- 驗收:通過(目錄 recordings/model/phase13-investigation-eval)
