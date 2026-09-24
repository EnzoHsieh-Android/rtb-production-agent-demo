severity: major

新一輪完整展示的情境被標成單一情境重跑
severity: major
blocking: 是
引句:「if full_demo_id is not None and scenario.source_demo_id != full_demo_id:」
file: `src/rtb/demo/server.py:218`
file: `src/rtb/demo/page.py:680`
重現: 唯讀追查 `state()` 與 `_origin_kind()`：上一輪完整展示結束後再啟動新一輪時，`server.py` 傳給頁面的 `full_demo_id` 仍是上一輪編號；本輪已執行的情境編號與它不同，主頁和報告便誤標「取自單一情境重跑」。反過來，若第一次就執行單一情境，`full_demo_id` 為空，該情境會誤標「取自完整執行」。兩種情況都讓結果來源與實際執行不符。
建議: 為每次展示記錄完整執行或單一情境重跑的種類，依結果所屬展示判定來源；補測「第二次完整展示進行中」與「首次操作直接重跑」。

上一輪 x1、x2 已由完整展示在跑時只讀本次情境與查核結果處理；x3 的簽發與關窗已放進同一個寫入交易。本席依上一輪的唯讀追查方式驗收，未跑測試或啟動展示行程。`ls -la ~/.rtb` 前後均只見空目錄；前後 `ps` 檢查均回覆 `operation not permitted`，本席沒有啟動需清理的行程。

1 條,blocking 1。