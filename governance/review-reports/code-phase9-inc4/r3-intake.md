# code-phase9-inc4-std 第 3 輪收貨與重現紀錄(2026-09-24,standard 上限)

## 收貨
- 兩席收齊:regress、arch(sonnet)。受審 6982c3b..cc7eb88(測試與筆記)。quote-check 兩份全數錨定。
- 前輪 4 條:驗收席逐條讀碼並在 /tmp 實測,全數已修;全套 1682 綠;守衛搬家逐行比對邏輯與常數未變、配方與斷言沒掉。
- 共 4 條新發現,全部 major。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| regress-1 | 讀 tests/executor/write_scan.py:170-180 _percent 字典分支:任一值解不出就整個模板所有佔位正規化成 %s,已解出的常數表名也被抹掉;.format 是逐欄處理 | HIT,折入(比照 .format 逐欄) |
| regress-2 | 讀 tests/executor/write_scan.py:164 format(value, spec) 對字串套精度會截斷("{:.3}" 把 operations 截成 ope) | HIT,折入(規格含精度就退回裸佔位) |
| arch-1 | 讀 tests/executor/write_scan.py:1-4 檔首只列稽核守衛;tests/executor/test_dead_letter.py:34、455、496 仍直接用 reconstructed_strings | HIT,折入 |
| arch-2 | 讀 Systems/稽核表只增不改守衛.md:31 第 1 輪舊條目被改寫成第 2 輪才有的落腳點,與 :32 第 2 輪條目互斥 | HIT,折入(舊條目還原成第 1 輪當時的事實) |

## 處置
- 全部折入,放行 0、駁回 0。已達 standard 上限,不開第 4 輪;修正交回增量 4 實作員,修正差異由編排者逐行讀過再合併,收尾報告照實說明未經另一組獨立審查席。
