preflight-4: ran

# Phase 10 設計 第 3 輪 收貨紀錄(最後一輪)

三席:s1 前輪修復驗收與鏡像、sarch 架構對齊(sonnet)、x1 外家 Codex(沒撞到用量限額)。受審版本:第 4 版(提交 2815917)。三席收齊才動計劃;收件前查 reflog,沒有不是編排者做的提交。sarch 報告開頭多一行開場白,只用 report-normalize 補檔級行。條數由腳本數:8 條(7 major、1 minor),blocking 7。s1 判第 2 輪 17 條全數已修。

重現:
- `grep -n "^from\|^import" src/rtb/analyzer/policy.py`:決策規則匯入六個 rtb 模組 → s1-F1 HIT。
- `grep -rn "exec\|compile\|module_from_spec" src tests`:沒有動態執行原始碼的先例;維運邊界測試把這類寫法列為禁用 → sarch-F2 HIT。
- `grep -n "timeout" src/rtb/httpclient.py src/rtb/httpkit.py`:專案的逾時都是合作式的網路逾時,沒有強制中斷同步呼叫的機制 → sarch-F1、x1-F3 HIT。
- 其餘(x1-F1 允許清單循環、x1-F2 三類被壓成二元、x1-F4 隱藏集放進程式庫、s1-F2 建構失敗例外)是對快照文字的讀取,編排者逐條讀快照核對,HIT。

| id | 結果 | 處置 |
|---|---|---|
| s1-F1 | HIT | 折:凍結舊規則三步分開報,第二步紅代表相依模組變了;增量 1 不改六個相依模組;相依介面變動時撤掉(REVISIT) |
| s1-F2 | HIT | 折:建構失敗丟專用例外,決策函式只接這一種 |
| sarch-F1 | HIT | 折:逾時改用既有的合作式 HTTP 逾時,不經共用 HTTP 用戶端的候選不得採用;殘留風險列實務隱患附 REVISIT |
| sarch-F2 | HIT | 折:凍結舊規則改成測試目錄裡照常匯入的模組,不動態執行 |
| x1-F1 | HIT | 折:允許清單分成已驗證清單與待測格清單兩種型別,補 [S716] |
| x1-F2 | HIT | 折:三類答案的格另要類別正確率,[S708] 跟著改 |
| x1-F3 | HIT | 折:同 sarch-F1 |
| x1-F4 | HIT | 折:正式環境隱藏集不進程式庫,只存版本、雜湊、出處與彙總;候選版本須早於揭露 |
