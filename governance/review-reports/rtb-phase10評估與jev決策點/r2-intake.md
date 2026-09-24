preflight-4: ran

# Phase 10 設計 第 2 輪 收貨紀錄

五席:s1 前輪驗收與鏡像、s2 評分表與指標數學、s3 新介面可測性、sarch 架構對齊(sonnet)、x1 外家 Codex(沒撞到用量限額)。受審版本:第 3 版(提交 8bde684)。五席收齊才動計劃;收件前查 reflog,沒有不是編排者做的提交。sarch 報告開頭多一行開場白,只用 report-normalize 補檔級行。s2 檔級行寫 major、但 F1 標 blocker,帳面照最高記 blocker。條數由腳本數:17 條(1 blocker、14 major、2 minor),blocking 15。s1 判第 1 輪 36 條全數已修。

這輪折入時一併寫進使用者本人(經協調者轉達)對評估對象的裁定:保留、照實寫兩個不採用理由、Phase 0 路由表加註。

重現(程式與計算類;其餘是對快照文字的讀取,編排者逐條讀快照核對,全部 HIT):
- `sed -n 64,76p src/rtb/analyzer/policy.py`:現行規則只看曝光點擊、不看狀態;task 為空與預算非數字兩處丟 AssertionError → x1-F1、x1-F2、s3-F2 HIT。
- `grep -n "fetch-depth\|checkout" .github/workflows/ci.yml`:CI 用預設淺層複製,測試看不到提交歷史 → s3-F4 HIT(改用存下舊原始碼與雜湊,不靠提交先後)。
- `git show e8b26f6:src/rtb/analyzer/policy.py | shasum -a 256` 與工作樹相同(750b2af2051a3c78…)→ 舊原始碼雜湊可以寫死。
- `head -3 src/rtb/ops/ruff.toml` 與 tests/ops/test_ops_boundaries.py 先驗 TID251 再掃原始碼 → sarch-F1 HIT。
- `find src -type f ! -name "*.py"` 只有各層 ruff.toml;既有樣本是 tests 下的 Python 模組 → sarch-F2 HIT。
- Wilson 全對下界 n/(n+z²):過 0.95 需 73、過 0.80 需 16 → s2-F4 HIT。

| id | 結果 | 處置 |
|---|---|---|
| s1-F1 | HIT | 折:補 [S713] 擾動檢查、[S714] 現行規則逐格計分 |
| s1-F2 | HIT | 折:「歸不了格」只指建構失敗,四種退回分支都靠單元測試 |
| s2-F1 | HIT | 折:判斷點欄位允許缺值、建構時驗值;歸不了格=建構失敗,發生在路由之前 |
| s2-F2 | HIT | 折:同 s1-F1 |
| s2-F3 | HIT | 折:建構時拒絕布林、非有限數、超界、狀態不在列舉;補 [S715] |
| s2-F4 | HIT | 折:值得加格 16 筆、其他三格 73 筆,分開寫 |
| s3-F1 | HIT | 折:[S703] 只當回歸網,值驗證另立 [S715] |
| s3-F2 | HIT | 折:診斷函式對丟例外的輸入丟同一種例外 |
| s3-F3 | HIT | 折:[S705] 用固定資料的決策輸入比對;評估計分走路由函式 |
| s3-F4 | HIT | 折:存下舊原始碼與寫死的雜湊、隔離執行比對 |
| sarch-F1 | HIT | 折:五個目錄匯入規則加禁令,邊界測試先驗禁令再掃原始碼 |
| sarch-F2 | HIT | 折:評估集改成 Python 模組 |
| sarch-F3 | HIT | 折:判斷點型別與列舉放領域層 |
| x1-F1 | HIT | 折:依退回後的最後有效答案計分,另報不知道、非法、例外、逾時、退回次數 |
| x1-F2 | HIT | 折:現行規則逐格報分子分母與錯誤子型 |
| x1-F3 | HIT | 折:合成集定位成有限合約案例、不套統計信賴、不能產生已驗證清單 |
| x1-F4 | HIT | 折:採用另要成本、延遲、各失敗率都量過且達標;候選包硬逾時 |
