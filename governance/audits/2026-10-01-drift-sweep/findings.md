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

## 第二輪:全讀(2026-10-01)

第一輪沒逐句讀的部分在這一輪補齊:Projects 18 篇正文全讀(14 個唯讀稽核員分工,Phase 13 計劃分前後兩半)、Verification 32 篇全讀、MOC。到這一輪為止,全圖譜 94 篇每一行都讀過。量測點 df6ff87。下面依筆記分組,每行格式:行號 + 原句要點 + 現況 — 形狀 + 誤導。「已列」= 第一輪清單已有同一句或同一事實。

### Phase9 計劃(全讀 546 行;67 個 [test:] 全在;無配方)
- 240 [S630] 合約句仍寫「模型與 Jev 標不適用」;metrics.py:810-840 改成從花費帳算/無樣本;同篇 186 後註已改 — H2 高
- 215 標籤表「模型與 Jev 無標籤」;metrics.py:168 四種標籤 — H1 中
- 188 允許標籤清單少三個模型標籤 — E1 中
- 185 分析沒呼叫模型照實標不適用 — H1 低
- 448、482 [S675] 七張稽核表;實際九張 — S3 中
- 452 只登記嘗試紀錄四欄;實際還有 dsp_calls 內容雜湊 — H1/S3
- 426、470、506 DSP 沒接時鐘、做不到;787a35e 起有 store_clock — S1(+P1) 中
- 139 [S622] 60 秒上限;實際 120 — S3 低(已列同類)
- 415、471 分析端沒可注入時鐘;runner 有、instrumented/narrate 仍讀系統時間 — S1 部分(已列)
- 424 標頭封閉列舉兩成員;實際三 — H1/S3 低
- 451 DSP 補兩欄;實際三 — S3 低
- lands_in 漏 Systems/追蹤檢視、522 行指錯家 — F1(胎生)低
- 新形狀:無;建議 F1 分「胎生」子類(lands_in 對〈落點〉段連結)
### Phase13 計劃 1–500(全讀;無 [test:]、無配方)
- 221–222 調查原始資料表不進任何決策;task_store/rule_round 規則輪定案讀它 — S1+M1 高
- 357 建提案只傳三種證據;[S1107] 改寫後帶四種收據 — D1 高
- 372、346 決策函式只拿三種證據 — S1+M2 中高(同句型已列於任務流程領域模型:89)
- 445 守衛只在 --ai-judge 套用、--timeout-seconds 5 合法;實跑 C 步 60.0 秒 → 現在會被拒 — S1+H3 高
- 400–402 --ai-judge 開關現在式 — H2 中
- 393/396/398 閘道清單含 runner;GATE_USERS=narrate、ai_judge、rule_mining_model — H2+E1 中
- 424–454 續租、收據容器、停止旗標、登入預檢都已撤;433 橫幅只蓋 RenewalSkipped — H3 中
- 416–418 EXAM_HOLD 現在式 — H3 中
- 348–349 MAPPED_ENUMS 十九個;實際 17 — S3+H3 中
- 345 runner 接 CallTerminated — H3
- 408–415 commit_step 兩個可選參數;investigation 已拿掉 — H3
- src/rtb/domain/evidence.py:31–33 程式註解「現行規則只拿三種」 — C1 中
- 478 評估照正式路徑;正式路徑已不存在 — M1 低
- 55、61 裁定 5 七情境 AI 參與;被裁定 8 翻案沒標作廢 — D1 低
- 236、449 細節實作時補/實作時定名;已做 — W1 低
- 395 model* 模組列舉漏三支 — E1 低
- updated 09-27 落後 — F1 低
- 新形狀候選:N1「照名字修補,同機制其他敘述沒蓋到」(H3 子型;偵測:新加橫幅時拿撤除提交刪掉的符號清單比兄弟段落);N2「計劃寫作當下的『現在』」(done 計劃裡無日期的「現在/現行/目前」+反引號識別碼)
### Phase14 計劃(全讀 413 行;[S1401]–[S1429] 綁定都在;無配方)
- 38 code_rule 只看 1 小時曝光點擊(現在式、無快照橫幅);已改呼叫九條 — U1 中
- 387 300 秒待實測;F7 已實跑 16.1 秒 — P2 中
- 116 run_case 用 _verdict / rule_verdict;增量 4 已改,_verdict 不存在 — H1 中
- 9、10 摘要「AI 提案經四查詢九條否決」「AI 複查只定案一次」;已撤,4919ebb 只補了同行 --hold-submit — H2(N1 補註半套)中
- 397、398 待審問題已處置/已測 — W1 低
- 100、102 AI 步寬限、AI 與規則一致 — H1 低
- 15 DEP 重查指令只查已刪符號 — 新 Q1 低
- 254 AI_NODES 保留 a_candidate、十八個;實際只 a_narrate、17 — S3 低
- 310–319〈會卡住這個設計的既有程式〉現在式無橫幅 — U1 低
- 171、206 未解決/待錄製(測試已不存在、已入庫)— H1 低
- 新形狀:Q1「重查指令只剩查已刪符號」(實跑 DEP 的 rg,命中全是否定斷言或說明字串就標);N1「補註只補半套」(加撤除標記時,同篇同機制關鍵詞的未標行列候選)

