severity: major

## 發現 1: 截斷歷史的摘要仍可通過不可能的操作計數

severity: major  
blocking: 是

引句:「and counts["total_budget_changes"] + counts["total_pauses"]」

`_summary_agrees()` 只要求兩種操作的合計小於等於 `total_operations`。但操作型別只有 `update_budget` 與 `pause_campaign`（file: `src/rtb/domain/proposal.py:43`），因此完整集合的兩種計數必須**等於**總數。現在可把 60 筆操作描述成 1 筆預算變更、49 筆暫停，讀取層仍接受；截斷歷史收據隨即少報 10 筆。這是第 1 輪「摘要與歷史列互相矛盾」修正後留下的缺口，違反 [S1422] 的完整集合計數契約。

最小重現（唯讀，未建立檔案）：

```sh
PYTHONPATH=src .venv/bin/python -c '
from datetime import datetime, UTC
from rtb.analyzer.dsp_client import check_history
from rtb.analyzer.investigation import receipt_payload, QueryOption
now = datetime(2026, 9, 26, 12, tzinfo=UTC)
rows = [dict(operation_id=i, action="pause_campaign", version_after=i+1,
             received_at=now.isoformat(), committed_at=now.isoformat(),
             idempotency_key=f"k{i}") for i in range(1, 51)]
rows[-1]["action"] = "update_budget"
summary = dict(total_operations=60, total_budget_changes=1, total_pauses=49,
               budget_changes_7d=1, budget_changes_last_3d=1,
               has_recent_budget_change=True)
checked = check_history(dict(history=rows, summary=summary, truncated=True))
print("accepted", checked is not None)
receipt = receipt_payload(QueryOption.CHECK_CHANGE_HISTORY, checked, now)
print("operations", summary["total_operations"],
      "classified", int(receipt["budget_changes"]) + int(receipt["pauses"]))
'
```

實際輸出：

```text
accepted True
operations 60 classified 50
```

預期 `check_history()` 對此摘要回 `None`、記為 `invalid`。

## 第 1 輪發現驗收

逐項對照九席共 30 條：固定時鐘、讀取不寫與鎖內可讀、七日全缺回 404、整數加總、舊 REAL 遷移、舊加額前值缺漏、金額白名單、負數三態、歷史快照與未截斷形狀、評估時間戳等，都已在本輪差異中找到對應修補。歷史摘要的下界與大小關係已有檢查，但上述完整計數矛盾仍會通過。跨 HTTP 讀取恰逢 UTC 午夜而保守記 `invalid`，屬計劃明列的接受偏離。

72 筆案例與生成器相符；收據及 `SYSTEM_PROMPT` 雜湊符合差異中的固定值。本唯讀沙箱沒有可用暫存目錄，`pytest` 在啟動擷取時即失敗，因此未把本席未跑的測試記為通過。

## 圖譜固定席

- **Mock-DSP**：同鍵至多套用一次、寫入全成全敗、版本衝突及逾時對帳的合約未見本差異破壞；新增日桶持久化仍在寫入交易內。
- **任務流程領域模型**：提案解析、證據新鮮度、終點狀態及領域匯入邊界未見破壞；金額檢查改為領域共用函式。
- **分析行程流程與檢查點**：未改推進、租約或不可信廣告文字的處理；讀取層的歷史計數缺口會影響交給後續判斷的收據。
- **正式九條判斷領域規則**：本差異未改判斷順序或門檻；字串金額驗值已接共用判準。
- **確定性指標計算**：缺值原因與收據格式未見破壞；72 筆收據雜湊已核對。
- **評估與 Jev 決策點**：72 筆重新生成相符，標準答案與提示雜湊核對相符。
- **提案收件口**：未改收件、修訂或死信重放路徑，相關合約未受本差異直接影響。
- **共用行程基礎**：未改 HTTP 用戶端標頭、轉址或伺服器故障注入入口，相關合約未見破壞。
- **執行迴圈**：未改同鍵對帳及工作者流程；DSP 新增的日桶持久化在原寫入交易內，未見重複套用路徑。