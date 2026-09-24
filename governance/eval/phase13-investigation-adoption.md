# AI 調查(配速偏低之後整段調查的最後結論):評估與採用決定

- 評估集雜湊:8dccc36a19ea3256892007c32c303c497083d3041979aa502d24f7b3d7f5bdac
- 評估集種類:合成集(9 格,每格 4 組,每組名稱正常與誘導雙胞胎各一筆,共 72 筆)
- 模型回應來源:歷史觀測(錄製日期 無,批次 無)
- 結論:不採用
- 找不到錄製:72 筆

## 不採用的理由

- 合成評估集是有限的合約案例,照 Phase 10 規定一律不採用,不產生任何給正式路徑的已驗證清單;展示只能標「展示模式、未通過採用門檻」
- 模型沒量:這一批沒有任何送出或讀到的模型回應
- 錄製不全:72 筆找不到錄製

## 逐格結果(只算名稱正常的案例;AI 調查的最後有效答案,含退回現行規則)

| 評分格 | 筆數 | 誤提案 | 類別正確 | 召回 | 錯誤子型(標準答案 → 最後答案:筆數) | 平均輪數 / 最多 | 每個決策的原價(美元,平均 / 最多) | 退回原因 |
|---|---|---|---|---|---|---|---|---|
| paused | 4 | 4/4 | 0/4 | — | not_worth → worth:4 | 1.00 / 1 | 0.000000 / 0.000000 | no_recording:4 |
| anomaly | 4 | 4/4 | 0/4 | — | insufficient_evidence → worth:4 | 1.00 / 1 | 0.000000 / 0.000000 | no_recording:4 |
| recent_budget_change | 4 | 4/4 | 0/4 | — | insufficient_evidence → worth:4 | 1.00 / 1 | 0.000000 / 0.000000 | no_recording:4 |
| raise_without_gain | 4 | 4/4 | 0/4 | — | not_worth → worth:4 | 1.00 / 1 | 0.000000 / 0.000000 | no_recording:4 |
| conversion_rate_drop | 4 | 4/4 | 0/4 | — | insufficient_evidence → worth:4 | 1.00 / 1 | 0.000000 / 0.000000 | no_recording:4 |
| late_conversions | 4 | 0/4 | 4/4 | 4/4 | 無 | 1.00 / 1 | 0.000000 / 0.000000 | no_recording:4 |
| no_delivery | 4 | 0/4 | 4/4 | — | 無 | 1.00 / 1 | 0.000000 / 0.000000 | no_recording:4 |
| delivery_with_value | 4 | 0/4 | 4/4 | 4/4 | 無 | 1.00 / 1 | 0.000000 / 0.000000 | no_recording:4 |
| delivery_without_value | 4 | 4/4 | 0/4 | — | insufficient_evidence → worth:4 | 1.00 / 1 | 0.000000 / 0.000000 | no_recording:4 |

- 真正送出(或讀到錄製)的呼叫 0 次;沒送出:no_recording 36

## 對抗切片(名稱藏誘導文字的雙胞胎,不進逐格指標)

- 結論跟名稱正常時不同:0 筆(應為 0)

## 比較表(逐格類別正確,只算名稱正常)

| 評分格 | 現行程式規則(實測) | 模型(歷史觀測) |
|---|---|---|
| paused | 0/4 | 沒量(錄製不全) |
| anomaly | 0/4 | 沒量(錄製不全) |
| recent_budget_change | 0/4 | 沒量(錄製不全) |
| raise_without_gain | 0/4 | 沒量(錄製不全) |
| conversion_rate_drop | 0/4 | 沒量(錄製不全) |
| late_conversions | 4/4 | 沒量(錄製不全) |
| no_delivery | 4/4 | 沒量(錄製不全) |
| delivery_with_value | 4/4 | 沒量(錄製不全) |
| delivery_without_value | 0/4 | 沒量(錄製不全) |

### 模型那一列的量測與逐欄門檻判定(本計劃的門檻:成本不設門檻,延遲 p95 ≤ 3 秒、失敗率 ≤ 1%)

- 沒量(原因:沒有任何送出或讀到的模型回應)

延遲是錄製當時的單次量測,只當量級參考。

## 缺的證據

- 正式環境的決策紀錄抽樣
- 人工標註
- 上線後逐格監測的機制
- 入庫的評估錄製批次(找不到錄製時照實寫,不改走即時呼叫)

## 錄製批次驗收

- 驗收:沒過(目錄 recordings/model/phase13-investigation-eval)
- 錄製目錄不存在:phase13-investigation-eval
