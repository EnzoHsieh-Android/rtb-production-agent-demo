# code-phase9-integration 第 1 輪收貨與重現紀錄(2026-09-24)

## 收貨
- 兩席收齊:merge(衝突處置正確性)、arch(sonnet)。材料是三次合併(540ef0a、6742fb9、96415b1)的 git show --remerge-diff,只含 src 與 tests,共 320 行。quote-check 兩份全數錨定。
- arch 席 clean:三支維運命令列共用同一份解析器與結束代碼、時區參數型別只有一份、builtins 判斷在既有掃描函式裡、守衛集合照實更新。
- merge 席:實演稽核金鑰只在測試、結束代碼表與索引清單一致;1 條 major。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| merge-1 | 讀 tests/ops/test_ops_boundaries.py:104-112:ast.Attribute 只在 node.value 字面上是 builtins 或 __builtins__ 時才把 eval 等算違規;import builtins as b 後 b.eval 不算,合併前的版本(任何物件上的這幾個屬性都算)抓得到 | HIT,折入(維運套件一律不准匯入或引用 builtins 模組,別名路徑因此擋在匯入那一步) |

## 處置
- 折入 1,放行 0、駁回 0。修法交整合實作員。
