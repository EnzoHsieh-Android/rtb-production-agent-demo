# code-phase10-inc2 第 2 輪收貨與重現紀錄(2026-09-24)

## 收貨
- 3 席收齊:regress、arch(sonnet),finder(Codex;唯讀沙盒全套跑不起來,評估子集 20 過,Wilson 邊界值手算核對正確)。受審 e76be3d..8a3a542 修正段(全量 6308080..cce84fd)。quote-check 三份全數錨定。
- regress 席 clean:第 1 輪 9 件逐條讀碼已修;在 /tmp 做 13 道變異(含實作員 20 道以外 2 道)全紅;合入增量 1 最終版的 ValidatedCells 用法正確;報告數字重跑逐字相同(延遲兩數字抖動)。實作員自承這輪先寫實作後寫測試,以變異補證,regress 席的獨立變異可作為第二份證據。
- 共 6 條,合併成 6 件。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| x1-1 | 讀 src/rtb/eval/scoring.py:112、:195:production_report 公開、不檢查案例有沒有經過候選,也不驗雜湊格式;全部走現行規則的計分結果也能簽成正式報告 | HIT,折入(每格至少要有經候選的案例,雜湊要是 64 位十六進位) |
| x1-2 | 讀 src/rtb/eval/adoption.py:79、:185:依 mapping 鍵取列、不比 row.cell | HIT,折入 |
| x1-3 | 讀 src/rtb/eval/scoring.py:102:candidate_predates_disclosure 不驗型別,字串 "false" 為真 | HIT,折入 |
| x1-4 | 讀 src/rtb/eval/adoption.py:25-31 品質門檻標「暫用、待使用者覆核」卻已生效 | HIT,處置:使用者 2026-09-24 本人裁定這組品質門檻(0.95/73、0.80/16),改標使用者裁定;成本延遲失敗率門檻照舊待接上第一個候選時裁定 |
| x1-5 | 讀 tests/eval/test_evaluation.py:349-368 動態匯入只認字面常數參數;別名賦值、fromlist、字串串接漏掉 | HIT,折入(受保護五層直接禁止 __import__ 與 importlib) |
| arch-1 | 讀 src/rtb/eval/adoption.py:45-47 重寫 is_plain_number 的判準,沒接 OverflowError | HIT,折入 |

## 處置
- 全部處置,放行 0、駁回 0。第 3 輪是 standard 上限。
