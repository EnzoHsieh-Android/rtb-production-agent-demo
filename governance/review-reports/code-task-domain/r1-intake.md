# code-task-domain r1 intake(收貨、重現與處置留痕)

## 收貨三道
- quote-check:兩席全數錨定;refcheck:全部引用存在。
- 席位:t1 通才單一審查員、tarch 架構對齊(標準強度;外家席依編制「退同門並留痕」,本輪沒派外家)。

## 編排者機械重現(修復前的程式碼)
| 宣稱 | 命令 | 結果 |
|---|---|---|
| t1f1:action_type 為 list 或 dict 時 parse_proposal 丟 TypeError | python3 直接呼叫 parse_proposal | HIT:TypeError |
| t1f2:極深巢狀 dict 讓 json.dumps 丟 RecursionError | 巢狀 100000 層放進 risk_summary | HIT:RecursionError |
| t1f3:too_large 計的是字元數不是位元組 | 席位重現輸出完整(6000 個中文字得 36366) | 採信;修復後改為 UTF-8 位元組估算,並有測試 |
| t1f4:max_age_seconds=10**400 丟 OverflowError、utcoffset 為 None 的 tzinfo 被當成有時區 | 席位以 e.py、f.py 重現輸出完整 | 採信;修復後有測試,改壞會紅 |
| t1f5:reason_codes 與 evidence_refs 允許重複、risk_summary 允許控制字元與孤立 surrogate | 席位實測輸出完整 | 採信;修復後有測試 |
| t1f6、taf4:TRANSITIONS 是可變 dict,改寫後「終點有出路」 | 席位 g.py 輸出 True | 採信;修復後改為唯讀對映,有測試 |
| taf1:輔助函式與識別碼格式重複多份 | 席位 grep 輸出完整 | 採信;領域層抽出共用模組,DSP 是外部系統模擬器,刻意不依賴領域層,理由寫進圖譜 |
| taf2:Proposal 與 ParsedProposal 不自我驗證 | 席位以直接建構重現 | 採信;修復後兩者都在建構時驗證,有測試 |
| taf3:FIELDS 未使用、CHECKS 的隱含前提未寫明 | 席位 grep 輸出完整 | 採信;刪除 FIELDS,表旁補註解 |

refuted:none。

## 處置
- 10 條全部折入,沒有放行(t1 為重要等級,規定不得放行)。
- 折入方式:
  - proposal.py 重寫大小量測:不遞迴、有深度與位元組上限的迭代式走訪,取代 json.dumps;
  - 新增確定性隨機亂測(3000 次、每次塞垃圾值進 1 到 4 個欄位)驗證「絕不丟例外且結果一致」;
  - 341 條測試;新增防護做 13 個變異檢查,全被抓到。
- 我自己在提交前標的一題「張力」(先序列化整份輸入才量大小)被 t1 證實不只是效能問題,而是會崩潰;修復同時消掉那個張力。
