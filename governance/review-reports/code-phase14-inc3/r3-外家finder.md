severity: clean

## 上輪修復驗收

- 百分比核對已加入 NFKC 正規化與多種寫法；千分號、萬分號會整句移除。唯讀 Python 呼叫確認「500﹪」「500٪」「百分之500」「500 percent」「500 pct」「500 個百分點」都被移除，而證據中的 10% 可用等值寫法保留。file: `src/rtb/modelclient.py:223`
- `InstrumentedEvidenceSource` 與 `dsp_evidence_source` 的實作已刪；規則輪改由 `rule_source` 逐端點記錄，測試也改走該入口。file: `src/rtb/analyzer/instrumented.py:85`
- 展示頁面摘要已改為按 `decided_by` 顯示新舊紀錄的決定來源。file: `docs/rtb-production-agent-demo-knowledge/Systems/展示頁面.md:20`
- `pyproject.toml` 已納入提交，AI 決策模組的匯入禁令改為只允許評估使用。file: `pyproject.toml:25`

## 正式路徑與固定席

runner 固定把 `rule_source` 和 `rule_round.decide` 傳入流程；`flow.advance` 已無 `ai_decide` 參數。評估則直接呼叫 `Judge`，遇到 `RuleContinue` 在評估內結算，不進正式流程。未找到模型回答通往正式提案的新路徑。file: `src/rtb/analyzer/runner.py:120`、`src/rtb/analyzer/flow.py:229`、`src/rtb/eval/investigation_eval.py:134`

| 固定席節點 | 判定 |
|---|---|
| Mock-DSP | 本輪未改寫入端的版本、冪等或交易處理；未見合約受損。 |
| 任務流程領域模型 | 提案白名單、證據新鮮度與終點狀態仍由原有領域及流程檢查把關。 |
| 分析行程流程與檢查點 | AI 步已撤，租約、分步提交及正式規則接線仍在。 |
| 正式九條判斷領域規則、確定性指標計算 | 正式定案仍呼叫九條規則；本輪沒有改動九條門檻或指標算法。file: `src/rtb/analyzer/rule_round.py:313` |
| 模型用戶端、評估與Jev決策點 | 模型呼叫留在說明、假說及評估；錄製帳本由共用函式在模式判定後選定。file: `src/rtb/modelclient.py:310` |
| 一鍵展示、展示頁面 | 分析端不帶模型決策入口；頁面改按紀錄來源顯示，舊紀錄不冒稱九條規則。 |
| 服務水準與燒損告警、追蹤檢視 | 變更涉及模型文字入口及邊界測試，未見告警算式或唯讀追蹤路徑被改壞。 |
| 共用行程基礎、執行迴圈、提案收件口 | HTTP 邊界、執行前檢查及收件冪等路徑未因本輪刪除 AI 分支而改變。 |

棧別效能表態與所讀程式相符：規則輪是同步、有逾時且每步讀取數固定；F7 的既有量測宣稱本席未重跑。

## 驗證限制

`pytest` 在載入測試前因唯讀環境沒有可用暫存目錄而退出，故本席沒有取得測試通過結果；錄製重播也未執行。唯讀檢查確認 runner 參數不含 AI 決策開關、流程簽章不含 `ai_decide`，並完成上述百分比輸入呼叫。所有本席啟動的命令均已結束。