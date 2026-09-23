severity: clean

# 外家否決席(代 Codex)審查報告:Phase 5 執行側(r1-snapshot.patch)

只找會擋合併的問題(blocker/major)。逐 hunk 讀完整份 diff,並在臨時目錄(mktemp -d 複製 src/tests)實跑,沒有動 repo。結論:沒有找到會擋合併的問題。

### 已看:[S310] 三處確認依結果代碼分流
- execution.py 的 `_ack_terminal`、開始一筆撞到既有失敗鍵(`code = block_code_for_failure(begun.row.code)`)、inbox_store 的 `_settle_existing`(`self._settle_existing(tx, receipt, existing.state, existing.code, now)`),三處都改成呼叫共用的 `block_code_for_failure`,repo 裡沒有第四處寫死 `OPERATION_PREVIOUSLY_FAILED`(grep src 只剩列舉定義和這個函式本身)。
- 殺傷力實測(臨時目錄裡逐處改回寫死舊代碼,各跑一次):改回 `_ack_terminal` 那處,2 支測試翻紅(含 S309);改回開始一筆那處,`test_a_dsp_version_conflict_is_acknowledged_as_version_changed` 翻紅;改回取件那處,`test_a_failed_attempt_blocks_the_new_delivery` 翻紅,另外 1 支也紅。三處都有測試守著。
- 其他失敗照舊:`test_other_failures_are_still_acknowledged_as_previously_failed` 有覆蓋;test_crash_recovery:296、test_reconcile:286、test_execution:513 原本斷言「同一操作先前已失敗」,改完仍然通過,代表那幾條走的是非版本衝突的失敗,沒被誤改。

### 已看:「版本已變」擴大到 DSP 端的衝突,會不會讓沒寫過的誤判成可以重做、或已寫過的被當成沒寫過
- DSP 的判定順序是先查冪等紀錄,再查作廢,最後才比版本(`/Users/enzo/rtb-3b/src/rtb/dsp/store.py:370-380`)。所以同一把鍵只要寫進去過,重送一定拿到「重播成功」,不可能拿到 409。收到 409 就代表這把鍵確實沒寫進去。結論:分析端據此另開接續任務,不會造成同一決策寫兩次,也符合事故 F1(結果不明時只用原鍵對帳)。
- 作廢的 409(operation_voided)在回應對照表裡排在版本衝突前面,記的是「沒發生」,不會被當成版本已變。DSP 伺服器只會回這兩種 409(`/Users/enzo/rtb-3b/src/rtb/dsp/server.py:67-68`)。
- 對帳只看「沒有終點列」的鍵。這次只改確認時寫的擋下原因,嘗試的終點狀態(失敗)沒動,所以對帳不會以為這把鍵沒寫過而再送一次。已擋下的提案也不會再被取件,因為處置已經確認,取件條件沒改。
- 接續任務用的是新任務編號,冪等鍵跟著換新,不會撞到舊鍵的失敗紀錄。

### 已看:[S300] 收件口回應本文多一個鍵 block_code,是否跟分析側約定的介面一致
- 第一次收下(回 201)走的是 `Accepted(..., "pending", digest, False)`,block_code 用預設空值;重送(回 200)用 SQL 的 `CASE WHEN disposition = ? THEN block_code END` 取值,只有已擋下時才有值。`_accepted_body` 一律輸出這個鍵,所以鍵永遠存在、沒擋下一律是 null、擋下時值是擋下原因字串(BlockCode 是字串列舉,sqlite 存的就是字串)。
- 對照分析側(`/Users/enzo/rtb-production-agent-demo/src/rtb/analyzer/flow.py:315-334`,`/Users/enzo/rtb-production-agent-demo/src/rtb/analyzer/inbox_client.py`):分析側規定「只有已擋下才可以有原因」,有原因就必須是字串,並用字串 "version_changed" 比對。執行側的輸出剛好滿足這些條件。
- 會不會出現「已擋下卻沒有原因」:repo 裡寫入「已擋下」的地方只有 `ack_blocked`,每次都會一起寫擋下原因(`/Users/enzo/rtb-3b/src/rtb/executor/inbox_store.py:575`),所以不會。
- 合約「同一個任務的同一個修訂只收一份」:這次只在既有查詢多選一欄,沒有新增寫入,內容相同的重送仍然不會多一筆。

### 已看:[S309] 兩套系統同時寫同一廣告
- 測試用柵欄讓兩邊都讀完、檢查都通過之後才一起寫,DSP 只接受一方,落敗那方的嘗試記版本衝突、收件表記版本已變。單獨連跑 5 次都通過;把 `_ack_terminal` 改回舊代碼時這支會翻紅。

### 已看:[S315 執行側] 衝突次數查詢
- 唯讀的 SELECT COUNT,SQL 只拼接固定字串,參數都有綁定。失敗是終點狀態,每把鍵最多一列終點列,所以一次 DSP 衝突只算一次。可以依廣告篩,測試覆蓋三個廣告加一種別的失敗。

### 已看:整體回歸
- 臨時目錄跑 tests/executor 全套:324 支全過。

⚠ 交編排者:`/Users/enzo/rtb-3b/docs/rtb-production-agent-demo-knowledge/Systems/執行迴圈.md:50` 流程第 4 步還寫著「鍵已存在…失敗 → 擋下『同一操作先前已失敗』」,跟同一篇第 30 行新加的 RULE 和這次的程式行為對不上(版本衝突造成的失敗現在寫「版本已變」)。這一行不在審查的 diff 裡,只是筆記前後不一致,不影響行為,建議順手更正。

沒有會擋合併的問題,最高等級為 clean,需要擋合併的條數是 0。
