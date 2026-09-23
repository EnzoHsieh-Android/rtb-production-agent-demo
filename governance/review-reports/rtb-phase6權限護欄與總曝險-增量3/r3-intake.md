# 第 3 輪設計審收貨與重現紀錄(2026-09-23)

## 收貨
- 四席收齊才動計劃:s1 前輪修正驗收、s2 處理待核可與順序、sarch 架構對齊、x1 外家 Codex(沒有撞到用量限額)。受審版本:第 3 版(main 的 018a60d)。共 9 條:8 條 major、1 條 minor,沒有阻擋級。這是最後一輪。
- report-normalize:四份都已是正規化格式;quote-check 四份全數錨定。sarch 的佐證用絕對路徑加行號範圍,refcheck 抽不到,編排者逐條手動開檔核對(見重現表)。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| s1-1 | 讀 tests/executor/test_guardrails.py:108 直接呼叫 precheck;第 3 版被改寫清單沒有 S402 | HIT,採信,折入(比例判斷搬進護欄模組,S402 改呼叫它;被改寫清單補上) |
| s2-2 | 同 s1-1 | HIT,跟 s1-1 同一件,折入 |
| s2-1 | 讀 src/rtb/executor/capability_signer.py:93 只讀 tenants,頂層欄位沒有傳出路徑 | HIT,採信,折入(政策版本改成共用程式常數,不放設定檔) |
| sarch-2 | 同 s2-1,另讀 :104-106 既有欄位分缺值與不合法 | HIT,跟 s2-1 同一件,折入(欄位不存在了,驗證問題一併消失) |
| x1-2 | 讀 src/rtb/analyzer/policy.py:27 政策版本是程式常數,設定檔要人同步 | HIT,採信,折入(共用常數;剩下分開重啟的落差寫進實務隱患並附回頭條件) |
| sarch-1 | 讀 src/rtb/executor/execution.py:290-292 比例常數在執行迴圈模組;src/rtb/executor/inbox_server.py 不匯入執行迴圈 | HIT,採信,折入(護欄模組放比例常數與判斷;核可模組放範圍指紋,兩邊共用;S379) |
| sarch-3 | 讀 src/rtb/executor/capability_signer.py:140-142 回傳攤平兩欄 | HIT,採信,折入(回傳整個租戶物件;S378) |
| x1-1 | 讀 src/rtb/executor/execution.py:491 開始一筆在交易內判、:743 重送在交易外呼叫 DSP;src/rtb/executor/capability_signer.py:169 憑證有效期與核可無關 | HIT,採信,折入(憑證到期封頂在核可到期;重送重判;S376、S377) |
| x1-3 | 第 3 版寫「先取簽發時間最新」,時間相同會並列 | HIT,採信,折入(改取核可表寫入順序最新;S371 改寫) |
| main-1 | 編排者核對 x1-1 時讀 src/rtb/executor/execution.py:731 重送只跑 precheck,比例搬走後重送不判比例 | HIT,編排者自己發現,折入(重送走同一套;S377) |

## 處置
- 折入 9(合併成 6 件事),另折入編排者自己發現的 1 件;放行 0、駁回 0。使用者裁定沒有動。
- 政策版本從「設定檔宣告」改成「共用程式常數」是設計改向,不違反任何使用者裁定;剩下的分開重啟落差照實寫進實務隱患。
- 設計審跑滿 3 輪,第 4 版的折入由實作後的代碼審覆核。
