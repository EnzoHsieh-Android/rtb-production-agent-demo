severity: minor

# 架構對齊席 r2(arch2)

已核對:PRIOR-ART 一行、RETIRE-IF 兩行、決策四欄(d1 到 d8 皆齊)、d8 撤除條件、REVISIT 緊鄰原句(SQLite 那條)、事件入口(分析端與執行端待決、http.server 殘留風險)、lumos linter 宣告檔機制(`scripts/lumos:2651` 有 `.lumos/lint.json` 檢查,計劃「補宣告檔」與之一致)、元件數量對照 handoff 第 5 節(分析、隔離執行、政策、工具,未多拆 Agent)、政策十一道檢查對照 handoff 第 8 節(十一項相符)。元件與 Phase 1 範圍未超出 handoff 第 5 節與 Phase 0 完成條件,無過度設計的 blocker。

## Finding 1:pytest 例外(d6)沒有撤除條件

severity: minor
blocking: 否 判準:家規缺口,補一句即可,不影響設計方向或後續 Phase 的正確性。

- 問題:d8(ruff)寫了撤除條件「誤報多過真報」,d6(pytest)沒有。第二條 RETIRE-IF 講的是「執行路徑 import 外部套件,例外清單重審」,那是例外邊界失效,不是「pytest 何時該退場」。
- 佐證:引句:「取捨:多一個外部依賴,只限測試工具,Demo 執行時仍只用標準函式庫。」
- 佐證:CLAUDE.md 設計與排查一節要求寫不出撤除條件就是還沒想清楚該不該建。

## Finding 2:d7 的理由含「換儲存或佇列」這種沒有需求的彈性,與 18.5 有小張力

severity: minor
blocking: 否 判準:張力局部、原則段已有「只在兩種實作或需隔離時才切介面」的自我約束,沒有導致新增元件。

- 問題:「純判斷與外部動作分開」有正當理由(不啟動行程就能測安全規則,對應 handoff 第 8 節),但 d7 的 why_chosen 又掛上「換儲存或佇列時不動政策」。本專案只有 SQLite 一種儲存,換儲存不是 Demo 需求;「資料庫與 DSP 客戶端透過小介面接進來」若在只有單一實作時也切介面,就是 18.5 點名的多餘抽象。
- 佐證:引句:「安全規則要能不啟動任何行程就測完,且換儲存或佇列時不動政策」
- 佐證:handoff 第 18.5 節「每個 abstraction、service、Agent、database、queue 都要對應明確 requirement 或 failure mode」(`/Users/enzo/Downloads/RTB_PRODUCTION_AGENT_DEMO_HANDOFF.md:801`)。
- 建議:理由只留「可不啟動行程測政策」;DSP 客戶端介面可留(測試替身有需求),資料庫不預先切介面。

## Finding 3:承認「沒有機械判準」但沒附回頭條件

severity: minor
blocking: 否 判準:屬家規第 4 條的形式缺口,風險本身(命名與切分品質)低。

- 問題:計劃承認命名與切分無機械守衛,依家規第 4 條旁邊要有帶日期的 REVISIT 或明寫事件入口;此處與 d7 取捨都沒有。
- 佐證:引句:「命名與切分是否合理留給設計審與代碼審判斷,沒有機械判準。」
- 佐證:`/Users/enzo/rtb-production-agent-demo/CLAUDE.md:56`(承認風險要附回頭看的條件)。

## Finding 4:lands_in 的第三篇節點與 Phase 1 範圍對不上

severity: minor
blocking: 否 判準:只是落點宣告的時程偏差,Phase 1 開工前改一行即可。

- 問題:落點說 Phase 1 新開「事件與狀態的資料模型」節點,但 Phase 1 範圍只有 Mock DSP 與指標計算,事件與狀態屬 Phase 2(handoff Phase 2 才做 State、Trace)。家規要求每支檔有家、lands_in 寫現況落點;沒有程式的節點會是空殼,或逼 Phase 1 多寫沒需求的程式。
- 佐證:引句:「Phase 1 會新開三篇 Systems 節點:Mock DSP、確定性指標計算、事件與狀態的資料模型」
- 佐證:`/Users/enzo/rtb-production-agent-demo/docs/rtb-production-agent-demo-knowledge/Systems` 目前為空;`lands_in` 前兩篇對應 Phase 1 程式,第三篇沒有對應程式。
- 建議:第三篇改到 Phase 2 開新,或 lands_in 只列前兩篇。

## Finding 5:「沒有任何測試」與 repo 現況不符

severity: minor
blocking: 否 判準:事實描述小偏差,不影響任何決策。

- 問題:「已知」一節宣稱用指令查過,但 repo 內已有 lumos 隨附的測試檔,現行 lumos 測試設定也已指向 pytest 樣式的執行指令。應改成「專案自己的程式沒有測試」,並說明 Phase 1 要改的是 `run_cmd` 指向 `.venv`。
- 佐證:引句:「沒有 CI 設定、沒有任何測試;pytest 尚未安裝。」
- 佐證:file: `/Users/enzo/rtb-production-agent-demo/scripts/test_lumos.py:1`;file: `/Users/enzo/rtb-production-agent-demo/.lumos/config.json:5`(`python3 -m pytest -k {method} -q`)。

## Finding 6:故障控制面「獨立連接埠」與「單一請求以標頭或參數指定」機制含糊

severity: minor
blocking: 否 判準:Phase 1 有 50 次實驗判準與控制面隔離測試把關,實作時必會暴露,不會帶進錯誤架構。

- 問題:若故障模式由每個業務請求的標頭或參數指定,故障入口就在業務連接埠上,獨立控制面連接埠沒有作用;若模式由控制面預先登錄再對應到單一請求,則需要一個相關鍵機制,計劃沒交代。兩種做法擇一即可,否則實作時可能長出第二套。這也是保守判斷下最接近「過度設計」的一處(獨立連接埠加啟動旗標加隔離測試)。
- 佐證:引句:「控制面:故障注入,獨立連接埠,啟動旗標開啟才存在」
- 建議:Phase 1 開工前寫一句「模式如何綁到單一請求」,或改為只用啟動旗標保護的請求標頭,省掉第二個連接埠。

最嚴重等級為 minor,blocking 共 0 條。
