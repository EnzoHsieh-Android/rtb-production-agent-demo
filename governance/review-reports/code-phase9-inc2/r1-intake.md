# code-phase9-inc2 第 1 輪收貨與重現紀錄(2026-09-24)

## 收貨
- 7 席收齊才判讀:formula、reads、tests、規格符合、架構對齊、資安(sonnet),finder(Codex,沒撞到限額)。受審 40b2a01..c5a34da(增量 2 實作,另開唯讀工作樹 /Users/enzo/rtb-p9i2,不動實作員工作樹)。
- quote-check:六份有發現的報告全數錨定;資安席 clean,沒有引句。
- 共 15 條(3 條 minor),合併成 11 件事。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| formula-1 | 公式席用 tests/ops/rows.py 實跑:窗一死信後窗外重放成功,重查窗一 execution_seconds 樣本從 540 秒變無樣本;讀 src/rtb/ops/metrics.py:580-597 用 finals 全域最後終點 | HIT,折入 |
| x1-1 | 同 formula-1 | HIT,同一件,折入(執行端處理改以窗內實際終點事件分段) |
| x1-2 | 讀 src/rtb/ops/metrics.py:707 版本值域把 finals(可在窗外)也放進 seen | HIT,折入(值域只收窗內) |
| formula-3 | 讀 src/rtb/ops/metrics.py:244-246 政策版本跟查詢當下的常數比 | HIT,折入 |
| formula-2 | 公式席實跑 run() 無時區時間丟未捕捉 ValueError | HIT,折入 |
| reads-1 | 讀 src/rtb/analyzer/task_store.py 的 _iso 沒有拒收無時區時間;讀取席實測窗口位移 8 小時漏資料 | HIT,同一件,折入 |
| x1-3 | 同 formula-2,另指混用有無時區丟 TypeError | HIT,同一件,折入 |
| spec-1 | 讀 src/rtb/ops/metrics.py:725-745 整份窗內統計都進重讀迴圈;規格只給端到端 | HIT,折入(照規格收斂到端到端) |
| tests-1 | 測試席在 /tmp 副本改窗界 < 為 <=,609 支全綠 | HIT,折入 |
| tests-2 | 測試席拿掉鏈尾檢查,[S641] 測試仍綠 | HIT,折入 |
| tests-3 | 延遲範例耗時相同取最新沒有測試資料 | HIT,折入(minor,本輪有 major,照規矩不放行) |
| arch-2 | 讀 src/rtb/executor/observability.py:81-116 停下紀錄同提案同種類去重;指標從生命週期事件逐次數。兩者數的東西不同(事件次數與提案數) | HIT,折入(兩邊定義寫明,指標名標明是事件次數) |
| arch-3 | 讀 src/rtb/analyzer/task_store.py 端點型別錯丟 TypeError,執行端同類檢查丟 ValueError | HIT,折入(minor,改成 ValueError) |
| reads-2 | 讀 src/rtb/sqlitekit.py:107 missing_schema 不查索引;唯讀開舊庫不建索引 | HIT,折入(minor,缺新索引視同沒升級) |
| arch-1 | 版本衝突率沒用 attempt_store.version_conflict_count。規格 [S645] 經增量 2 設計審第 2 輪兩席明定「完全從 DSP 呼叫紀錄算」,理由是分子分母不同表會在窗界超過百分之百;既有計數不收時間窗。重現不到錯誤行為,是照規格 | MISS,駁回(照規格 [S645]) |

## 處置
- 折入 14(合併成 10 件),駁回 1(arch-1),放行 0。修法交給新派的增量 2 修正實作員(在本工作樹開分支,不動增量 3 實作員的工作樹)。
