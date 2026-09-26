severity: blocker

## 發現 1: AI 可不查四項資料就直接提案，繞過正式九條

severity: blocker  
blocking: 是  
引句:「AI 已用過或失敗、預檢沒過、答非選項時，退回新規則；若模型前面未選滿四查詢，轉入規則查詢步驟，補齊後才定案。」

輸入場景：廣告配速偏低、1 小時有點擊與轉換，但最近三天剛調過預算，九條的第 3 條應判證據不足。模型第一輪直接回答 `propose` 時，spec 只要求**退回規則**補查，沒有要求 AI 的提案先查齊或通過九條。結果仍會建立提案。

file: `src/rtb/analyzer/investigation.py:159` + 第一輪允許的選項已包含三種結論，且模型整件工作最多只能選三種查詢。  
file: `src/rtb/analyzer/investigation.py:374` + 現有提示甚至示範只引用基本收據就回答 `propose`。  
file: `src/rtb/analyzer/ai_judge.py:139` + `PROPOSE` 直接呼叫建提案函式；沒有正式規則核對。  
file: `src/rtb/analyzer/flow.py:425` + 流程隨即提交 `PROPOSED`。

## 發現 2: 一般預算寫入不產生第 4 條所需的過去調整資料

severity: major  
blocking: 是  
引句:「四查詢成功但 `rows=[]`、`no_data` 或分母零，是有結果且數值不可算；第 4／5 條按原 `answer()` 語意跳過。」

輸入場景：展示種子先給空的過去調整表，之後正常加一次預算；四天後，前後三天轉換沒有增加、配速偏低且 1 小時有轉換。操作歷史會記得加預算，但過去調整查詢仍成功回 `rows=[]`。照 spec 跳過第 4 條後，平穩逐日資料不命中第 5 條，第 8 條便會誤提案。

file: `src/rtb/dsp/seed.py:67` + 展示種子明定沒有過去調整。  
file: `src/rtb/dsp/store.py:680` + 正常改預算只更新廣告並寫操作歷史，沒有寫過去調整表。  
file: `src/rtb/dsp/store.py:478` + 過去調整端點讀的是另一張表，空列仍是成功結果。  
file: `src/rtb/eval/investigation_cases.py:118` + 第 4 條只從過去調整列找最近一次加預算。

## 發現 3: 分步蒐證只核對基本資料，會接受互相矛盾的日資料與長窗

severity: major  
blocking: 是  
引句:「步驟 C 讀 1d、7d 並重讀現況與 1 小時指標（4 次），核對版本、廣告編號與基本資料；最後才在同一 `now` 上判斷。」

輸入場景：步驟 B 讀到轉換率平穩的逐日資料；步驟 C 前，7 天窗已更新成與逐日加總不符的數字，廣告版本與 1 小時資料沒有變。四項查詢各自都通過欄位檢查，spec 的重讀核對也通過，第 5 條依舊逐日資料跳過，第 8 條可提案。這批數字無法共同描述同一個決策時點，應先判證據不足。

file: `src/rtb/analyzer/dsp_client.py:266` + 逐日檢查只驗該回應的形狀與欄位。  
file: `src/rtb/analyzer/dsp_client.py:308` + 長窗檢查只比較 1d 與 7d，未與逐日資料比較。  
file: `src/rtb/dsp/seed.py:111` + 專案已有「逐日第 1 天等於 1d、逐日加總等於 7d」的一致性判法，但它只供種子核對。  
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase13AI參與決策_計劃.md:698` + [S1127] 要求展示種子滿足這兩項一致性；本設計的正式分步讀取沒有守住同一條件。

## 發現 4: 規則改版沒有政策版本切換，舊誤提案可在新規則上線後執行

severity: major  
blocking: 是  
引句:「已建立但未確認的提案依原版本與到期時間處理；已送平台的預算不靠回退程式自動撤銷，按執行紀錄人工核對。」

輸入場景：舊規則在近期調過預算的廣告上建立提案；九條規則上線後、該提案的 30 分鐘有效期內，人工核可它。若照 spec 只替換判斷而沿用現行政策版本，執行端看到提案版本等於現行版本，仍可寫入預算；新規則本應判證據不足。回退段落對未確認提案的處置也沒有排除這條路徑。

file: `src/rtb/domain/proposal.py:26` + 目前政策版本是共用常數 `demo-pacing-v1`，分析端與執行端同讀它。  
file: `src/rtb/analyzer/policy.py:41` + 提案有效期為 30 分鐘。  
file: `src/rtb/analyzer/flow.py:446` + 已建立的提案會按存下的快照送件，不會重跑決策。  
file: `src/rtb/executor/execution.py:350` + 執行前只在提案政策版本**不等於**現行版本時擋下。  
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase8死信重放與過時決策_計劃.md:99` + [S504] 的政策變更阻擋依賴版本確實變更；本次改規則須把版本切換與既有提案處置寫入驗收。

逐節判讀：〈這份計劃在解決什麼〉、〈使用者裁定〉、〈現況〉已讀，無 finding。〈設計／九條判斷〉見發現 2；〈正式蒐證、時間與租約〉見發現 3；〈AI 退回與展示〉見發現 1；〈評估與報告〉已讀，無 finding。〈拆增量〉、〈會卡住這個設計的既有程式〉已讀，無 finding。〈要改寫的既有合約〉與〈回退〉見發現 4。〈合約候選〉、〈驗收條款〉已讀，相關缺口同發現 1–4；〈待審問題〉、〈審計修正紀錄〉已讀，無 finding。

實務隱患：併發時跨步讀取的一致性見發現 3；效能與資源已有逐步租約上界及 F7 實跑門檻，已讀，無 finding；回滾與未到期提案見發現 4。

圖譜節點：`Systems/分析行程流程與檢查點` 的單次提交與快照送件不被設計直接改寫，但其快照性使發現 4 必須在送件前處理。`Systems/任務流程領域模型` 的證據新鮮度須繼續逐筆驗證；發現 3 說明新鮮仍不足以證明跨查詢一致。`Systems/評估與Jev決策點` 的逐格報告要求有保留。`Systems/一鍵展示` 與 `Systems/展示頁面` 的改動已列在 spec，未見另項合約破壞。`Systems/Mock-DSP` 的寫入原子性、冪等及版本核對不被改寫；發現 2 涉及其唯讀歷史資料來源。`Systems/追蹤檢視` 的既有追蹤語意未被改寫，已讀，無 finding。

總結: 最嚴重 severity: blocker；blocking 4 條。