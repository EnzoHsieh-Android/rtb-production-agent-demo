preflight-4: ran

# Phase 9 增量 4 設計 第 2 輪 收貨紀錄

前掃在第 1 輪已跑(見 `r1-intake.md` 與 `r1-preflight.md`);這一輪是第 1 輪大改之後的驗收輪,不另跑前掃。

## 第 2 輪收貨(2026-09-24)

四席:s1 前輪驗收與鏡像、s2 逐刻推演對照程式、sarch 架構對齊(sonnet)、x1 外家 Codex(沒撞到用量限額)。受審版本:第 3 版(提交 ebe5db4)。收齊後才動計劃;收件前查 reflog 與作者,沒有不是編排者做的提交。共 5 條:1 blocker、3 major、1 minor;s1 驗收第 1 輪 27 條都已修。quote-check 四席全數錨定。refcheck 的錨不到:sarch 引的 `tests/executor/write_scan.py`、`tests/ops/test_ops_boundaries.py` 在增量 1 實作分支(`git show phase9-inc1:tests/executor/write_scan.py` 對得上),`tests/executor/test_dead_letter.py:451` 在主線,`tests/_sql_scan.py` 是第 1 輪席位建議的檔名、不存在。記帳載體用 x1。

| id | 重現 | 結果 | 處置 |
|---|---|---|---|
| s2-F1 | `grep -n DECISION_FRESHNESS src/rtb/executor/guardrails.py` 為 15 分鐘;讀 `src/rtb/executor/execution.py` 重送前 `still_latest` 呼叫 `_stale_without_approval`;既有測試 `tests/executor/test_stale_decision.py` 驗證對帳重送撞決策過時就作廢 | HIT | 折:事故改 12 分鐘,夾在 10 分鐘期限與 15 分鐘新鮮度之間,前提檢查斷言兩條不等式 |
| x1-F1 | 讀 `src/rtb/executor/inbox_store.py` 接手租約:擁有者是自己就立刻接手 | HIT | 折:取件那一刻不跑對帳,逐刻斷言送出次數 1、2、3 |
| x1-F2 | 讀 `src/rtb/dsp/server.py` 請求處理新開儲存物件沒傳時鐘、`src/rtb/dsp/store.py` 提交時間取系統時間;`tests/executor/conftest.py` 測試時鐘固定起點 | HIT | 折:虛擬時鐘從系統時間起跳;副作用核對斷言有效事件大於 0 |
| x1-F3 | 讀 `src/rtb/executor/attempt_store.py` 檔頭:只增不改歷史表;增量 3 讀它算期限與副作用 | HIT | 折:守衛加嘗試紀錄成七張表;它在通用補欄位登記表裡,規則改成驗登記欄位可為空無預設(今天四欄都合規) |
| sarch-F1 | `git show phase9-inc1:tests/executor/write_scan.py` 是原始碼掃描共用模組,維運套件測試跨目錄匯入 | HIT | 折:拼回字串函式併進那一支,不另開 |
| s1-F1 | 逐席數第 1 輪報告的 severity 與 blocking 行 | HIT | 折:增量 4 r1 那行改成 3 blocker、21 major、3 minor,blocking 24 |