### Phase2/Phase7/F7效能/README流程動圖 計劃(全讀;[test:] 全在;無配方)
- README:19 WHY「產出物工具不寫自動測試」;da9a5d9 已加 tests/test_readme_flow_diagram.py — D1+S1 高
- README:14、17 借 flow.py 節點、narrate 灰階規劃中;產生器自訂節點、narrate 已上 — 新 B1 中
- README:21 驗證入口漏 test_readme_flow_diagram.py — G1 中(同句另一處)
- Phase7:245 [S213]、218 對外呼叫只有三種;EXPECTED_ENDPOINTS 6 種;測試註解也寫三種;Verification/Phase7驗收紀錄:25 同 — S3+C1 中高
- F7:174 [S686]、137、61、22、73「S340 60 秒照舊」;實際 120 — S3/D1 中
- F7:86「重試成效還沒實測」;驗收紀錄已 30 次 0 紅 — H1 中
- F7:22、42、25 要不要做由使用者裁、RETIRE-IF 已觸發;改裁橫幅只蓋第一段 — H1+D1 低中
- Phase7:255 拒收計數併入 Phase 9;Phase 9 沒做 — P2 中
- Phase2:189 重新規劃沒上限 — S1+M1 中(已列計劃版)
- Phase2:123–124、190 REVISIT 收件表保留 — P2(已列)
- Phase2:280 REVISIT 2026-10-05「若增量 2 尚未開始」— P1 低中(10-05 會唸無意義的回頭條件)
- Phase2:126 增量 3 定義對應 — W1(已列計劃版)
- Phase2:85 事件表上限 1000;實際每種代碼 200 — S3 低
- Phase2:209 ClientHeader 只有冪等鍵;多了 CAPABILITY — E1/S3 低
- Phase2:218–219 instrumented 兩個包裝;已改 — H2 低
- Phase2:217/223–224/264、Phase7:46/163/303「示範決策規則」— H2/M1 低
- Phase2:63 trace 只到 HANDED_OFF;ops/trace.py 已組完整時間線 — S1 低
- Phase2:210 DSP_TIMEOUT_SECONDS 等常數不存在 — S3 低
- Phase7:307 兩條線合併會衝突;已合併 — P2/W1 低
- Phase7:41–48 現況段沒標快照 — U1(已列)
- README:19 SVG 目視留到 Phase 12;事件已發生 — P2 低
- Phase2/Phase7/F7 updated 落後 — F1 低
- 新形狀:B1「後續改版繞過計劃」(計劃 updated 早於 lands_in/plan_refs 指到的 Verification 最新日期或管轄檔最後提交日)
### Phase11B 計劃(全讀 394 行;45 個 [test:] 全在;無配方)
- 37、116、128 每次展示 1 美元/每月 20 美元上限;CAPPED_CALLERS 不含說明與假說(Phase 13 裁定 13)— D1+H2 高
- 341、337、334 金流後果已由上限管住 — D1 中
- 354 OAuth 令牌被白名單擋、不支援;modelclaude.py:624 已帶 — H1 中高
- 106 帳檔路徑用 HOME 算;account_home 不看 HOME — H1 中
- 41 說明給人工核可的人看;核可表單說明欄固定空(協調者交辦,無使用者新裁定)— 新 D2 中;186、192 收據 — P1
- 70 會呼叫模型的入口只有三支;另有實測、規則探勘、調查評估 — S1 中
- 138 只放行模型用戶端(subprocess);實際放行 modelclaude + 啟動器、驅動 — S1/E1 中
- 139 只有說明命令列准匯入模型用戶端;已改經閘道三支 — H2 中
- 73–75、80「四支新檔」modelclient 包辦;已拆五支 — 新 L1 中
- 350 續寫看不看得出來沒實測;352 已實測 — H1 低中
- 351 REVISIT 12-31 觸發分支永遠走不到 — W1/P1 低中
- 88、147 白名單五個鍵;可到六 — H2 低
- 196、357、status done 接入點 3 實測結論;從沒錄過、報告寫沒量 — 新 N1(done 計劃未交付項沒去處)低中
- 196 三列「尚未實作」;Phase 0 已改 Code — U1 低
- 83 後端兩個實作;只有一個 — M1 低
- lands_in 只列模型用戶端 — F1 低
- 新形狀:D2「裁定被實作悄悄繞開」(無翻案紀錄);L1「職責住址漂移」(拆檔後功能搬家,路徑還在);N1「done 計劃未交付項沒去處」
### Phase6 計劃(全讀 489 行;56 個 [test:] 全在;無配方)
- 96–97、310–312 F7 預告行目前在收件口、等增量 3 再轉正;早已轉正搬執行迴圈 — W1(轉正後計劃沒回頭)高
- 392、387、424 aggregate_used 清單加總、共用 _counted;F7 效能改 SQL SUM,同裁定 :80 已標取代 — H2(D1 底)中
- 399、373 不另寫唯讀交易;Phase 9 已有 read_transaction — S1 中
- 84 分析行程沒有啟動程式 — S1 中
- lands_in、435 查詢寫進收件口/嘗試紀錄;實際全在 observability.py(家:可觀測查詢)— F1、H1 中
- 173–182 規則表比例上限在執行前檢查;已搬 guardrails 簽發後 — H1 中
- 138、165 比例常數在執行迴圈模組;在 guardrails.py — H1 中低
- 247–252 增量 3 現況段四種處置、一把金鑰 — U1 中低
- 147–152、371–374、431 — 已列
- 257–258、184 硬規則/非單筆代碼列舉漏 Phase 8 兩種 — E1/S3 低
- 65、101 比例在執行前檢查 — H1 低
- 468 增量順序警告前提消失 — P1 低
- 104、38、448、406 待辦已做沒回頭結案 — W1 低
- 規律:「轉正後計劃沒回頭」(W1 子型:Verification pass/已轉正 → 掃 plan_refs 計劃的轉正節);「取代標記只蓋一處」(H2)

