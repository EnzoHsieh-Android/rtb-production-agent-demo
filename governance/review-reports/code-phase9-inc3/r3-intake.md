# code-phase9-inc3 第 3 輪收貨與重現紀錄(2026-09-24,代碼審上限)

## 收貨
- 4 席收齊:regress、arch、資安(sonnet),finder(Codex;沙盒跑不了 pytest,讀碼與直譯器計算)。受審 c5a34da..83093e6 全量與 b34055f..83093e6 修正段。
- 前輪 3 件:驗收席逐條讀碼判已修(例外分類不漏接也不吞、8 優先於 5 有同時成立的測試、專用標頭分離、base64url 邊界)。arch、資安兩席 clean。
- quote-check:有發現的三份全數錨定;arch 席 clean 無引句。
- 共 3 條:2 major、1 minor,合併成 2 件事。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| x1-1 | 讀 src/rtb/ops/side_effects.py:282、:312 接住 DspUnreadable 只回 missing,原因丟掉;src/rtb/ops/slo.py stderr 只印通用說明 | HIT,折入 |
| regress-1 | 讀 src/rtb/ops/slo.py:239 evaluate 的 except DspUnreadable 在現行六條指標下接不到,error 恆為空 | HIT,同一件,折入(minor;原因帶進 Tally 傳到 error,evaluate 那層註明是未來防線) |
| x1-2 | 讀 src/rtb/capabilitykit.py read_key 只有 32 位元組下限;超長金鑰 base64url 後超過 CPython 單行標頭 65536 位元組上限,DSP 回 431 | HIT,折入(代使用者裁定上限 1024 位元組) |

## 處置
- 全部折入,放行 0、駁回 0。已達代碼審上限,不開第 4 輪;修正交回增量 3 修正實作員,修正差異由編排者逐行讀過再收,收尾報告照實說明未經另一組獨立審查席。
