# B6 修正結果(工作目錄 /Users/enzo/rtb-lumos-update,2026-10-03)

筆記:行 | 形狀 | 處置 | 怎麼修或為什麼沒修 | 驗證證據

- Systems/執行迴圈:22 | X9 | 已修 | 事故 F3 的 WHY 括號改成:原本掛在 Phase4 計劃增量 3b 的日期保底回頭條件 2026-10-03 因寫下時就已成立、使用者裁定撤除;補「同一則訊息對應同一個任務編號」合約目前沒有排程。不再指向不存在的 REVISIT | Phase4 計劃現存 REVISIT 只有 129/327/330/515 行,都不是這條;同計劃 506 行寫明撤除、目前沒有排程;`git log -1 aef0f5d`;src/rtb/analyzer/runner.py 的 argparse 只有 --db/--dsp-url/--inbox-url 等,沒有訊息入口
- Systems/外部寫入嘗試紀錄:28 | H2 | 已修 | TEST 行「計數算法與負數擋下」改成「計數算法與資料庫拒收同一把鍵的第二列終點列」 | attempt_store.py:533 `if count < 0` 分支;`grep -rn "未結案計數為負" tests/` 0 筆;test_attempt_store.py:419 那支只驗 sqlite3.IntegrityError,不經 unresolved_count 的負數分支
- Projects/RTB_Phase6權限護欄與總曝險_計劃:466 | E2 | 已修 | 已完成計劃,句後加「(2026-10-03 更正:專案裡沒有任何啟動說明寫這件事…只記在 [[Systems/執行迴圈]]〈政策版本與決策新鮮度〉的部署 RULE)」並附重查指令 | `rg -n "重啟" README.md src/rtb/executor/runner.py src/rtb/analyzer/runner.py src/rtb/domain/proposal.py`:README 只有 F2 故障表一行,其他都跟換版無關;proposal.py:23-27 常數註解沒寫重啟
- Projects/README流程動圖_計劃:19 | P1 | 已修(要不要補做 SVG 頁面目視:要人裁) | 句尾加 2026-10-03 更正:那篇 revalidate_when 不含 11B 增量 2 或 Phase 12,這個觸發點不存在;兩件事都已發生(AI 說明接上後 2026-09-28 已重畫重驗;Phase 12 展示頁沒放這張 SVG,只有 README 連結);SVG 瀏覽器目視至今沒做、沒有回頭入口,要不要補做要人裁 | Verification/README流程動圖驗證.md:8 revalidate_when 內文;`rg -n "agent-flow" README.md src tools tests` 只有 README.md:21、23;Phase12 計劃 status: done
- Verification/README流程動圖驗證:51 | P1 | 已修(同上,要人裁) | 句後加 2026-10-03 更正:本篇 revalidate_when 不含這個事件;Phase 12 已完成但展示頁沒放 SVG,重驗從沒被觸發,瀏覽器目視沒做,要不要補做要人裁,指向計劃 | 同上
- Projects/RTB_Phase7提示注入與信任邊界_計劃:48 | X9 | 已修 | 直接改正 2026-10-02 那個寫錯的更正括號(沒疊新的):判斷用同一輪四查詢存在調查原始資料表的原始回應(白名單驗過);四種收據跟著證據傳進去,只用來判新鮮度與列進提案的證據參照,不進九條判斷。比稽核員多驗出一點:收據也會列進提案的 evidence_refs | rule_round.py:243-248 `_round_queries` 讀 `reads.raw_query`、191-194 `_raw`;rule_round.py:314-316 收據傳進 policy.steps/explain;policy.py:63 `_all_fresh` 看全部證據、269-270 `_payload` 只取 CAMPAIGN_STATE/METRICS、286-291 判斷走 queries;policy.py:329-352 build_proposal 的 evidence_refs 是傳進來的每一筆
- Systems/執行迴圈:25 | M2 | 沒修:要人裁 | 這是 F7 的 ★INVARIANT★ 合約行,協調者明令不准動(合約有獨立審計);把 test_an_approval_does_not_hold_for_a_proposal_from_another_policy_version 補進 [test:] 清單要走合約審計或使用者裁定 | 確認該測試不在第 25 行 [test:] 的 15 支裡;本篇 2026-10-03 新段與 kill_recipes 最後一條配方已綁它;tests/executor/test_stale_decision.py 有這支
- Systems/外部寫入嘗試紀錄:45 | H1 | 已修 | 改成「以純函式為主,另有寫入與唯讀兩個交易物件型別(ExecutorTransaction、ReadTransaction,都只包著收件口交易入口或唯讀開法交進來的那一筆交易)和幾個資料類別(如 LegacyMemo);這支模組自己不開連線」 | attempt_store.py:190 LegacyMemo、205 ExecutorTransaction、229 ReadTransaction(兩者都由建構參數收 conn、檢查私有發行憑證)
- Systems/外部寫入嘗試紀錄:6 | F1 | 已修 | lumos set responsibility,補:總曝險預留與已用額度(資料庫端快路徑與退回的原算法)、DSP 呼叫紀錄表的表結構與寫入、給指標與追蹤檢視的時間窗口與游標讀取、第一列記下的開始時核對材料。用「表結構」而非「建表」,因為同篇 45 行寫建表由收件口做 | attempt_store.py:77 dsp_calls 表結構、763 aggregate_used、799 aggregate_used_reference、1067 record_dsp_call、1093/1118/1134 窗口讀取、1233-1244 游標讀取、1183 first_rows_for
- Systems/執行迴圈:6 | F1 | 已修 | lumos set responsibility,補:每輪處理待核可、寫結果碰到資料庫忙碌時在限度內重試、護欄模組的單筆加預算比例上限與決策新鮮度判斷 | guardrails.py:13-43(increase_too_large、decision_stale);runner.py:1 檔頭「每輪先對帳、再處理待核可、再處理一筆」;execution.py:426-433 RESULT_WRITE_RETRIES
- Systems/執行迴圈:39-46 | F1 | 已修 | lumos append verified_by [[Verification/F7效能驗收紀錄]] | F7效能驗收紀錄 valid_under 列「寫結果碰到資料庫忙碌時在限度內重試」、第 36-47 行 [S691] 30 次與啟動程式睡眠接線測試;實作在 execution.py:426-433
- Systems/執行迴圈:133 | S3 | 已修 | 改成「收件表沒有待處理的舊任務兩小時後整個清掉、收件口的拒收事件表每種事件代碼只留最新 200 筆」,附 `rg -n "^RETENTION|^MAX_EVENTS_PER_CODE" src/rtb/executor/inbox_store.py` | inbox_store.py:67 RETENTION=2h、69 MAX_EVENTS_PER_CODE=200、1565 docstring「只記格式合法的提案被拒收」、1590-1592 依代碼刪
- Issues/共用HTTP伺服器連線名額測試在CI偶發逾時:38 | L1 | 已修(記下)+ 程式要改 | 已結案問題單,在「什麼條件算修好」後加 2026-10-03 更正:兄弟測試仍用固定等待(0.2 秒後開第三條;關一條後 0.3 秒斷言名額還回來,後者等的就是本篇根因的還名額時間差);結案當天就關、沒觀察過「CI 連續多次推送」;沒有重開。加 REVISIT:2026-11-30 看兄弟測試改了沒。測試本身不能在這裡改,交協調者 | tests/kit/test_httpkit.py:414-428(sleep 0.2、0.3);同檔 466 `_wait_until_free_slots`;問題單 created/updated 原本同為 2026-09-22。只說 0.3 秒那段是同根因;0.2 秒那段在 listen 佇列先進先出下不一定會錯,沒下同一句結論

