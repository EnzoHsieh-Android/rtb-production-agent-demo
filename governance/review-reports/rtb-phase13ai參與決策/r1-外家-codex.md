severity: major

## 發現 1:三欄的合格回答會被合約判成不合格
severity: major
blocking: 是
引句:「當模型的回答不是恰好含選項與理由兩欄的 JSON、選項不在這一輪允許清單」
設計正文第 191 行要求回答恰好有 `choice`、`reason`、`evidence` 三欄，[S1131] 也要求核對 `evidence`；[S1105] 卻以「恰好兩欄」作為合格條件。輸入 → 模型回傳三欄且證據有效的 JSON；預期 → 接受結論；照 [S1105] 字面實作 → 判為選項外答案，所有有效結論都退回程式規則。

## 發現 2:首輪直接下結論時沒有可引用的收據
severity: major
blocking: 是
引句:「選結論時至少 1 項」
設計只替追加查詢建立參照代號與收據，首輪送出的 1 小時指標沒有相同機制；現行蒐證首輪只有現況、1 小時指標及廣告文字，見 `src/rtb/analyzer/dsp_client.py:126`、`src/rtb/analyzer/dsp_client.py:136`。輸入 → 首輪數字已足夠，模型直接選 `propose` 並引用 1 小時轉換數；預期 → 一輪完成有證據的決策；照設計 → 無收據參照可填，空證據被退回規則，先查一次無關資料反而成為讓 AI 結論生效的必要步驟。

## 發現 3:追加證據的種類隔離擋不住新鮮度檢查
severity: major
blocking: 是
引句:「當一批證據除了現況與 1 小時指標之外還帶任意追加查詢證據時,現行決策函式的結果應跟不帶時相同。」
現行規則先檢查**每一筆**證據的新鮮度，之後才按種類挑現況與指標，見 `src/rtb/analyzer/policy.py:54`、`src/rtb/analyzer/policy.py:210`、`src/rtb/analyzer/policy.py:212`。輸入 → 新鮮的現況與 1 小時指標足以提案，另附一筆已超過 15 分鐘的追加證據；預期 → 退回規則仍照基本兩筆提案；照「只新增種類」實作 → 回傳 `NeedsFreshEvidence`，回去重蒐證。[S1115] 與既有新鮮度合約不能同時成立，須明定追加證據在退回路徑如何排除。

## 發現 4:租約守衛漏算模型呼叫前後的等待
severity: major
blocking: 是
引句:「每一步最壞耗時 = max(蒐集證據的 DSP 呼叫次數 × DSP 逾時, 模型逾時);兩倍要小於租約。」
25 秒只限制後端請求。模型用戶端在請求前預留帳款、請求後最多重試結算三次；每次資料庫鎖等待可達 5 秒，逾時清理行程群組也有額外等待，見 `src/rtb/modelclient.py:354`、`src/rtb/modelclient.py:443`、`src/rtb/sqlitekit.py:16`、`src/rtb/modelclaude.py:294`、`src/rtb/modelclaude.py:307`。輸入 → 帳本競爭、模型在 25 秒逾時、清理及結算再次等待；預期 → 原持有者在 60 秒租約內完成或停止；照守衛放行 → 整步可超過 60 秒，另一工作者可取得租約並重做這輪模型呼叫。現行圍籬只防舊持有者提交，不能收回重複呼叫的成本，見 `src/rtb/analyzer/task_store.py:588`、`src/rtb/analyzer/task_store.py:489`。

## 發現 5:原始多列回應無法按現有證據形狀原樣保存
severity: major
blocking: 是
引句:「每次查詢的原始回應(逐日 7 列、過去調整最多 5 筆、操作歷史、1 天/7 天窗)照原樣存進證據表」
現有 `Evidence.payload` 只接受至多 32 個純量欄位，不能容納七列或五筆巢狀回應，見 `src/rtb/domain/evidence.py:20`、`src/rtb/domain/evidence.py:23`、`src/rtb/domain/evidence.py:95`。現有證據寫入還會重新序列化並排序鍵，不能保證取回 HTTP 原文逐位元組相同，見 `src/rtb/analyzer/task_store.py:539`、`src/rtb/analyzer/task_store.py:545`。輸入 → DSP 回七列逐日資料；預期 → 保存並按 [S1130] 逐位元組取回；照「新增證據種類」及現有寫法 → 型別驗證拒收，或轉成扁平欄位後失去原文。[S1130] 需要另定原始回應的儲存形狀與位元組邊界。

## 發現 6:七個故障情境遇到合法的不提案答案會誤判
severity: major
blocking: 是
引句:「哪些情境讓 AI 決定 (使用者裁定 5:七個情境全開;F7 規模、F3 執行緒與判定分層的後果由設計處理,見〈使用者裁定〉)」
設計只具體改寫 F5 的斷言；現有 F1 必須等到寫入完成，F7 必須等到全部廣告進入寫入或待核可，見 `src/rtb/demo/driver.py:470`、`src/rtb/demo/driver.py:477`、`src/rtb/demo/driver.py:883`、`src/rtb/demo/driver.py:887`。輸入 → AI 合法選 `do_not_propose`；預期 → 分開記錄「AI 未提案」與「故障處理因此未被演練」；照增量 4 所列的改動實作 → F1 等不到寫入、F7 等不到核可，情境逾時並標「沒跑完」，把有效 AI 決策混成故障流程失敗。

## 發現 7:F5 雙胞胎會與「沒有別的廣告寫入」互撞
severity: major
blocking: 是
引句:「F5 另種一個數字完全相同、名稱正常的雙胞胎廣告;預期兩者的選項序列與結論相同。」
F5 現在把廣告種在同一個情境資料庫，對各廣告建立任務，並核對全平台寫入只含受攻擊廣告的一筆，見 `src/rtb/demo/driver.py:680`、`src/rtb/demo/driver.py:683`、`src/rtb/demo/driver.py:696`。輸入 → 雙胞胎與受攻擊廣告數字相同，模型對兩者都正確選 `propose`；預期 → 模型考題通過，F5 程式層斷言也通過；照「另種廣告」放進同一條執行路徑 → 雙胞胎亦產生平台寫入，違反設計第 295 行的「沒有別的廣告」及 F5 全平台斷言。須指定雙胞胎的隔離執行或只讀評測邊界。

7 條,blocking 7。