### Phase13 計劃 501–992(全讀;61 個 [test:] 名稱都在;無配方)
- 515–598 錄製批次、展示接 AI、F5 雙胞胎、--hold-submit 整片無橫幅;摘要第 8 行「沒標歷史的是現況」放大誤導 — H3 高
- 685–764 共 12 條撤除條款仍掛活 [test:](3 條綁的是斷言反面的測試:S1121、S1122、S1157;S1116、S1137 子項寫「此綁定撤除」綁定還在)— 新「撤除條款仍掛活綁定」中高
- 541、603、950 F7 永遠錄製;NEVER_LIVE 已撤 — D1 中
- 539、653、945 三個模型變數、--ai-judge — H2+S3 中
- 520、528、774 展示批次 phase13-demo;實際 phase14-demo,BATCH_PATTERN 拒收 — S3 中
- 535 改種子要重錄 F1;S1158 已撤 — P1 中
- 802 守衛面 runner 拿模型變數、續租 — D1+H1 中
- 713 逐輪卡/考題/只有合成資料測試;已撤、CI 跑 Playwright — S1+P1 中
- 786–794 要改寫的既有合約清單只註一項 — H3 中
- 805、804、809、810、806、603、605 展示 AI 相關風險前提消失 — P1(+P2) 中低~低
- 632–663 前掃段現在式無快照 — U1 低中
- 924–938 重錄指令 mv 到已存在目錄會變子目錄、CI 會紅 — 新「重跑配方前提過期」低中
- 503、507 CAPPED_CALLERS 兩個、不分呼叫者 — E1+H2/U1 低中
- 628 README 要畫 AI 決策點;README 已改九條版 — D1 低
- 578、799 誰決定標示、AI 最大影響 — H1 低
- 882 prompt-injection 宣稱範圍 — D1 低
- 新形狀:「撤除條款仍掛活綁定」(子項含撤除、條款仍有 [test:] 無 [manual:] 就報;子項寫「此綁定測試」而仍列 [test:] 報高);「重跑配方前提過期」(程式碼區塊 mv/cp -r 的目標已是目錄就報)

