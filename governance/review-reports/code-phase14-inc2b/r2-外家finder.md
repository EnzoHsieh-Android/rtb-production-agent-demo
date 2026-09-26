severity: minor

## 第 1 輪相關修正驗收

已對照修訂 diff：AI 答 `propose` 後改由 A/B/C 規則輪複查；暫停與異常在模型前結案；C 步在查詢後重讀現況；政策升版後變動重來計數歸零；進度改由已提交列純函式重算；歷史回應核對廣告編號；毀損的規則步驟列轉為失敗。相關測試也已加入。第 1 輪指出的測試預期自我引用及 F6 接續任務正路缺口，亦有對應修改。本輪未發現這些修正仍會造成誤提案。

## 發現 1：跨 UTC 日的逐日證據若另有壞列，跨日原因被遮蔽

severity: minor  
blocking: 否

引句:「if any(_invalid_daily_row(row) for row in evidence.daily.rows):」

新增的 `_invalid_query()` 在 `_daily_decision()` 檢查 `read_at` 之前先檢查壞列。逐日資料讀取日與決策日不同、且其中一列點擊為負數時，結果是 `invalid_row_value`，而非計劃 [S1406] 指定的 `day_boundary`。兩者都不會提案，但永久規則事件會記錯診斷。位置：`src/rtb/domain/nine_rules.py:395`；跨日判斷在 `src/rtb/domain/nine_rules.py:336`。

唯讀重現命令：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -B -c 'from dataclasses import replace; from datetime import timedelta; from tests.domain.test_nine_rules import _worth,_evidence,NOW; from rtb.domain import nine_rules as r; e=_evidence(); d=r.DailyTrend((replace(e.daily.rows[0], clicks=-1),*e.daily.rows[1:]), read_at=NOW-timedelta(days=1)); x=r.decide(_worth(), _evidence(daily=d), NOW); print(x.reason.value, x.query.value)'
```

輸出：`invalid_row_value check_daily_trend`。應明定並實作跨日與壞列同時存在時的診斷優先序。

## 發現 2：修正段落與仍標為現況的舊說明互相矛盾

severity: minor  
blocking: 否

引句:「AI 答 `propose` 回 `RuleContinue` 開規則輪,九條也判值得加才由規則輪建提案,否則否決」

新增說明正確描述了目前流程，但同一篇系統筆記仍稱「AI 自己答 propose 仍直接建提案」，計劃的增量 2b 摘要也仍如此宣稱；`_fallback()` 註解仍稱規則否決屬增量 3。這些句子沒有標成已被取代，下一輪若據此修改送件路徑，會重開第 1 輪已修的誤提案洞。對照：`docs/rtb-production-agent-demo-knowledge/Systems/分析行程流程與檢查點.md:307`、`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase14正式規則照九條判斷_計劃.md:156`、`src/rtb/analyzer/ai_judge.py:194`。應直接更正或明確標示這些舊句已失效。

## 圖譜固定席

| 節點 | 判定 |
|---|---|
| Mock-DSP | 歷史讀取多帶廣告編號；寫入冪等、原子性、逾時及版本合約未受此 diff 改動。 |
| 一鍵展示 | 觀察器接上 AI 提案後的規則輪；本輪未見展示合約的新破壞。 |
| 分析行程流程與檢查點 | 租約、序號與已存提案快照仍沿原入口；發現 2 是本節點的現況說明衝突。 |
| 正式九條判斷領域規則 | 不合格資料仍擋提案；發現 1 是跨日診斷優先序偏離。 |
| 評估與Jev決策點 | 原始 AI 答案與正式否決路徑分開，缺四查詢標註已補；未見此 diff 破壞評估入口。 |
| 任務流程領域模型 | 此 diff 未放寬提案解析、新鮮度、終點狀態或領域匯入限制。 |
| 模型用戶端 | 正式 runner 未啟用 `raw_replay`；模型入口與花費閘道未改。 |
| 共用行程基礎 | 未改故障標頭、網路邊界或重新導向處理。 |
| 執行迴圈 | 執行端守衛未改；F4/F6 測試期望改為近期調額後不再加額。 |
| 提案收件口 | 收件、修訂與死信重放邏輯未改。 |
| 確定性指標計算 | 新檢查使用精確分數；缺值不當成零的合約未見改動。 |

**驗證限制：**上面的領域重現已執行。pytest 在本次唯讀沙箱啟動時因無可用暫存目錄而失敗，未能執行測試子集；未使用真帳本或啟動展示伺服器。