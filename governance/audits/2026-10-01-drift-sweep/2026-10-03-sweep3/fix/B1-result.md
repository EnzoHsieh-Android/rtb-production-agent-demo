# B1 修正結果(2026-10-03,工作目錄 /Users/enzo/rtb-lumos-update)

格式:`筆記:行 | 形狀 | 處置 | 怎麼修或為什麼沒修 | 驗證證據`(行號是修之前的)

## 逐列

Systems/共用行程基礎:60 | P2 | 沒修:要人裁 | REVISIT 的 when-status 條件確實早已成立,評估也確實沒做;但「要不要改成伺服器只准讀宣告過的請求標頭」是在接受殘留風險(2026-09-22 兩條合約由使用者人裁蓋章,殘留風險「只靠代碼審把關」)與加一層結構檢查之間取捨,照規則 6 記要人裁、該行不改。給使用者裁決用的現況:沒有任何宣告清單機制;請求標頭讀取點是 httpkit 的 `single_header`(Host、X-Fault、Sec-Fetch-Site、Origin、Content-Length、Content-Type)加一處 `self.headers.get("Transfer-Encoding")`,子類別另有收件口 `self.headers.get("Origin")`、`single_header("Content-Type")`,DSP `single_header` 讀稽核金鑰、能力憑證、Idempotency-Key;任何子類別都能直接讀 `self.headers`,沒有機械擋 | `rg -n "self.headers|single_header\(" src/rtb`;`grep -n "allowed_headers\|declared" src/rtb/httpkit.py` 無結果;Phase 3 計劃 status: done
Systems/共用行程基礎:57 | P2 | 已修 | 跟第 55 行合成一條:`REVISIT:2026-10-20 評估三支伺服器要不要每來源的連線配額,連同全域上限的風險接不接受一起請使用者裁定(原本綁「提案收件口上線前」,收件口早已上線)`;刪掉已成立的 `[when-file:src/rtb/executor/inbox_server.py]` | `ls src/rtb/executor/inbox_server.py` 存在;2026-10-20 還沒到
Systems/共用行程基礎:55 | H1 | 已修 | 「見上面每來源連線配額的評估」那條刪掉,併進上一列的單一 REVISIT,不再有位置指稱 | 同篇修後只剩一條連線配額 REVISIT
Systems/共用行程基礎:54 | S1 | 已修 | 「只給本機的單一呼叫者用」改寫成現況:用這份基底的伺服器有三支(DSP:分析端、執行端、維運指令;收件口:分析端送提案;展示網頁伺服器:本機瀏覽器),都只綁回送位址、來源位址都是 127.0.0.1;明寫當初接受風險的前提已不成立,風險接不接受要人裁(併進同一條 REVISIT);附可重跑查詢 `rg -n "\(KitServer\)|\(JsonHandler\)" src/rtb` | 子類別:dsp/server.py:129/347、executor/inbox_server.py:77/157、demo/server.py:490/522;`request_json(` 呼叫者:analyzer/dsp_client.py、analyzer/inbox_client.py、executor/dsp_client.py、ops/trace.py、ops/side_effects.py;src 裡沒有其他 urlopen/HTTPConnection
Systems/任務流程領域模型:40 | S1/P1 | 已修 | 「Phase 3 執行前重新授權時會再用一次」改成:唯一呼叫者是分析端正式決策規則(附 `rg -n "check_freshness\(" src/rtb`);執行行程不呼叫它,執行前靠自己的檢查(決策過期、重讀 DSP 比廣告版本、政策版本、決策過時),連到 [[Systems/執行迴圈]] | check_freshness 只在 analyzer/policy.py:69 被呼叫;executor/execution.py:361-364(VERSION_CHANGED、POLICY_VERSION_CHANGED)、543/550(decision_expires_at)、604/618(decision_stale)
Systems/任務流程領域模型:96 | H2 | 已修 | 句尾補「已解決:展示批次重錄入庫為 phase14-demo-20260927(recordings/model/phase14-demo/),改由 test_committed_demo_recordings_replay_f1_to_f6_with_narratives 守,見 Verification/Phase14增量3驗證紀錄」 | Phase14 計劃:171 的 10-02 更正;`ls recordings/model/phase14-demo/` 有檔;tests/demo/test_ai_demo.py:364 有該測試;Phase14增量3驗證紀錄 有 phase14-demo-20260927
Systems/任務流程領域模型:63 | S1 | 已修 | 改成「check_freshness 的年齡上限照舊由呼叫端傳入,分析端集中用 analyzer/policy.py 的 MAX_EVIDENCE_AGE(暫用值)」,附 `rg -n "MAX_EVIDENCE_AGE" src/rtb` | policy.py:50 `MAX_EVIDENCE_AGE = timedelta(minutes=15)  # 暫用值`;rule_round.py:144、dsp_client.py:32/332、demo/basis.py:67 都用它
Systems/任務流程領域模型:65 | U1 | 已修 | 改寫成:增量 1 當初沒先過設計審、狀態機與證據格式在增量 2 設計審前定型(理由連 Phase2 計劃〈設計審的安排〉);Phase 2 已驗收通過,「審查員可能要求修改」這個開篇風險已不再待決。沒照稽核員寫「經過後續多輪設計審」,因為沒查到增量 1 本身補審過 | Verification/Phase2驗收紀錄 status: pass;Phase2 計劃:279、288
Systems/任務流程領域模型:6 | F1 | 已修 | `lumos set responsibility`:補上 worth.py(判斷點輸入型別、四值答案、5 格歸格)與 _checks.py(識別碼、整數與數字、時區、雜湊格式、固定兩位小數金額字串的唯一定義) | worth.py 有 CampaignStatus/WorthVerdict/WorthCell/WorthInput/cell_of;_checks.py 有 is_fixed_amount 等、is_id、is_sha256、is_aware、require_aware
Systems/確定性指標計算:6、36 | F1/H1 | 已修 | responsibility 補「收據用的精確比率(exact_value、exact_ratio、exact_change、exact_click_rate)與收據固定字串(percent_text、receipt_*)」;第 36 行補同樣內容並指向下文〈收據的精確比率與固定字串〉,附 `rg -n "^def " src/rtb/domain/metrics.py` | metrics.py:195-277 那組函式
Systems/確定性指標計算:47 | X9 | 已修(稽核員建議的改法有一半不對) | 10-02 註記與 REVISIT 改寫:正式規則只用 pacing(),時間比例固定傳 1/24,時間比例為 0 那條分支正式路徑走不到;cvr() 在 src/rtb 沒有呼叫端;九條匯入的是精確比率那組。稽核員建議寫「轉換多於點擊的語意由 exact_* 沿用」不成立:九條(逐日列、長窗)與 worth.py 歸格都把轉換多於點擊判成資料異常(歸格那條是使用者 2026-09-24 裁定),跟 cvr 的取捨相反,所以照實寫相反。REVISIT 換成還沒做的事:2026-11-21 決定 cvr 要不要改成跟正式路徑一致或刪掉;正式規則改傳可變時間比例時重看 pacing 的取捨 | `rg -n "\bcvr\(" src/rtb` 只剩定義;policy.py:35 匯入 pacing、:48 ELAPSED_FRACTION_1H = 1/24、:281;nine_rules.py:269/308/354 用 m.exact_*、:322-331 `_invalid_daily_row`、:370-376 `_invalid_window` 都把 conversions > clicks 判不合格;worth.py:85
Systems/有界標籤的指標:6 | F1 | 已修 | `lumos set responsibility`:資料來源補「與模型花費帳」 | ops/metrics.py:810-870 `_model_samples`、:948 `--model-ledger`
Verification/事故F5_不可信文字不能擴權:5 | F1 | 已修 | updated 改 2026-10-03(用 lumos set) | 同篇正文 2026-10-03 改 superseded;git log 最後一次改動 aef0f5d 2026-10-03
Projects/RTB_Phase13AI參與決策_計劃:706 | X9/M2 | 已修(先前) | 協調者已改綁 [S1427] 的 test_f5_adversarial_name_preserves_rule_and_traceable_narrative,本次沒動那行 | 工作樹 diff 確認
Projects/RTB_Phase13AI參與決策_計劃:196-205、281-298、359-369、582-597、599-612 | H3 | 已修 | 五節開頭各補一行「[歷史:Phase 14 增量 3 已撤除 AI 決策路徑,本節是當時設計]」橫幅,寫明哪些已撤、哪些照舊成立,各附可重跑查詢。〈核心原則〉:正式路徑沒有 AI 退回與「這次改由程式規則決定」,只選列舉值與程式先過濾只剩評估 Judge,執行端不知道 AI 照舊;〈一件工作的一生〉:runner 不收 --ai-judge、AI 答值得加只記原始答案不建提案;〈提案與金額〉:提案只由規則輪照九條建,暫停廣告由九條第 1 條結案;〈展示頁怎麼顯示〉:流程圖沒有 AI 選下一步,歸 AI 的節點只剩模型說明,這次誰決定新跑一律九條;〈F5 對抗案例的預期〉:雙胞胎、--hold-submit、考題整組刪,F5 改驗 [S1427] 那幾項 | ai_judge.py:121-124(PROPOSE → RuleContinue);`rg -n "ai_judge.Judge\(" src/rtb` 只剩 eval/investigation_eval.py:121;runner.py add_argument 只剩 --db/--dsp-url/--inbox-url/--timeout-seconds/--interval-seconds/--owner;flow.py:63-66 AI_NODES={"a_narrate"};present.py:81-83 DECIDED_BY_TEXT;policy.py:149-150、:252-257(PAUSED 由 _base_rule 結案)、:329 build_proposal;nine_rules.py:429-430;test_f5_end_to_end.py:191-205
Projects/RTB_Phase13AI參與決策_計劃:758、766、768、771、777、785、787 | H3 | 已修 | 七條(S1144、S1149、S1150、S1152、S1156、S1160、S1161)主行補 `[status:superseded] [被取代:[[Projects/RTB_Phase14正式規則照九條判斷_計劃]]]`,原 [manual:] 保留。同根因另補 S1125(子行寫「撤除三種 AI 標示的截圖驗收」,稽核員漏列)。S1139、S1115、S1143、S1145、S1164、S1167 子行是「保留一半/改寫」,不標 superseded | 逐條核程式:四支舊測試(renewed_receipt、renewal_reads_the_clock、busy_renewal、pending_stop)在 tests 都不存在;src 沒有 RenewalSkipped;runner 沒有 preflight_login;driver.py:78-79 寫 not_exercised 隨增量 3 撤除、只剩頁面唯讀顯示舊紀錄;policy.py:149 寫 exam_hold 已撤;Phase14 計劃 282-303 翻案索引逐條寫撤除
Projects/RTB_Phase13AI參與決策_計劃:643 | X9 | 已修 | 「README.md 開頭的做法三條」改成「README.md 開頭『這個 Demo 的做法』那幾條」(不寫條數,免得再漂) | README.md:10-15 是四條
Projects/RTB_Phase13AI參與決策_計劃:941 | X9 | 已修 | 直接擴充同一句既有的 10-02 更正括號(沒疊新括號):決定紀錄已照入庫批次重產,不再是「72 筆找不到錄製、模型沒量」,現在寫找不到錄製 0 筆、模型有量(名稱正常 36 筆裡有效答案 23 筆,延遲中位 4236 毫秒),結論仍是不採用 | governance/eval/phase13-investigation-adoption.md:4、:14「找不到錄製:0 筆」、:12「結論:不採用」;recordings/model/phase13-investigation-eval/ 84 份;git log 3edf2ae、b2b17ea

