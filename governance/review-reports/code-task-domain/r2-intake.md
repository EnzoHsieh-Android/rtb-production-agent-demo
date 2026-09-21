# code-task-domain r2 intake(收貨、重現與處置留痕)

## 收貨三道
- quote-check 與 refcheck:兩席全數錨定、引用全部存在。
- 這一輪是驗收輪,只審第一輪修復的差異(r2-snapshot.patch,709 行)。席位:u1 修復差異審查員(做了深度、寬度、共享結構、循環、超大整數、時間極端值等實驗)、uarch 架構對齊。

## 編排者機械重現與驗證
| 宣稱 | 命令 | 結果 |
|---|---|---|
| u1f1:刻意會丟例外的自訂 dict 或 str 子類別讓 parse_proposal 丟 RuntimeError | 修復前用 ExplodingDict、ExplodingStr 呼叫(新增測試,先紅) | HIT:測試在修復前失敗;修復後回 unexpected_failure 錯誤 |
| uarch-f1:「非布林數字」判斷在 metrics 與 evidence 各一份 | 席位 grep 輸出完整 | 採信;抽成 _checks.is_plain_number,兩處都改用,有變異檢查 |
| uarch-f2:_children 用哨兵物件繞路 | 席位指出行號 | 採信;改成在走訪前直接檢查鍵,有測試(巢狀非字串鍵) |
| uarch-f3:Proposal 自我驗證風格與同層不一致、匯入時檢查與測試重複 | 席位指出行號 | 採信;補註解說明重用解析規則的理由;匯入時檢查保留並寫進圖譜(有意雙重守) |

refuted:none。

## 處置
- 4 條全部折入,沒有放行。
- 折入方式:程式修正加測試(343 條);新增防護做 3 個變異檢查,全被抓到;u1 對其餘項目(深度邊界、共享子結構不會指數爆炸、寬而淺結構在 0.03 秒內被拒、位元組估算不低估、Proposal 可 replace 與 copy)的逐項驗證結果都沒有 finding。
