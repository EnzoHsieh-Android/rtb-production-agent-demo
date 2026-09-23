preflight-4: ran

# Phase 9 增量 4 設計 第 3 輪 收貨紀錄

前掃在第 1 輪已跑(見 `r1-intake.md` 與 `r1-preflight.md`);這一輪是末輪驗收,不另跑前掃。

## 第 3 輪收貨(2026-09-24)

三席:s1 第 2 輪驗收與逐刻重推、sarch 架構對齊(sonnet)、x1 外家 Codex(沒撞到用量限額)。受審版本:第 4 版(提交 d9cb9e9)。收齊後才動計劃;收件前查 reflog,沒有不是編排者做的提交。條數由腳本數席報告的 `### F` 標題下第一行等級與 `blocking: 是` 行:6 條(1 blocker、4 major、1 minor),blocking 5。三席全數錨定。s1 驗收第 2 輪 6 條都已修。

更正:`r2-intake.md` 開頭寫的「共 5 條:1 blocker、3 major、1 minor」是目視加總錯,腳本重數是 6 條(1 blocker、4 major、1 minor)、blocking 5;表格 6 列本身正確,帳上各席 findings 也照各席報告記,不受影響。

| id | 重現 | 結果 | 處置 |
|---|---|---|---|
| sarch-F1 | `git show phase9-inc1:tests/executor/write_scan.py` 第 16 行已有 `_strings`,主線 Phase 8 那支也叫 `_strings` | HIT | 折:搬進來的換名,原有的保留原名原語意,共用模組補一支兩種語意都驗的測試 |
| x1-F4 | 同 sarch-F1 | HIT | 同上 |
| x1-F1 | 讀 `src/rtb/executor/execution.py` 對帳查到就走已提交待驗證,不重送;送出次數只在轉回嘗試中時加 | HIT | 折:送出次數分兩類斷言,提交後逾時那把一直是 1 |
| x1-F2 | `git show phase9-inc1:src/rtb/__init__.py` 程式版本是行程載入時定的常數;`git show phase9-inc1:src/rtb/executor/attempt_store.py` 直接匯入 | HIT | 折:比照 `tests/analyzer/test_f6_end_to_end.py` 換政策版本的做法,輪到哪個工作者前換常數,換完斷言三種紀錄的版本 |
| x1-F3 | `src/rtb/executor/inbox_store.py` 待處理上限預設 8,建構參數可調 | HIT | 折:上限開到 17 以上,佈置後斷言 17 筆都收進去 |
| s1-F1 | 腳本重數第 2 輪四份報告 | HIT | 折:審計修正紀錄增量 4 r2 那行改成 6 條、blocking 5,並註明更正 |