## 轉給別篇

- Systems/一鍵展示:249「展示狀態庫裡舊的 not_exercised 與 exam、answered_rounds 欄位只唯讀略過(`state_store._RETIRED_DETAIL_KEYS`)」:not_exercised 不在略過清單裡。`_RETIRED_DETAIL_KEYS = frozenset({"exam", "answered_rounds"})`(state_store.py:175);not_exercised 是情境狀態值,present.py:78 照樣對到 `ScenarioStatus.NOT_EXERCISED`,page.py:464/469/729 照樣顯示,driver.py:78-79 也寫「只由頁面唯讀顯示」。建議改成「舊的 not_exercised 狀態照樣讀出、頁面唯讀顯示;exam、answered_rounds 欄位讀回時略過」。

## 程式要改(交協調者)

- tests/httpclient/test_httpclient.py:143 `test_client_headers_are_exactly_idempotency_key_and_capability`:名稱說兩個,實際斷言三個(另有 DSP 稽核金鑰,:150-151)。建議改名成 `test_client_headers_are_exactly_the_three_documented_headers`,同時改 Systems/共用行程基礎 第 36 行的 [test:] 綁定。(稽核員 B1 提的,沒有另外重驗斷言內容以外的事)
- tests/analyzer/test_ai_judge.py:277-293 `test_an_injected_name_can_only_flip_propose_or_not`:比金額、廣告、動作的斷言放在 `if isinstance(result, ProposalDecision)` 底下,Judge 現在不會回 ProposalDecision(ai_judge.py:121-124 propose → RuleContinue),那段是死碼;真走進去還會因為 `normal.result` 是 RuleContinue 而丟 AttributeError。docstring:278「送出的只可能是照公式那一份提案或沒有提案」也過時。建議改成斷言 Judge 帶誘導名稱時只回 RuleContinue 或 NoAction、不會有提案,並刪掉死碼。
- 另外的觀察(沒要求改):src/rtb/demo/state.py:43 註解「Phase 13 [S1144]:受測的工作 AI 合法判不提案」講的是舊狀態值的來源,沒標它已撤除、只剩舊紀錄;要不要補一句交協調者決定。

## lint 結果

- Projects/RTB_Phase13AI參與決策_計劃:0 問題
- Systems/共用行程基礎:0 問題
- Systems/任務流程領域模型:0 error / 1 warning(既有 RULE「2026-09-22 增量 2 新增轉人工類代碼『憑證被拒』」缺 since/retire,不是這次寫的,沒動)
- Systems/確定性指標計算:0 問題
- Systems/有界標籤的指標:0 問題
- Verification/事故F5_不可信文字不能擴權:0 問題
- 宣稱驗證器、Phase3驗收紀錄、稽核表只增不改守衛、Issues/說明提示仍寫給核可的人看:B1.txt 沒有這幾篇的列(稽核員讀完說沒漂移),沒改

## updated

改過的篇都已 `lumos set … updated 2026-10-03`:共用行程基礎、任務流程領域模型、確定性指標計算、有界標籤的指標、事故F5_不可信文字不能擴權;Phase13 計劃本來就是 2026-10-03。
