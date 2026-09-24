severity: major

F1 核可只查展示快照，可能替已不再待核可的提案簽發
severity: major
blocking: 是
引句:「伺服器行程內直接呼叫既有的簽發函式(approval.issue)再寫進 F7 的執行端暫存資料庫(add_approval),不另開核可命令列子行程」
file: `src/rtb/executor/approve.py:67`
file: `src/rtb/executor/inbox_store.py:1427`
file: `src/rtb/executor/inbox_store.py:1444`
具體例:確認頁開啟後，提案在收件表中到期並結案 → POST 仍只比對展示狀態保存的編號、雜湊與數字，照設計可簽發並寫入一張已無法放行的核可；`approval.issue` 和 `add_approval` 還需要設計未保存的完整提案物件 → 應先確認資料庫裡這份提案仍停在同一待核可關卡，才回報核可成功。
建議:POST 使用既有 `find_proposal` 取得完整提案，核對目前關卡、內容雜湊及到期時間與展示快照一致；不一致就拒絕並要求重讀確認頁。

F2 F7 八個工作者的任務進度被壓成單一目前節點
severity: major
blocking: 是
引句:「資料結構再加:Scenario 加 current_node(str | None,正在執行的節點 id;只有 RUNNING 的情境會有);DemoState 加 current(CurrentStep | None):scenario(ScenarioCode)、node(str)、started_at(datetime)、last_decision(Decision | None)。」
file: `/Users/enzo/rtb-12-page/src/rtb/demo/state.py:180`
file: `/Users/enzo/rtb-12-page/src/rtb/demo/state.py:202`
file: `tests/executor/test_f7_end_to_end.py:61`
具體例:F7 同時有一筆任務正在寫入 DSP、另一筆停在等人確認 → `Scenario.current_node` 與 `DemoState.current` 各只能保存一個節點，頁面會把其中一筆顯示成整個情境的目前進度，使用者看不到另一筆走到哪裡 → 應按任務顯示各自的目前節點、進入時間與處置。
建議:展示狀態加入以任務識別碼為鍵的進度與判斷紀錄；F7 頂端可另顯示彙總，但任務明細須能同時呈現多個工作者的狀態。

F3 F7 只核對總數，寫錯廣告仍可能顯示完成
severity: major
blocking: 是
引句:「F7 按彙總比(放行幾筆、停在等人確認幾筆),不逐筆比時間序。對不上就標「沒跑完」並寫哪一步對不上。」
file: `tests/executor/test_f7_end_to_end.py:83`
具體例:123 筆寫入中，一筆寫到原應停下的廣告，另一筆原應寫入的廣告沒改；寫入數、待確認數及加額總和都不變 → 照設計的彙總核對仍會標「照預期跑完」 → 應核對每次實際寫入都有對應的提案與廣告，且待確認提案沒有寫入。
建議:保留不比較並行完成順序的做法，但以任務、廣告、提案雜湊及預算值逐筆比對 DSP 操作歷史和收件表處置。

3 條,blocking 3。