severity: major

## F1 交易內補抓的總曝險核可沒有縮短能力憑證

severity: major

blocking: 是 — 核可過期後，DSP 仍可能接受壽命較長的能力憑證並寫入，破壞既有人工核可合約 [S376]。

引句:「live[AGGREGATE] = found」

當鎖外預判不需要總曝險核可、鎖內重算才發現需要時，修正會讀取最新核可並加入 `live`，卻沒有重新簽發或縮短已生成的 `signed`。後續仍把原本較長的 `signed.expires_at` 寫入嘗試並把原 token 送往 DSP。file: `src/rtb/executor/execution.py:739` file: `src/rtb/executor/execution.py:680` file: `src/rtb/executor/execution.py:430`

這與既有合約「能力憑證不能活得比它使用的核可更久」直接衝突。file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md:337`

純記憶體重現令預判額度為 0、交易內額度為 90，門檻 100、增加 20；核可 30 秒到期，原能力憑證 120 秒到期。輸出：

```text
result= executed
approval_exp= 1790079630
capability_exp= 1790079720
approval_uses= [('aggregate_limit_reached',)]
```

系統一方面記錄總曝險核可已被使用，另一方面送出的能力憑證卻比該核可多活 90 秒。應在交易內補抓核可後，於呼叫 DSP 前以該核可到期時間重新簽發，或採等價方式確保憑證期限不超過所有實際使用的核可。

其餘第 2 輪修正，包括先處理被取代的核可、過期比例核可擋下、不可讀死信拒絕重放，以及 [S511] 兩條重跑路徑保留新原因，未發現另一個擋合併問題。