統計:已修 12 列(其中 README 兩列的「要不要補做 SVG 目視」另記要人裁;問題單那列的測試修改另記程式要改);沒修 1 列(要人裁:執行迴圈:25 F7 合約 [test:] 清單)。

## 轉給別篇

- Verification/Phase4驗收紀錄:40:2026-10-02 更正寫「回頭條件改掛在 [[Projects/RTB_Phase4佇列與重新投遞_計劃]] 的 REVISIT」,那條 2026-10-03 已撤除(Phase4 計劃 506 行)。建議改成「原本掛在 Phase4 計劃的日期保底回頭條件 2026-10-03 已撤除,補這條合約目前沒有排程」(直接改正那個括號,不疊新的)。
- Verification/Phase4驗收紀錄:6 revalidate_when 最後一段「分析行程加啟動程式時,補『同一則訊息穩定對應同一個任務編號』的合約」:這個事件 2026-09-24 已發生(分析端驅動命令列 src/rtb/analyzer/runner.py),但沒有訊息入口、合約沒補;這個觸發已不會再響。建議改成綁「分析行程新增訊息入口時」,或依 Phase4 計劃的撤除裁定拿掉。

## 程式要改

- tests/kit/test_httpkit.py:418-427 test_connections_beyond_the_cap_are_dropped_and_slots_come_back:把 `time.sleep(0.3)`(關掉 held[0] 後等名額還回來)改成 `_wait_until_free_slots(srv, 1)`;`time.sleep(0.2)` 可改成 `_wait_until_free_slots(srv, 0)` 讓兩支寫法一致。改了之後問題單那條 REVISIT 2026-11-30 可以拿掉。
- docs/assets/make_agent_flow_gif.py:1 檔頭「對照 src/rtb/demo/flow.py」:已在 code.txt 第 6 列,不重複。

## 注意(不屬於本清單但看到的)

- 工作目錄裡有大量別的會談的未提交改動(48 篇筆記),我只動了下面 7 篇;Systems/執行迴圈 的 kill_recipes 與所有 ★INVARIANT★ 行用 git diff 確認沒動。
- Phase9驗收紀錄、Phase14增量4驗證紀錄、Phase8驗收紀錄、Phase2驗收紀錄、Phase12增量3驗收紀錄:本清單沒有列它們的問題,傳播搜尋也沒有要改的句子,沒動。

## lint

- Systems/執行迴圈:0 問題
- Systems/外部寫入嘗試紀錄:0 問題
- Projects/RTB_Phase6權限護欄與總曝險_計劃:0 問題
- Projects/RTB_Phase7提示注入與信任邊界_計劃:0 問題
- Projects/README流程動圖_計劃:0 問題
- Verification/README流程動圖驗證:0 問題
- Issues/共用HTTP伺服器連線名額測試在CI偶發逾時:0 問題
(以上 7 篇 updated 都已用 lumos set 改成 2026-10-03)
