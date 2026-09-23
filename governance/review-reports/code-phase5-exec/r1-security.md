severity: major

# 審查範圍

`r1-snapshot.patch`(repo `/Users/enzo/rtb-3b`),執行側:`src/rtb/executor/attempt_store.py`、
`execution.py`、`inbox_server.py`、`inbox_store.py`,以及對應測試。站在攻擊者角度:被劫持的分析
行程(能送任意提案、能重送)、能改廣告的另一方。

## F1 收件口回應新帶的 block_code,讓送提案的一方能反查租戶的預算上限與廣告歸屬(跨租戶)

severity: major
blocking: 是 — 有完整、可執行的四步攻擊路徑(不需要額外前提假設),終點是外洩執行行程才該持有的
租戶機密設定(`max_budget` 精確值、campaign→租戶歸屬),命中嚴重度錨裡「資料外洩」那一類,不是縱深
防禦性質的假設風險
引句:「"replayed": result.replayed, "block_code": result.block_code}」

攻擊路徑:
1. **前提**:收件口 `accept` 明文承認「不執行提案、不呼叫 DSP、不做政策與租戶檢查」(`inbox_server.py`
   模組docstring),即任何呼叫端在**接收當下**可對任意 `campaign_id` 送提案,不限自己租戶名下的廣告。
   租戶與廣告的對應、以及每個租戶的 `max_budget` 只存在 `capability_signer.py` 讀的租戶設定檔裡,
   分析行程本身讀不到這份設定(`_tenant_of`/`sign` 只在執行行程內部用)。
2. **動作**:分析行程(或被劫持的分析行程)對同一個 task 連續送多筆修訂(`revision` 遞增,最多
   `MAX_REVISIONS_PER_TASK = 50` 次,見 `inbox_store.py`),每次換不同的 `campaign_id` 或不同的
   `new_budget` 值,再用完全相同內容重送(resend)拿回應。
3. **可觀察效果**:本次 diff 把 `Accepted.block_code` 接進 HTTP 回應體(`_accepted_body`,
   `inbox_server.py:135`),值域是封閉列舉 `campaign_not_found / campaign_not_active /
   version_changed / campaign_not_allowed / over_budget_cap / operation_previously_failed`
   (`inbox_store.py` 的 `BlockCode`)。`campaign_not_allowed` 直接來自
   `capability_signer.CapabilitySigner._tenant_of`(廣告不屬於任何租戶就丟這個),
   `over_budget_cap` 直接來自 `CapabilitySigner.sign`(`new_budget > tenant.max_budget`)。改動前
   這兩個代碼只在執行行程內部流轉,不回傳給呼叫者;改動後透過這支查詢外顯。
4. **為什麼要緊**:對同一個 `campaign_id` 反覆送不同 `new_budget` 觀察 `over_budget_cap` 是否翻轉,
   幾次之內就能二分逼近出該租戶設定檔裡 `max_budget` 的精確值——這是 PROMPT 裡點名的「預算上限」;
   對任意 `campaign_id` 送一次觀察 `campaign_not_allowed`,能枚舉出哪些廣告屬於本租戶之外的其他租戶
   (等於探出「其他廣告的租戶歸屬狀態」)。這正是分析行程依威脅模型不該知道、且過去程式碼設計上刻意
   不讓它知道的資訊(執行前檢查與簽發是執行行程的職責,收件口本身「不做政策與租戶檢查」)。

判準:[S300] 讓分析側拿到擋下原因是使用者核定的設計要求,這件事本身不算違規;但核定的是「讓分析側
知道自己那筆提案為什麼被擋」,不是「讓分析側能反查其他租戶的機密設定值」——這裡的攻擊路徑不假設
任何額外弱點,單純沿著這支 diff 打開的回應欄位走到底就外洩了 `max_budget` 與跨租戶歸屬,屬於嚴重度
錨裡「資料外洩、繞過權限(範圍檢查)」的直接可利用類,不是縱深防禦類的假設風險,所以判 major 且擋
推送:要嘛把 `block_code` 限縮到不含 `campaign_not_allowed` / `over_budget_cap` 這種洩漏設定值的
代碼(對外只給「blocked/其他原因見審計」之類粗粒度訊號),要嘛在收件口 accept 階段補上「這個
`campaign_id` 是不是送件端有權碰的」檢查,兩者擇一補了才放行。

