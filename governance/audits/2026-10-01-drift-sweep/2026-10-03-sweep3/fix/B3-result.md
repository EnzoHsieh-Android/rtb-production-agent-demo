# B3 修正結果(2026-10-03)

工作目錄 /Users/enzo/rtb-lumos-update(HEAD 3f2d82c),只改筆記,沒有任何 git 寫入。

## 每列處置

筆記:行 | 形狀 | 處置 | 怎麼修或為什麼沒修 | 驗證證據
---|---|---|---|---
Systems/一鍵展示:72 | X9(兼 D1) | 已修 | 「補做或撤掉待裁定」改成「使用者 2026-10-03 裁定 AI 說明不進核可表單,收據不做、S1034 作廢」,連到已結案的 Issue 與 Phase 11B 計劃決策 d1 | Issues/確認頁顯示AI說明時的收據還沒做 status resolved+結案橫幅;Projects/RTB_Phase11B大模型接入_計劃 decisions d1(decided 2026-10-03);src/rtb/demo/present.py:325-326 `ApprovalForm(... narrative=None, source=None ...)`
Verification/Phase14增量3驗證紀錄:8 | V1 | 已修 | 重驗事件確實觸發(626cccb 改 narrate.py 送出的證據與提示、同批號重錄兩檔)。今天在 3f2d82c 上重跑,文末新增〈626cccb 重錄後的重驗〉一節記觸發與結果;valid_under 那行 10-03 補註改寫成講清楚「之前的重播通過指的是換新前兩檔」+ 626cccb 之後的重驗;正文 10-02 更正括號裡的位置指稱「開頭前兩個重驗事件」改成直接寫事件內容。status 保持 pass(重驗通過) | `git show 626cccb --stat`(narrate.py、phase14-demo 兩刪兩加);`git log -- recordings/model/phase14-demo` 最後一筆 626cccb;`pytest tests/demo/test_ai_demo.py` 20 passed(含 test_committed_demo_recordings_replay_f1_to_f6_with_narratives,已用 --collect-only 確認在內);`pytest tests/analyzer/test_narrate.py tests/demo/test_flow.py` 93 passed;F7 全規模 1 passed
Systems/一鍵展示:34 | S3 | 已修 | 拿掉 `[count:…MAPPED_ENUMS=18]` 標籤,改成純文字「(當時)是十八個列舉」;同行 =17 標籤保留 | `len(flow.MAPPED_ENUMS)` = 17
Systems/一鍵展示:101 | H1/E1 | 已修 | 刪掉「分析端路由」;補四個列舉到各自的家:處理待確認的結果、最後失敗原因 → 提案收件口;證據新鮮度、值不值得加的判定 → 任務流程領域模型;加一條可重跑查詢與「分析端路由 2026-09-27 起不在清單」的指向 | 逐一印 MAPPED_ENUMS 的模組:AwaitingOutcome/LastFailure 在 executor/inbox_store.py,Freshness 在 domain/evidence.py,WorthVerdict 在 domain/worth.py;各檔 about_code 的家用 `grep -rlx "  - <檔>" Systems` 查出;清單沒有 RoutePath
Systems/一鍵展示:94 | S3 | 已修 | 「本機一次約 9 秒」改成「Phase 12 舊規則時約 9 秒;改九條規則後每件讀 9 次平台,2026-09-27 實測 16.1 秒、2026-10-03 重跑 20.3 秒,時限 300 秒」,附重量指令 | 今天實跑 `pytest -s tests/demo/test_driver.py::test_f7_finishes_with_rule_reads_under_the_actual_allowance`:total_seconds 20.3、dsp_reads 2700(=300×9);16.1 秒出自 Verification/Phase14增量3驗證紀錄〈F7 全規模實測〉
Systems/一鍵展示:6 | F1 | 已修 | 用 lumos set 改 responsibility,補上暫存狀態庫、驅動程式、觀察判斷、重算根據、展示狀態換成頁面資料、展示伺服器(觸發、網頁核可簽發、另存靜態報告)、錄製批次入庫前檢查 | 讀 state_store/observe/driver/basis/present/server/recordings 七支模組開頭說明
Systems/提案收件口:6 | F1 | 已修 | 用 lumos set 改 responsibility,補上收件表兼佇列(取件、租約延長與放回、處置確認、接手)、死信信封/重放條件/稽核、停下紀錄表、核可表與核可使用表、生命週期事件、唯讀開法、整個執行行程資料庫唯一的交易入口(含外部寫入嘗試表);不負責項把「(那是 Phase 3)」改成「(在執行迴圈與寫入能力憑證)」,另寫明重放命令列不歸這篇 | src/rtb/executor/inbox_store.py 的表(dead_letters、dead_letter_ops、write_stops、approvals、approval_uses、lifecycle_events)與方法(_lease、extend、release、ack_*、take_over、replay、_replay_refusal、transaction、class ReadOnlyInbox:1595);ReadOnlyInbox 使用者是 demo/observe、demo/driver、ops/*、executor/observability;Systems/死信重放指令 responsibility 一致
Systems/提案收件口:90 | H1 | 已修 | 改成「(Phase 6 當時)兩個索引……Phase 9 增量 3 又加了依鍵與關卡的索引,現在是三個」,附查詢指令 | inbox_store.py:249-251 approval_uses_by_tenant/by_time/by_key
Systems/提案收件口:20、34、63 | P1 | 已修 | 第 20 行 retire 改「收件口有呼叫者認證、能擋掉不可信的呼叫者時重審」;第 34 行 retire 改「收件表被外部佇列取代時重審」;第 63 行括號改成「Phase 3 做的能力憑證與政策層管的是執行端簽發與寫入,收件口至今沒有呼叫者認證」並指到同篇那條 | `grep -i "auth\|token\|credential\|憑證\|認證" src/rtb/executor/inbox_server.py` 0 筆;同篇「佇列語意」節 WHY「收件表同時是提案佇列」;inbox_store.py `_lease` 直接寫 proposals 表
Systems/追蹤檢視:43 | S3 | 已修 | 句尾補「2026-09-25 起假說命令列多一個 9(預留時花費帳忙碌,只有它用)」,附查詢指令 | `rg -n "^EXIT_\w+ = " src/rtb/ops`:9 只在 hypothesis.py:59;`git log -S"EXIT_LEDGER_BUSY = 9"` → 10c4b9b 2026-09-25
Issues/每支檔有家在合併提交上誤擋:37、46 | W1 | 已修 | 兩條 REVISIT 都拿掉:第 37 行換成撤除說明(工具鏈已修好、624df28 推送照常過、已結案),第 46 行改成「原本的回頭條件……已完成(2026-10-03 撤掉回頭行)」,都指到〈2026-09-25 第二次結案〉 | `git log -1 624df28`:合併提交、在 main 裡;同篇第二次結案段落
Verification/事故F3_重複投遞不重複分析與副作用:5 | F1 | 已修 | lumos set updated 2026-10-03 | `git log -- …事故F3_….md` 最後一筆 aef0f5d 2026-10-03
Projects/RTB_Phase3外部寫入安全_計劃:195、198 | P2 | 已修(補回頭條件);runner 要不要清金鑰要人裁 | 在 09-30 回頭看那段後面加 2026-10-03 補註(照實寫 runner.py 不讀也不清環境變數,防線只有展示啟動器白名單,要不要改由使用者裁)與一行 `REVISIT:2026-12-31` 由使用者裁定 runner 要不要自己清三把金鑰並補測試、接受現狀就寫理由撤行。沒改程式(規則禁止,也是使用者的決定) | `rg -n environ src/rtb/analyzer/runner.py` 0 筆;src/rtb/capabilitykit.py:22/24/27 三把金鑰名稱;tests/demo/test_launcher.py:59-73 Role.ANALYZER 的白名單是空集合

另外順手修(同篇,規則 2 的位置指稱):
- Systems/提案收件口「(2026-10-03 撤除一條回頭條件……見上一條更正)」改成指名「見『收件表已有清理與保留期限』那條更正」。

指派清單裡的其他五篇(Projects/RTB_Phase11證據清單與驗證器_計劃、Issues/Phase9代使用者裁定待覆核、Verification/Phase12增量2驗收紀錄、Verification/Phase13增量3驗收紀錄、Systems/死信重放指令)沒有要修的列,搜尋同一件事時也沒有需要跟著改的句子,沒動。

## 轉給別篇

- Projects/RTB_Phase12一鍵展示與HTML報告_計劃 第 45 行與第 199 行:更正括號還寫「補做或撤掉待使用者裁定」。S1034 那篇 Issue 已在 2026-10-03 結案(使用者裁定 AI 說明不進核可表單、收據不做、S1034 作廢,Phase 11B 決策 d1);兩處括號直接改成「……2026-10-03 使用者裁定不做,S1034 作廢,見 [[Issues/確認頁顯示AI說明時的收據還沒做]]」(第 287 行已有同樣寫法可照抄)。
- Systems/分析行程流程與檢查點(src/rtb/analyzer/runner.py 的家):建議補一句「分析端驅動命令列不清金鑰環境變數,防線只有展示啟動器的環境白名單;要不要改待使用者裁定,見 [[Projects/RTB_Phase3外部寫入安全_計劃]] 2026-10-03 補註與 REVISIT」,讓改 runner.py 的人在那支檔的家就看得到。

## 程式要改

無(B3 的列都不是程式註解或測試名的問題)。Phase 3 那列的「runner 自己清金鑰並補測試」是要不要做的使用者決定,已記成要人裁+REVISIT,不是交協調者改註解。

## lint 結果(各篇 0 問題)

- Systems/一鍵展示:0
- Verification/Phase14增量3驗證紀錄:0
- Systems/提案收件口:0
- Systems/追蹤檢視:0
- Issues/每支檔有家在合併提交上誤擋:0
- Projects/RTB_Phase3外部寫入安全_計劃:0
- Verification/事故F3_重複投遞不重複分析與副作用:0

以上七篇的 updated 都已用 lumos set 改成 2026-10-03。
