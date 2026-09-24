# code-phase10-inc2 第 1 輪收貨與重現紀錄(2026-09-24)

## 收貨
- 3 席收齊:eval(評估方法)、arch(sonnet),finder(Codex;唯讀沙盒全套測試跑不起來,改看評估子集與唯讀實驗)。受審 fa4559c..65150b1 的 src 與 tests。quote-check 三份全數錨定。
- eval 席手算逐格計分與 Wilson 下界、重跑 record 與報告數字一致、重跑生成器逐值相同;它的全套跑有 1 支 F7 端到端計時測試紅(與評估無關,當時多個工作樹同時在跑全套,機器負載高;照使用者對 F7 的裁定只記錄、不改)。
- 共 10 條(2 minor),合併成 9 件。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| x1-1 | 讀 src/rtb/eval/scoring.py:88 與 src/rtb/eval/adoption.py:131:正式與合成只靠 Report.kind 字串區分,呼叫端可把合成集結果標成正式 | HIT,折入(正式報告改獨立型別、必帶隱藏集版本、雜湊、抽樣與標註出處、候選版本) |
| x1-2 | 讀 src/rtb/eval/adoption.py:44、:110 Measure 不擋 NaN;nan > 門檻恆假 | HIT,折入 |
| x1-3 | 讀 src/rtb/eval/adoption.py:59-134 比較表一列套全部格、中位延遲沒門檻;計劃寫比較表逐格 | HIT,折入 |
| x1-4 | 讀 src/rtb/eval/scoring.py:49、:75 正式報告 lower_bound 固定空、非值得加格只算類別正確率下界 | HIT,折入(每項指標存分子分母點估計與下界,採用函式只讀報告存的下界) |
| x1-5 | 讀 src/rtb/eval/generator.py:121 預算變體後又依新預算重抽花費 | HIT,折入 |
| x1-6 | 讀 src/rtb/eval/record.py:85 無條件輸出不採用理由 | HIT,折入 |
| x1-7 | 讀 tests/eval/test_evaluation.py:238-252 只掃 Import 與 ImportFrom,__import__ 與 importlib.import_module 呼叫看不到 | HIT,折入 |
| eval-2 | 同 x1-7 | HIT,同一件,折入 |
| eval-1 | 讀 src/rtb/eval/generator.py:89 點擊多於曝光的故障另把轉換歸零,不是單一故障 | HIT,折入(minor) |
| arch-1 | 讀 src/rtb/eval/record.py main() 沒有 run(argv) 與結束代碼;其他 8 支命令列都是兩層形狀 | HIT,折入 |

## 處置
- 全部折入,放行 0、駁回 0。修法交增量 2 實作員(另一個工作階段),同時合進增量 1 最終版與使用者對原意對照的確認。