### Phase4 計劃(全讀 566 行;[test:] 全在;無配方)
- 142 [S103] 處置恰好四種;實際五種;綁定測試用 set(Disposition) 自我參照所以照綠 — S3+新 T2 高
- 139 [S100]、54、99 死信後永不再取件;Phase 8 重放放回;測試沒測死信 — 新 X1 中高
- 80「上限 5 就是最多交出去 5 次」;核可放回/重放歸零 — X1 中
- 504、130 條件式 REVISIT 寫下時條件已成立(c5b24cb 本輪改寫);504 的 runner.py 不代表「有訊息入口」— 新 P3(胎生即成立)中
- 436、455 每步最多兩次讀 DSP;規則輪 C 一步 5 次 — S3+S1 中
- 497、484 沒有啟動程式 — S1(已列同句型)
- 457–458 兩條回頭註判決相反(458 寫的 Phase 13 AI 守衛本計劃沒有)— M3 低中(本輪 c5b24cb 寫的)
- 90 失敗成員含「同廣告被鎖」;實際 3 個 — H1 低中
- 95–99 確認列舉漏待核可 — E1 低
- lands_in 漏外部寫入嘗試紀錄、updated 落後 — F1 低
- 545、36、122、233 同篇前後矛盾 — H1 低
- 489、251 F1/F2 預告行掛在收件口;已轉正到執行迴圈 — W1 低
- 134 [S95] 四種處置、測試名 migrates_to_four_dispositions — S3+C1 中
- 新形狀:X1「後期功能開例外口」(條款點名的成員綁定測試沒用到;新增放回/歸零寫入反查「永不/最多」條款);P3「胎生即成立的條件式回頭條件」(blame 寫入提交 vs 目標檔建檔日);T2「列舉自我參照綁定」(條款寫死 N、測試用 set(Enum))
### Phase3 計劃(全讀 559 行;92 個 [test:] 全在;無配方)
- 517 REVISIT when-file approve.py;approve.py 管待核可提案不是轉人工嘗試,resolve 零呼叫端;寫下時已成立(本輪 c5b24cb)— 新「觸發代理錯位」+P3 高
- 138 人工處置指令列工具「Phase 6 一起做」;沒做 — P1 中
- 124 [S13] 被 Phase 4 [S127] 取代沒回標(S55、S68 有標);測試只跑 held=() — 新「取代回標漏條」+M2 高
- 107 REVISIT 寫下時已成立;嘗試紀錄事實上已定為不清 — P3+P2 中
- 366 [S63] 標頭恰好兩個;實際三 — S3 中
- 262、266 單一執行者下走不到;3a 後會走到 — H1 中
- 535 執行行程是唯一有寫入能力的行程;展示伺服器也有金鑰 — S1 中低
- 391、239 RETIRE-IF 綁錯事件永不依原意成立 — P1 低中
- 310–317 單一執行者鎖整段現在式無橫幅 — H1 低中
- updated、lands_in 漏共用行程基礎 — F1+H2 低
- 194 分析啟動程式必須清金鑰;runner.py 不清、由啟動器白名單守 — S1+M2 低
- 230 指向已被換掉的 REVISIT — H1 懸空指涉 低
- 528 轉正落在收件口;已在執行迴圈 — H1 低
- 337 處置四個;實際五 — S3 低
- 新形狀:「觸發代理錯位」(when-file 檔存在但代表的事沒發生;建議 [expect:符號]);「生而已成立」(=P3;寫入時當場求值就擋);「取代回標漏條」(解析「Phase X 的 S## 由…取代」確認原條款有回標)

### Phase10/11/5/8 計劃(全讀;[test:] 全在;無配方)
- Phase11:97、27 F7 60 秒紅了就重跑、算進回頭條件次數(在〈使用者裁定〉節);已改 120、結案 — D1+P1 高
- Phase5:158 [S300]、194 兩種權限類合併;實際四種 — S3/E1 中
- Phase10:73 候選必須經共用 HTTP 用戶端;policy.py 說明已改 — H1(D1) 中
- Phase10:116 [S705] 豁免含考題結束;exam_hold 已撤、豁免清單空 — 新 E2(列舉含已刪成員)中
- Phase5:89–99 處置表缺 Phase 8 的等待與重新規劃列 — H1+E1 中
- Phase10:59 Jev 三列都還沒實作;Phase 0 已改 Code — S1 中低
- Phase10:83、84 決定紀錄數字;報告 09-27 重產 — A1/H1 中低
- Phase11:95、144 F7 60 秒、CI 抖動 — S3/P1 低
- Phase10:77 決策函式經路由;沒有候選時直接九條 — H1 低
- Phase10:105 LLM 沒量原因未導入 — S1 低
- Phase11:143 五個受保護層禁 importlib;實際八份 — S3 低
- Phase10:147 回退拿掉五個目錄禁令;漏三份 — E1 低
- Phase10:132 [S700] 決策結果一字不變;已改寫 — H1 低
- Phase5:72、76、62;Phase8:79、80 設計段「現在/目前」描述施工前狀態 — 新 U2 低
- Phase5:33–35、Phase8:41–46、Phase11:31–36 現況段無快照 — U1(部分已列)
- 新形狀:E2「列舉含已刪成員」(合約列舉成員對 StrEnum/常數比對);U2「設計段的施工前現在式」(=Phase13 的 N2「計劃寫作當下的現在」)
### Phase12 計劃(全讀 500 行;68 個 [test:] 67 在,[S1034] 自註不存在;無配方)
- 153、98 頂端花費按展示編號加總、上一次全跑花費存伺服器;present.py:337 寫死 None、used_by_demo 沒人呼叫 → 頁面「本次 AI 費用」永遠「—」(程式本身的缺口);Systems/模型用戶端:155 雙生句 — 新 N1「設計句沒落地」中高
- 149、264 沒開即時帶 --ledger、改走錄製記真帳;driver 一律 --recorded-ledger — H1 中(部分已列)
- 268 [S1000]、297 [S1029]、298 [S1030] 條款旁括號註記描述綁定測試內容,測試已改 — 新 B1「綁定註記過期」中
- 128 分析端節點含由誰判斷、說明旁支在送件前;flow.py 已改 — D1 中
- 146 模型入口三個變數 — H1/S3(已列雙生)
- 136–139 對應清單 18 個;實際 17 — H1 低中
- 263 花費帳只能按時間取;已有 used_by_demo — S1 低
- 75、76 驅動跑法、決策函式丟原因;已改 — H1/S1 低
- 362 停止時限兩倍逾時加一 — S3(已列)
- 41、96、160、337 1 美元/20 美元上限 — D1(已列 337)
- 41/46/97/195/198/286/302/341 確認頁說明與收據 — P1/W1(已列)
- 343 展示時間實作後記進驗收紀錄;沒記 — P2 低
- 162–163 頁面分區前後比較、已知限制只在報告 — H1 低
- updated 落後 — F1 低
- 57–67 現況段 — U1(已列)
- 新形狀:N1「設計句沒落地」(done 計劃設計段點到的欄位/函式在 src 沒有生產者;或要求設計小節對到 [S] 或明寫不做);B1「綁定註記過期」(測試函式最後修改日晚於條款註記日期)
### Verification 後 16 篇 + MOC(全讀)
- Phase13增量4:3、18 驗收「展示讓 AI 參與決定」仍 pass;已撤 — V3+M2 高
- Phase13增量4:29、64 兩支 CI 守衛都在跑;一支已刪 — T1 中高
- Phase13增量4:65、9、58 回頭條件、revalidate_when、已知限制過期 — P1/V1/H1 中
- Phase9:74 F7 使用者選擇不實作;已實作 — D1 中;Phase9:6 revalidate 已觸發 — V1 中
- Phase3:46、43 單一執行者、沒有回饋迴圈 — S1 中
- Phase7:5、45 示範規則、沒比例上限 — S1/M1 中
- README流程動圖驗證:17、21「現行圖」雜湊已變 — H1+A1 中;:6 valid_under — F1 低
- Phase14增量3:7–8、89、47 revalidate 已發生、懸空已補、改綁測試改名 — V1/W1/T1 低
- Phase14增量2b:18 正文 pending、開頭 superseded — H1 低(本輪 drift fix c3 改了開頭沒改正文)
- Phase14增量2a:22、32 待驗已做 — W1/H1 低
- Phase9:98、88;Phase5:44、Phase6:48、Phase8:42 等 Phase 9 — P2 低
- Phase6:45、Phase7:48 — W1/P1 低
- MOC:93、94 兩篇 Issue 標 (open) 實際 resolved — 新 X1「索引狀態副本落後」中;MOC 漏列 12 篇(已列)
- 新形狀:X1「索引狀態副本落後」(MOC 裡 [[X]](status) 對 X 的 status);X2「正文宣告蓋過開頭欄位」(grep「以這一節為準」;反向:開頭改了正文沒改)

### Verification 前 16 篇(全讀;F1–F7 合約與綁定測試都還在)
- Phase10:56–66、19「現行規則」錯在哪;已換九條、報告重產 — A1+M1 高
- Phase10:70、5 成本延遲門檻還沒裁定;09-24 已裁定 — S1 中;Phase10:6 revalidate 已觸發 — V1 中
- Phase11B增量1:45 AI 真的影響提不提案;已撤 — D1 中;:44 還沒做即時實測(一半錯)— S1 低中;:23 五支模組實際 8 — S3 低
- Phase12增量1:57、56 分析端沒有模型入口、畫面還沒有 — S1/H2 中;:44、46 偶發紅、REVISIT 時序修正 — W1 低中
- Phase13增量1:33 還沒有真錄製 — S1 中
- F7效能:29 還沒處理、會偶發紅;同篇 31–35 已做 — H1 低中
- Phase11:6 revalidate 改 policy 要另派審 — V1 低中
- Phase1:54 getMetrics 胎生名 — T1 低;Phase1:51、6 已補驗沒收尾 — V1
- Phase12增量1:41、Phase11:57 F7 紅燈重跑 — D1/P1 低
- Phase12增量1:25 parser 理由 — M3 低
- 事故F4–F7 摘要「N 條配方全紅」;配方已失配 — K1 連帶
- 新形狀:「預告殼」(verification pass 但只有預告、無 valid_under;7 篇事故驗證);「驗收效期欄空白」(pass 但 valid_under 與 revalidate_when 都空;F7效能驗收紀錄)
### Phase15 + Phase0 計劃(全讀;Phase15 20 個 [test:] 全在;無配方)
- Phase0:146–147、149、167、180、187 分析與執行各持有 DSP 讀取令牌;DSP 讀取端點不驗令牌,Phase 3 明寫不做;Phase11B [S905] 照抄 — 新 D1b(下游砍範圍上游沒回指)中高
- Phase0:28–33 d4 valid、185、186、156–157 畫面只讀不能下指令;展示伺服器有 POST /run、/approve 並在行程內簽核可 — D1b 中
- Phase0:166、192 每段佇列各有 DLQ;分析側不做死信(使用者裁定)— D1b 中
- Phase15:178 重跑 [S1501]–[S1518];實際到 [S1520],漏的正是最該重跑的兩條 — E1 中低
- Phase15:5 updated 落後 — F1 低;:9 VERIFY 沒列本案交付的檔 — G1 低
- Phase15:26、190 Phase 14 仍留 --ai-judge、尚在另一分支 — H1 低
- Phase15:90 CAPPED_CALLERS 兩個 — U1/H1(已列)
- Phase15:132 生成器沒有數週歷史;rule_mining_history.py 已有 — U1/S1 低
- Phase15:147–148、188 合約候選待實作時決定、待審問題 — W1 低
- Phase0:3、8、107 status doing、只管 Phase 0;實際當活的決策總表 — F1/M1 低
- Phase0:341 lands_in 尚未有程式;開頭欄位已列 — H2 低
- Phase0:120–123 待決三項時點已過 — W1 低
- Phase0:99–101、234 RETIRE-IF 從沒判過 — P2 低
- Phase0:327 分析端引入 LLM 要重開設計審;已發生 — P2 低
- 新形狀:D1b「下游砍範圍或偏離,上游沒回指」(下游「不做/先不做/刻意偏離 [[上游]]」→ 上游同名詞現在式句附近沒有回指就報;機制名詞在 src 找不到識別字)