## F2 版本衝突次數無上限累積,重複衝突可放大成本(推論)

severity: minor
blocking: 否 — 有既有上限與清理機制部分抵銷,且不繞過執行閘,推論性質
引句:「嘗試紀錄只增不改、不會被清,是執行側衝突」

攻擊路徑(推論):
1. **前提**:威脅模型明列「能改廣告的另一方」;`attempts` 表本身(非本次新增,但新函式
   `version_conflict_count` 的docstring自己承認)「只增不改、不會被清」,跟 `proposals` 表有
   `RETENTION = timedelta(hours=2)` 定期清不同。
2. **動作**:另一方在執行行程通過執行前檢查、寫入 DSP 前反覆改動同一支廣告,持續觸發 DSP 端
   409 版本衝突。
3. **可觀察效果**:每次衝突都在 `attempts` 多一列 `("failed", "version_conflict")`,收件表對應鍵被
   `block_code_for_failure` 標成 `version_changed`,依 [S310] 語意會驅使分析端重新規劃——但重新規劃
   仍要走收件口既有的修訂遞增與上限(單一 task 最多 50 次修訂、收件表總列數上限 `MAX_ROWS = 5000`),
   不會繞過執行閘(符合筆記裡 F3 invariant「就算允許重做分析也不能繞過執行閘」,本次 diff 沒有動到
   那條路徑)。
4. **為什麼只列 minor**:攻擊者要付出對等成本(自己也要不斷改廣告),且既有的修訂數與收件表列數上限
   把單一 task 的放大限制住;真正沒有上限的是 `attempts` 表本身的列數成長,但這是既有設計(此 diff
   只是新增一支唯讀查詢把它暴露出來,不是製造这个洞),沒有實測驗證能不能真的把磁碟用滿,故標推論。

## 逐類檢查

1. **不可信輸入流到危險操作**:`version_conflict_count` 與 `unresolved_count` 同款,`campaign_id`
   一律走 `?` 綁定參數,SQL 文字裡只用固定字串拼接 WHERE 子句結構(`noqa: S608` 的解釋屬實);
   `_accept_in_transaction` 新增的 `CASE WHEN disposition = ?` 同樣是綁定參數。已看,無注入。
2. **權限與範圍**:見 F1——收件口在接收階段本來就不做租戶檢查(既有設計),但這次把執行階段才算出來
   的租戶/預算資訊回傳給送件端,等於把「執行行程才該知道的範圍判斷結果」洩漏一部分給送件端,範圍
   控管出現退化。
3. **密鑰與個資**:`block_code` 是封閉列舉裡的固定字串,不含租戶名稱、金鑰、個資本身;沒有新增
   log 或錯誤訊息回顯請求內容的路徑(`_log_invalid_proposal` 未改動、仍只記錯誤類別)。已看,無R16
   字面違規(密鑰/個資本身沒外洩),但列舉值本身構成 F1 的推論 oracle。
4. **加密與隨機數**:此 diff 未碰簽章、隨機數或金鑰產生邏輯。已看,無。
5. **執行邊界**:`Executor._ack_terminal`、`_settle_existing`、取件時三處呼叫
   `block_code_for_failure` 都只改「回報哪個 BlockCode」,沒有改變狀態機本身(仍是 FAILED→ack_blocked
   同一條路徑,只換標籤);不影響 F1/F2/F3 那幾條 ★INVARIANT★ 綁定的狀態轉換測試。已看,無新的執行
   邊界繞過。
6. **行動端**:不適用(本次改動是後端 executor/inbox 模組)。已看,無。

## 筆記對照

「分析行程流程與檢查點」與「執行迴圈」兩篇的 ★INVARIANT★(F1/F2/F3 事故、提案收件口的去重與遞增
規則)在這次 diff 裡都沒被動到寫入路徑本身,只是回應多帶一個唯讀欄位——找不到與筆記字面衝突之處,
判斷以程式碼行為為準:F1 這個資訊揭露問題筆記沒有記過,值得事後補一筆 PITFALL 或在
「提案收件口.md」加一條 RULE 說明 `block_code` 外顯後的權限影響範圍,供之後查閱。
