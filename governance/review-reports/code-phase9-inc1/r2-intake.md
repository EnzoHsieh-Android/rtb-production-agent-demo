# code-phase9-inc1 第 2 輪收貨與重現紀錄(2026-09-24)

## 收貨
- 5 席收齊才判讀:regress(前輪驗收)、tests、arch、資安(sonnet),finder(Codex,沒撞到限額)。各席實驗都在 /tmp 副本;repo reflog 只有實作員的修正提交。
- 受審:b81f56b..a2d808e 全量(資安席)與 1d8a538..a2d808e 修正段(其他席)。
- 前輪 8 條:驗收席逐條判已修;測試席逐條做變異,全紅。
- quote-check:regress 席 9 句有 1 句錨不到(「所以只有行程當機才會少記」):原文在 src/rtb/executor/execution.py:456 跨行斷開,快照第 1876 行「所以只有行程當機才會」後接下一行「少記」,機械重現 HIT,採信。其餘四席全數錨定。
- 共 7 條發現,合併成 4 件事。arch-1 低共識,派 Codex 辯方(r2-veto-codex.md)。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| arch-2 | 讀 src/rtb/executor/execution.py 的 flush_calls:撞忙 return,清單無上限;runner 的 busy_streak 看不到 | HIT,折入 |
| regress-1 | 同 arch-2,另指收尾仍忙時無聲丟掉;runner 說明與 execution 註解互相矛盾 | HIT,同一件,折入 |
| x1-1 | 同 arch-2,另指提交後刪清單前被打斷會重寫 | HIT,同一件,折入(上限、背壓、補寫失敗算忙碌計數、收尾印少記列數;打斷只可能是當機,照實寫進筆記) |
| sec-1 | 讀 src/rtb/ops/trace.py:348 hashes 以(任務、修訂、冪等鍵)為鍵;冪等鍵不含決策時間,內容雜湊含 | HIT,折入 |
| x1-2 | 同 sec-1 | HIT,同一件,折入(呼叫紀錄當下存內容雜湊,不回推) |
| tests-1 | 讀 tests/ops/test_ops_boundaries.py 動態禁用清單沒有 eval、exec、compile | HIT,折入 |
| arch-1 | DspPort 每次傳 on_call 被判第三種做法。辯方 Codex 查 src/rtb/analyzer/dsp_client.py:112-139 與 src/rtb/executor/execution.py:131-154:兩邊都是每次呼叫顯式傳身分,只差形狀;漏傳由必填參數擋下;改成建構時綁回呼在共用用戶端的多工作者下反而寫錯庫(tests/executor/test_multi_worker.py:35-40)。重現不到錯誤行為 | MISS,駁回(辯方判降為形狀偏好) |

## 處置
- 折入 6(合併成 3 件),駁回 1(arch-1),放行 0。修法交回增量 1 實作員。
