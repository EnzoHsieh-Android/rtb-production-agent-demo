# code-phase9-inc4-std 第 2 輪收貨與重現紀錄(2026-09-24)

## 收貨
- 兩席收齊才判讀:regress(前輪驗收)、arch(sonnet)。受審 6982c3b..084ef22(測試與筆記;F7 Issue 那個提交不在範圍)。quote-check 兩份全數錨定。
- 前輪 4 條:驗收席判原探針都已修、全套 1666 綠、死信守衛只變嚴、Clock 預設行為不變。
- 共 4 條新發現,全部 major。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| regress-1 | 讀 tests/executor/write_scan.py:157-161:+ 兩側各自先壓空白再相加,"UPDATE OR REPLACE " + "dead_letters ..." 拼回成黏在一起的字串;Phase 8 舊版同樣中招 | HIT,折入 |
| regress-2 | 讀 tests/executor/write_scan.py:162-173:.format 帶具名引數整個節點回 None;% 字典保守回帶 %(table)s 的模板;動態補欄位規則只認裸 {} 與 %s | HIT,折入 |
| arch-1 | 讀 tests/executor/write_scan.py:1-2 檔首只宣告兩種讀法,197-247 卻放了稽核政策;Systems/稽核表只增不改守衛 寫明政策歸 test_audit_tables.py | HIT,折入(政策搬回守衛測試檔,write_scan 只留通用讀法) |
| arch-2 | 讀 tests/executor/test_audit_tables.py 模組說明新句「共用同一套判定」與第 6 行舊句「跟 Phase 8 那支不同的是比法」互斥 | HIT,折入 |

## 處置
- 全部折入,放行 0、駁回 0。修法交回增量 4 實作員。第 3 輪是 standard 上限。
