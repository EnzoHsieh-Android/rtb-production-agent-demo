severity: major

F1 訂閱額度用完被誤列為未送出，評估失敗率會失真
severity: major
blocking: 是
引句:「都沒有送出請求,不算進格式失敗率、例外率、逾時率、退回率四種比率,另外列出件數」
第 5 版把「本地預留超限、尚未啟動子行程」與「Claude Code 回報訂閱額度用完」合併為同一個「已達上限」類別，見 `governance/review-reports/rtb-phase11b大模型接入-cc/r1-snapshot.md:89`；但後者已經啟動 Claude Code，可能已向供應商送出請求，不能套用 `governance/review-reports/rtb-phase11b大模型接入-cc/r1-snapshot.md:145` 的「都沒有送出」母體規則。現行採用判斷確實以例外率、逾時率及退回率對照失敗門檻，見 `src/rtb/eval/adoption.py:148`；候選例外又會被路由吃掉並退回現行規則，見 `src/rtb/analyzer/policy.py:153`，因此旁路分類是唯一能保留這次失敗的地方。
具體例: 評估第一筆已啟動 `claude -p`，供應商回覆訂閱額度用完 → 旁路記成「已達上限」，設計把它排除於四種失敗率並宣稱沒有送出 → 應記為「供應商訂閱額度用完／已啟動子行程」並納入相應的例外率、退回率與延遲，只有呼叫前的本地額度拒絕才能排除。
建議: 把「已達上限」拆成至少「本地額度拒絕（未啟動）」與「訂閱額度用完（已啟動）」兩種結果；S924 與母體規則逐類明定是否啟動、是否可能送出、如何結算及計入哪些比率，並補一支供應商額度錯誤不得被排除的測試。

F2 Claude Code 無輸出 token 上限，並行呼叫可突破本地硬上限
severity: major
blocking: 是
引句:「加輸出上限 token 數,各乘價目表,再乘安全係數 1.2」
模型用戶端收「輸出上限 token 數」，預留又把它當成最壞花費，見 `governance/review-reports/rtb-phase11b大模型接入-cc/r1-snapshot.md:80`、`governance/review-reports/rtb-phase11b大模型接入-cc/r1-snapshot.md:109`；但 Claude Code 啟動參數清單沒有把這個上限傳入，見 `governance/review-reports/rtb-phase11b大模型接入-cc/r1-snapshot.md:82`。本機 Claude Code 2.1.281 的 `claude --help` 只有整次呼叫的 `--max-budget-usd`，沒有輸出 token 上限參數。設計卻把每個子行程的花費上限設成當下剩餘額度除以 1.2，見 `governance/review-reports/rtb-phase11b大模型接入-cc/r1-snapshot.md:108`；它不是該筆已預留金額。因此 S903 的並行安全建立在一個後端根本沒有強制的輸出上限上。
具體例: 同一展示同時發出兩筆短提示，各宣告輸出上限 100 token → 兩筆只各預留很小金額，交易都放行，兩個 Claude Code 子行程又各取得接近 0.83 美元的單次上限；實際輸出不受 100 token 約束時，兩筆可各消耗約 0.8 美元，結算後含係數合計約 1.92 美元 → 應在啟動第二筆前把第一筆可能消耗的完整金額視為在途額度，使同一展示永遠不超過 1 美元。
建議: 不要用呼叫者提供但 Claude Code 無法強制的輸出 token 數計算「最壞花費」。預留應採 Claude Code／模型真正可能產生的最大輸出，或把傳給每個子行程的 `--max-budget-usd` 限制在該筆已預留原價內；並新增兩筆並行、實際花費遠高於宣告輸出上限的測試，驗證展示與月上限結算後仍不會超額。

2 條,blocking 2。