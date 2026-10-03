---
type: verification
status: pass
date: 2026-09-22
valid_under:
  - Python 3.14.6 與 sqlite3 3.53.3、pytest 9.1.1、ruff 0.16.8,macOS 本機;程式版本為提交 45af951;Mock DSP 只綁定本機回送位址且以獨立行程執行;只驗 DSP 端與指標計算,agent 端還不存在
  - "2026-10-03 補:Phase 2 第一次接上 DSP 客戶端後,分析端連不到故障注入與不匯入 DSP 內部模組兩條已由 tests/analyzer/test_boundaries.py 補驗(Phase 2 計劃 S52、S53),隨 [[Verification/Phase2驗收紀錄]] 的 tests/analyzer 重跑通過;改動之後的全套最近一次記在驗收紀錄的是 [[Verification/Phase14增量4驗證紀錄]](2026-09-27,3360 過、1 略過);之後的改動(Phase 15 等)只有 CI 與各自提交的測試,沒有驗收紀錄"
revalidate_when:
  - 改動 src/rtb/dsp 或 src/rtb/domain 時重跑全套
  - 升級 Python、SQLite、pytest 或 ruff 的主版本時重驗
tags:
  - type/verification
  - status/pass
plan_refs:
  - "[[Projects/RTB_Agent_Phase0架構]]"
decision_refs_ai:
  - "Projects/RTB_Agent_Phase0架構.md#d11"
  - "Projects/RTB_Agent_Phase0架構.md#d10"
system_refs:
  - "[[Systems/確定性指標計算]]"
  - "[[Systems/Mock-DSP]]"
---
# Phase1驗收紀錄

驗證對象:[[Systems/Mock-DSP]]、[[Systems/確定性指標計算]];依據計劃 [[Projects/RTB_Agent_Phase0架構]] 的「Phase 1 範圍與驗收」。

## 結論

Phase 1 在 DSP 端與指標計算的範圍內完成,下列缺口與偏離如實標明,不宣稱比這更多。

## 怎麼驗的(2026-09-22,提交 45af951)

- 驗收指令 `.venv/bin/python -m pytest -q`:188 條通過。
- `ruff check .` 通過;`lumos lint-check --smoke` 通過;`lumos doctor` 為 0 個問題。
- 變異檢查:對每個新增的防護,故意把程式改壞看測試會不會變紅。前後共做了約 60 個,全部被抓到,少數等價變異除外(例如用 fullmatch 時正則結尾的 `$`)。
- 穩定性:最怕不穩的測試(逾時、並行、重啟、猝死、連線洪水)手動用迴圈重跑,先後三批(50 次、50 次、30 次)失敗都是 0。
- 代碼審:Mock DSP 兩輪(高強度,10 席次,共 57 條發現,全部折入);指標計算兩輪(標準強度,19 條發現,18 條折入、1 條重現不到)。留痕與凍結判定都在治理帳。

## 對照計劃的驗收清單

- 四個必要介面加兩個查詢介面:有合約測試。`tests/dsp/test_server.py`、`tests/dsp/test_store.py`。
- 每種故障模式各一條測試、型別化錯誤可區分:有。版本衝突、同鍵同內容、同鍵不同內容以真實邏輯產生,不是標頭注入(有意的偏離,見已知缺口)。
- 版本單調、收到與提交時間:有(儲存層與 HTTP 各一)。
- 提交前逾時、提交後逾時:有,且提交前逾時測試會等過處理程式睡醒的時間,才能抓到「客戶端離開後才偷偷提交」。
- 同鍵同內容重送、同鍵不同內容拒絕:有。
- 並行同鍵只套用一次:有(儲存層與 HTTP,各 20 個請求同時放出);另有故意沒有任何保護的對照實作,證明這套手法抓得到雙寫;同鍵不同內容並行也有。
- DSP 原子提交:部分。有一條真的讓行程在「改狀態」與「寫冪等紀錄」之間猝死、再重開資料庫,驗證沒有半途狀態;另有一條獨立的 DSP 行程重啟測試驗證冪等紀錄還在。沒有「殺掉正在處理請求的 DSP 行程再重啟」這一條合併的端到端測試。
- 故障注入隔離(DSP 端):旗標關閉回 400 且不改狀態、只綁本機(以連線到非回送位址被拒絕來證明)。
- 指標計算與領域層禁用匯入:有,且有測試證明違規匯入會被 ruff 抓到、DSP 與測試路徑不受影響。
- 只用標準函式庫的掃描測試:有。
- 測試環境:埠動態分配、每測試獨立暫存資料庫、失敗與逾時路徑也會關閉行程(包含讀埠只讀到半行的情況)。
- lumos 測試指令與 .venv 一致:有,`lumos doctor` 不報錯。
- http.server 的最小實驗:一次性實驗(不在專案內)50 次一致;之後正式測試以迴圈重跑,見上。

## 已知缺口與偏離

- 「連續 50 次一致」是手動迴圈,沒有進 CI。
- agent 端連不到故障注入、不匯入 DSP 內部模組這兩條在 Phase 1 不宣稱已證明,因為 agent 端還不存在;Phase 2 第一次有 DSP 客戶端時必須補驗。(2026-10-02 更正:開頭這個重驗事件已發生,兩條都已有測試補驗——`tests/analyzer/test_boundaries.py` 的 test_the_analyzer_package_never_imports_dsp_internals 與 test_the_agent_cannot_reach_fault_injection_on_production_style_servers,見 [[Projects/RTB_Phase2任務流程_計劃]] 與 [[Systems/分析行程流程與檢查點]]。)
- 兩輪代碼審的第二輪都沒有外家席;指標計算沒有資安席。結論是單家族視角下未發現剩餘重要問題。
- 仍沒有測試守護的小防護、ruff 禁用清單不完整、`__import__` 與逐行 `noqa` 擋不住等,寫在兩篇系統節點的已知缺口。
- `getMetrics` 只回原始事實,比率由領域層自己算;金額欄位整數讀回是浮點。(2026-10-02 更正:程式裡從來沒有 getMetrics 這個名字,查指標是 `src/rtb/dsp/store.py` 的 get_metrics、路由 GET /campaigns/<id>/metrics;金額從 Phase 14 增量 2a 起改存整數分、讀回固定兩位小數字串,見 [[Systems/Mock-DSP]]。)
