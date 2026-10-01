# 2026-10-01 全圖譜漂移巡檢:發現清單

量測點:rtb main 4919ebb(工具鏈 v1.2)。五個唯讀稽核員分工:Systems 26 篇全文逐句讀(分四組);Issues 17 篇全文;Projects 18 篇只讀開頭欄位與標題含「現況/已知缺口/未排除/實務隱患」的段落;Verification 32 篇讀開頭欄位與正文第一節;MOC 比對索引。計劃正文(約 1.4 MB)沒有逐句讀。

行號是量測當時的行號。形狀代碼見同目錄 [shapes](#形狀代碼) 與 Issues/存量筆記漂移等工具修復〈形狀與修法(2026-10-01)〉。誤導程度:高=讀者照做會做錯或推翻正確的程式;中=會找不到東西或誤解現況;低=細節不準。

主控驗證過的:10 條殺傷力配方失配(逐條算 `old` 在目標檔出現次數);啟動器 child.py 的 parser 問題不是 bug(第 48 行已擋掉 INBOX,只有 DSP 與 EXECUTOR 走得到第 29 行),改列為「理由複製錯貼」。其餘是稽核員以 grep 對程式的判斷,沒逐筆重驗。

## 形狀代碼

| 代碼 | 名稱 |
|---|---|
| K1 | 殺傷力配方失配(kill recipe 的 old 在目標檔出現次數不是 1) |
| R1 | RULE/PITFALL 撤除條件已成立還保有效力 |
| D1 | 裁定被翻案、舊筆記沒改 |
| H1 | 同篇後蓋前(後段已改寫或宣告撤除,前段現在式句沒回頭改;含「同篇自相矛盾」「前段無橫幅」) |
| H2 | 雙生句只修一半(摘要與正文講同一件事,只改一邊) |
| H3 | 撤除橫幅混裝或少算(橫幅下活死混雜、現況只寫在橫幅下、「哪幾支已刪」少算) |
| T1 | 懸空 [test:](Systems/Verification 的綁定指到已刪或從沒存在的測試;doctor 只查計劃) |
| S1 | 新增了 X,舊句說沒有/只有/唯一/還沒 X |
| S3 | 數量或值變了、名稱沒變(含「N 支檔」與 about_code 不符、列舉成員數) |
| E1 | 列舉漏新成員(不帶數字的全稱列舉少了新成員) |
| P1 | 前提消解或死觸發(待辦/風險/回頭條件的前提已被拿掉,條件永遠不會成立) |
| P2 | 散文回頭條件事件已發生(沒寫成條件式) |
| V1 | 驗證紀錄 revalidate_when 事件已發生沒處理 |
| V2 | 階段性驗收沒收尾(寫「整份完成時併入/改寫」,一直沒做) |
| V3 | 驗收對象已撤除仍是 pass |
| U1 | 已完成計劃的「現況」段沒標快照 |
| A1 | 產物快照過期(筆記抄了入庫產物的數字,產物重產後沒跟) |
| A2 | 歷史段指向會變的常數(「逐字見某常數」但常數已換新版值) |
| W1 | 待辦段已完成,或待辦掛在已結案 Issue 上 |
| M1 | 指稱分裂、術語改義、同名換義(名稱還在,意思或所指變了) |
| M2 | 守衛換手(綁的測試還在、還綠,但跑的不是句子宣稱的正式路徑) |
| M3 | 理由複製錯貼(同一行 WHY 廣播到多篇,只對其中幾篇成立) |
| F1 | 開頭欄位落後(updated 戳記早於內文、responsibility 沒涵蓋新職責、Systems 節點 status 落後) |
| G1 | 驗證指令漏新守衛(摘要 TEST/VERIFY 指令沒包含管轄的測試檔) |
| C1 | 程式註解或測試名與筆記一起過期(以程式碼為準也裁不出) |

## 發現

### K1 殺傷力配方失配(10 條,全掛在 ★INVARIANT★ 上)

| 筆記 | 配方(合約/測試) | 失配原因 | 誤導 |
|---|---|---|---|
| Systems/執行迴圈 kill_recipes | F1 test_f1_timeout_before_commit… | old `self._sign(proposal, row.key)`;現在 `precheck(...) or self._sign(proposal)` | 高 |
| 同上 | F2 test_a_crash_before_the_terminal_commit_rolls_back… | `_ack_terminal(...)` 多了 block_code 參數 | 高 |
| 同上 | F4 test_a_version_conflict_hands_the_task_over… | flow.py 改查 `_BLOCK_CODES` 表 | 高 |
| 同上 | F7 test_an_unresolved_reservation_counts… | `if unresolved_count(tx):` 改成帶 open_keys 的條件式 | 高 |
| 同上 | F7 test_the_aggregate_limit_blocks_at_the_threshold… | `approved=AGGREGATE in live)` 後多了 ratio_allowance | 高 |
| Systems/提案收件口 kill_recipes | F6 test_a_dead_letter_is_never_handed_out_again | `if deliveries >= MAX_DELIVERIES:` 縮排 12→8 | 高 |
| 同上 | F6 test_a_replayed_proposal_goes_through_every_gate | 同上類重構 | 高 |
| 同上 | F6 test_a_dead_letter_leaves_a_durable_envelope(兩條) | `_record_dead_letter(...)` 參數改名;`_highest_revision` 前後文換成 `_has_pending` | 高 |
| Systems/Mock-DSP kill_recipes | 要嘛全部生效 test_failure_between_state_change_and_idempotency… | old 在目標檔出現 4 次(要求剛好 1 次) | 高 |

### 高誤導

| 筆記:行 | 原句摘錄 | 現況 | 形狀 |
|---|---|---|---|
| Systems/宣稱驗證器:41 | RULE: CI claims 工作因 F7 超過 60 秒而紅…[confirmed:2026-09-24] | 上限 09-25 已改 120 秒(test_f7_end_to_end.py:97);欄位齊全、半年內確認,依 CLAUDE.md 有挑戰程式的效力 | R1 |
| Systems/一鍵展示:41 | RULE 模型入口另帶「三個模型變數」[confirmed:2026-09-26] | MODEL_VARIABLES 四個(LOGIN_TOKEN_ENV);確認戳在改動之後 | S3(效力放大) |
| Issues/Phase9代使用者裁定待覆核:23–25、73 | F7 效能設計「選項 2、不實作、設計沒實作」 | 使用者改選做法 1、已實作(468bf65),上限又放寬到 120 秒 | D1 |
| Issues/F7端到端在CI上偶爾超過60秒:13、33、62 | 摘要「選做法 2…60 秒照舊,狀態維持 open」 | 狀態 resolved,正文寫改裁與 120 秒 | D1 |
| Systems/任務流程領域模型:89 | 現行決策規則拿到的證據只有三種,新種類不會改變規則路徑 | rule_round 把四種收據傳給 policy;綁的測試只跑評估路徑 | S1+M2 |
| Systems/任務流程領域模型:60 | 執行行程那一側的操作狀態…尚未定義 | domain/attempt.py 有狀態機,同篇 54 行寫了 | S1/H1 |
| Systems/分析行程流程與檢查點:321、325 | 開了 AI 時…、runner 開 AI 時經 held_rule、兩種模式都查 | --ai-judge、held_rule 已撤,runner 只有一種模式;這節沒有撤除橫幅 | H1 |
| Systems/一鍵展示:101 | 48 個節點…68 條分支 | 46 個節點、67 條邊(測試斷言 46/67);同篇 275 行寫了 68→67 | H1/S3 |
| Systems/靜態檢查閘:38 | CI 沒有跑 lumos…圖譜一致性只在本機推送前檢查 | ci.yml 有 drift 工作;同篇 30 行寫了 | H1/S1 |
| Systems/有界標籤的指標:41 | 模型與 Jev 那一項標不適用 | ops/metrics.py 從花費帳算出數字;同篇 25、42 行已改 | H1 |
| Systems/評估與Jev決策點:81 | 現行規則在暫停格誤提案 39/60… | governance/eval/phase10-worth-adoption.md 已重產,結果翻過來 | A1 |
| Systems/提案收件口:68 | 沒有收件表清理與保留期限 | RETENTION 2 小時、_purge_finished_tasks;同篇 27 行寫了 | H1 |
| Systems/提案收件口:36 | 三種權限類原因合併成 not_permitted | _PERMISSION_BLOCKS 四個(AGGREGATE_LIMIT_REACHED);同篇 88 行寫了 | H1/S3 |

### 中誤導

| 筆記:行 | 原句摘錄 | 現況 | 形狀 |
|---|---|---|---|
| Systems/一鍵展示:176–244 | 七段橫幅區 19 個 [test:] 指向已刪測試,夾雜仍存在的測試 | | H3/T1 |
| Systems/一鍵展示:212 | 第四個模型變數只寫在撤除橫幅底下 | 正式 RULE 行反而寫三個 | H3 |
| Systems/一鍵展示:59、55(摘要) | 模型那段等 11B 增量 2 補驗;停止時限兩倍逾時加一秒 | 正文 108–109、226 已改 | H2 |
| Systems/一鍵展示:72(摘要) | 顯示收據要等模型說明接上才做 | 說明已接、收據沒做;核可表單已不帶說明 | P1 |
| Systems/一鍵展示:256 | AI_NODES 只剩模型候選與模型說明 | 只有 a_narrate;同篇 267 行已改 | H1 |
| Systems/展示頁面:92–93 | [test:test_each_ai_round_shows…] | 已刪;同篇 104 行已註明 | H2/T1 |
| Systems/展示頁面:64 | REVISIT 專案若加了瀏覽器測試工具…(日期式) | Playwright 09-25 已加;1440 寬檢查沒做 | P2 |
| Systems/展示頁面:143、134 | F5 走「不需 AI 說明,送出」那條邊;AI 寬度給模型候選 | 那條邊與候選節點都已撤;同篇 150 行已改 | H1 |
| Systems/README流程動圖產生器:38、36 | 測試斷言「收件收下後」 | 現在是「建議被收下後」;同篇 49 行已記 | H1 |
| Systems/README流程動圖產生器:17 | TEST: pytest -q tests/tools tests/test_static_checks.py | 沒含唯一守衛 tests/test_readme_flow_diagram.py | G1 |
| Systems/規則模式探索評估:151、147、152 | v1 雜湊「全文見 EXPECTED_VERSION_SHA256」 | 常數已是 v3 的值 | A2 |
| Systems/模型用戶端:217 | [test:test_the_runner_refuses_a_model_timeout_that_outlives_the_lease] | b2fc512 刪;舊名清單漏列 | T1 |
| Systems/評估與Jev決策點:118 | [test:test_one_exact_ratio_function_feeds_the_answer_key] | 從沒存在過(實際 …feeds_receipts_and_the_answer_key) | T1(胎生) |
| Systems/可觀測查詢:31–32 | 現在只有人工與測試會呼叫;範圍上限留給增量 2 | ops/metrics.py、demo/observe.py 都呼叫;Phase 9 done 沒定 | S1+P2 |
| Systems/模型用戶端:272 | 模型閘道的呼叫者只剩說明、評估、假說 | GATE_USERS = narrate、ai_judge、rule_mining_model;假說不經閘道 | H1/S1 |
| Systems/模型用戶端:164–166 | 還沒做:錄製前實測與寫啟用紀錄 | 有 09-25 即時錄製入庫,推論已做 | W1 |
| Systems/模型用戶端:65、86 | 整個 rtb 唯一啟動子行程的模組 | 另有 PROJECT_STARTERS;同篇 171 行交代 | H1 |
| Systems/模型用戶端:153 | CAPPED_CALLERS(評估候選、即時實測) | 多了 RULE_MINING;同篇 279 行寫了 | H1/S3 |
| Systems/Mock-DSP:76 | 仍沒有測試守護:每個請求結束關閉連線 | test_shared_base.py:51 有;同篇 87 行寫了 | H1 |
| Systems/宣稱驗證器:63 | 每份清單 20 到 33 支正式碼 | 現在 29–49 支 | S3 |
| Systems/評估與Jev決策點:73;服務水準與燒損告警:30;寫入能力憑證:38;執行迴圈:57;任務流程領域模型:54/56 | 「本篇管 N 支檔」 | 與 about_code 不符 | S3 |
| Systems/執行迴圈:144、63 | 執行前檢查三條硬規則;列舉漏政策版本 | precheck 四條(Phase 8 加政策版本) | S3/E1 |
| Systems/執行迴圈:30 | RULE 沒進展就停機 [retire:改用佇列與多工作者(Phase 4)時重審] | Phase 4 done,行為已改成丟 LeaseLost,RULE 沒重審 | R1 |
| Systems/外部寫入嘗試紀錄:27 | 長期計數與告警等 Phase 9 [retire:Phase 9 接上正式指標時] | version_conflict_rate 已有 | R1 |
| Systems/提案收件口:71 | 租戶相符檢查還沒做 | capability_signer 的 campaign_not_allowed 已查 | S1 |
| Systems/提案收件口:72 | 三個狀態的對應要在增量 3 定義 | Phase 2 done,處置欄 5 個成員 | W1 |
| Systems/確定性指標計算:43–44 | 清單只在這個目錄的 ruff.toml;REVISIT 新增第二個受限層時 | 已有 9 份 ruff.toml,已選複製 | S1+P2 |
| Systems/分析行程流程與檢查點:382;任務流程領域模型:96 | 要協調者重錄,錄好前是紅的 | 626cccb 已重錄,對應 Issue resolved | W1 |
| Systems/分析行程流程與檢查點:266、281 | 撤除註記「(那支測試…刪)」 | 實際刪了 4 支、2 支 | H3 |
| Systems/分析行程流程與檢查點:50 | 停止時限兩倍逾時加一秒 | max(STOP, 2t+1, rule_stop_grace_seconds),預設 50 秒 | S3 |
| Systems/分析行程流程與檢查點:106 | 重新規劃的迴圈沒有次數上限 | MAX_GENERATION=3、MAX_CHANGE_RESTARTS=2;「重新規劃」已改指開接續任務 | S1+M1 |
| Systems/分析行程流程與檢查點:6 | responsibility「示範用的最小決策規則」 | policy.py 自稱正式決策規則(九條) | F1 |
| Projects/RTB_Phase12…:337、Issues/Phase12…:39、Issues/Phase11後…:38 | 每次展示 1 美元、每月 20 美元上限照 11B | CAPPED_CALLERS 不含說明與假說,Phase 13 裁定拿掉上限 | D1 |
| Issues/確認頁顯示AI說明時的收據還沒做;Projects/RTB_Phase12…:338–341 | 收據待裁定;緩解靠顯示收據 | 核可表單一律不帶說明,觸發情境永遠不會出現 | P1 |
| Issues/執行端寫入前再確認沒記讀到的平台版本:14、20 | 等「AI 參與決策」改到執行端時一起做 | AI 決策已撤,條件永遠不成立;問題本身還在 | P1 |
| Projects/RTB_Phase13…:808、811、812 | 重錄 F5、開 AI 情境時限 | F5 雙胞胎與開 AI 情境已撤 | P1 |
| Projects/RTB_Phase12…:57–58、344 | 分析端沒有正式啟動程式、沒有展示驅動、入口九支;錄製進真帳 | 都已改變;段落沒標快照 | U1+S1 |
| Verification/Phase5驗收紀錄:37、5;Phase4驗收紀錄:40、5 | 分析行程還沒有啟動程式 | runner.py 有傳操作查詢 | S1 |
| Verification/Phase14增量3驗證紀錄:18 | 狀態 pending,錄好之前照設計是紅的 | 狀態欄 pass、已重錄 | H1 |
| Verification/Phase13增量2驗收紀錄 | revalidate_when 第一次用真 claude 跑開 AI 的分析端;仍 pass | --ai-judge 已撤,被驗收的調查迴圈已不在 | P1+V3 |
| Verification/Phase13增量1/3/4、Phase12增量2 | revalidate_when 第一次錄批次時 / Phase 13 接 AI 時 | 事件都已發生 | V1 |
| Verification 約 14 篇(Phase1–8、11B-1、12-1、12-2、13-1/3/4) | revalidate_when 綁的事件已發生 | 是否在別處重跑過要人判 | V1 |
| Verification/Phase11B增量1:19;Phase12增量1:19 | 整份完成時改寫/併入 | 一直沒做 | V2 |

### 低誤導(摘要)

- H1/S3:Systems/調查實演:24(一支測試→兩支);展示頁面:134;README:36;提案收件口:91(兩個索引→三個);稽核表只增不改守衛:29(只有嘗試紀錄→也有 dsp_calls)、:30(七張→九張);共用行程基礎:67;外部寫入嘗試紀錄:45;分析行程:103(三個介面→六個)、:62(兩個端點→六個);任務流程領域模型:56;確定性指標計算:36;模型用戶端:56、67;追蹤檢視:43(結束代碼多了 9);一鍵展示:104、201。
- E1:寫入能力憑證:65(load_tenants 的長駐呼叫端);Mock-DSP:73(兩種動作,有爭議)。
- M1:執行迴圈:138、提案收件口:62(「事件表」在有了生命週期事件表後變成歧義);分析行程:318(code_rule 同名換義)。
- M3:死信重放指令:14、寫入能力憑證:17、提案收件口:16(「啟動器用正式入口同一支 parser 核對」只對 DSP 與執行迴圈成立)。
- P2:Systems/一鍵展示:63;展示頁面:112、114;確定性指標計算:47;執行迴圈:175;Issues/Phase9代使用者裁定待覆核:50;Projects/RTB_Phase9…:506;Phase6…:431;Phase2…:123–124、190。
- R1:分析行程:70、任務流程領域模型:33(推定)。
- U1:Projects/Phase5:34–35、Phase6:155/371/374、Phase7:44、Phase8:41/46、Phase11:35–36、Phase15 現況段。
- T1:Systems 合計 54 個懸空 [test:](分析行程 24–27、一鍵展示 19、展示頁面 4、外部寫入嘗試紀錄 2、模型用戶端 1、評估與Jev決策點 1);Verification/Phase10驗收紀錄:24。其中至少 3 個從沒存在過(胎生):test_a_negative_unresolved_count_is_treated_as_a_corrupted_table、test_restart_recovery_requires_the_held_keys、test_one_exact_ratio_function_feeds_the_answer_key。Mock-DSP:63 的 `getMetrics` 也是胎生(只有 get_metrics)。
- F1:updated 戳記落後約 8 篇(靜態檢查閘、宣稱驗證器、服務水準、追蹤檢視、可觀測查詢、提案收件口、寫入能力憑證、共用行程基礎);responsibility 落後:提案收件口、寫入能力憑證;Systems status: doing 但計劃已 done:調查實演、規則模式探索評估、規則模式探索模型入口。
- C1:src/rtb/executor/observability.py:11–12 模組說明「目前只有人工與測試會呼叫」;src/rtb/executor/inbox_store.py:77「其餘三種是確認」(實際 5 個處置);測試名 test_client_headers_are_exactly_idempotency_key_and_capability(實際 3 個成員);src/rtb/analyzer/flow.py 檔頭「三個介面」(實際 6 個)。
- 其他:MOC/index 漏列 12 篇(doctor S6 只算其中幾篇)。

## 稽核員判定排除的(校準用)

- 標了「已隨 b2fc512 刪除」的測試名、撤除橫幅下明說撤除的段落、帶日期的施工紀錄:都算歷史,不列。
- 常數與上限大多吻合(60 秒租約、5000 列、2 小時、15 分鐘、24 小時、最多 3 代、MAX_NESTING 64 等)。
- 四篇還開著的 Issue 問題都還在(SIGTERM、錄製檔深巢狀、說明提示寫給核可者、執行端沒記讀到的版本)。
- 拿不準沒列:一鍵展示 97 行「F7 約 9 秒」對 251 行「16 秒」(要實跑);state.py 是不是原樣複本(程式答不了)。
