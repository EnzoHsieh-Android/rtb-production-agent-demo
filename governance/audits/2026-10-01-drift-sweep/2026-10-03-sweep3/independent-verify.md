# 驗證結果:git diff 3f2d82c..HEAD(3e8236a、20527a1)

工作目錄 /Users/enzo/rtb-lumos-update。路徑以 `docs/rtb-production-agent-demo-knowledge/` 為根的寫成 `K/…`。行號是 HEAD 版本的行號。

## 錯誤(6 筆:高 0、中 3、低 3)

- [中] K/Projects/RTB_Phase14正式規則照九條判斷_計劃.md:522 | 新補的「沒有留下 spec-gate 重跑的紀錄」不對 | `docs/.governance-log.jsonl` 有兩筆這份計劃的 spec-gate-run:2026-09-26T23:36:59(提交 0ae6fea,clauses=27 red=0 green=20 regress=62)、2026-09-26T23:58:00(96154dd,clauses=26 green=19 regress=62)。另外 `scripts/lumos:7127-7141`:相依回歸只要有一支紅就先 `return 1`,不會寫這筆紀錄,所以有紀錄就代表 62 支相依回歸全綠。查法:`grep spec-gate docs/.governance-log.jsonl \| grep Phase14` | 改寫成「2026-09-26 23:36 與 23:58 在 0ae6fea、96154dd 上各重跑一次 spec-gate,相依回歸 62 支全綠(治理帳 spec-gate-run),事件入口已滿足」;原本那九支是否在這 62 支裡,還是可以照實寫「沒逐支對照」。

- [中] K/Verification/Phase11B增量1驗收紀錄.md:52 | 這次搬成獨立一行的 `REVISIT:2026-12-31 本機實測若能證明 -p 不會自動續寫,改回照一次請求算`,條件其實已有答案:2026-09-25 協調者實測 claude 2.1.281,證實會自動續寫(num_turns=4),使用者同日裁定允許續寫、預留照 4 次請求算。同一件事在 Phase 11B 計劃裡,這次 diff 已經撤掉 | K/Projects/RTB_Phase11B大模型接入_計劃.md:374(本次撤除註記)、:375(「已確認(2026-09-25…)撞頂後自動續寫」);`src/rtb/modelcore.py:67` `OUTPUT_RECOVERY_ATTEMPTS = 3` | 照計劃那邊的寫法撤掉這行,改成「(2026-10-03 撤除:2026-09-25 實測證實會續寫,使用者裁定照 4 次請求算,見 Phase 11B 計劃)」;要不然 doctor 到期會提醒一件早就裁定的事。

- [中] K/Issues/確認頁顯示AI說明時的收據還沒做.md:13 | 摘要的 `DECISION: 尚未裁定要補做還是撤掉這條`,跟這次新寫的第 23 行「2026-10-03 已裁定撤掉」、第 19 行結案說明、status: resolved 互相矛盾;摘要是最先被推到眼前的那段 | 同檔 :19、:23、:27;K/Projects/RTB_Phase11B大模型接入_計劃.md 的 decisions d1(decided: 2026-10-03);K/Projects/RTB_Phase12一鍵展示與HTML報告_計劃.md:303 [S1034] 已標 superseded | 用 `lumos set` 把 DECISION 改成「2026-10-03 使用者裁定撤掉(AI 說明不進核可表單,Phase 11B 決策 d1),[S1034] 作廢」。

- [低] K/Issues/執行端寫入前再確認沒記讀到的平台版本.md:14(摘要 WHY) | 摘要還寫「等『AI 參與決策』階段改到執行端時一起做」;但這次改的第 22 行寫「2026-10-03 使用者已裁定不做、結案」,檔內也有〈結案(2026-10-03)〉一節。正文的更正有提到 WHY 不再適用,摘要沒有跟著改(這句是本來就在的,不是這次新加的,但跟新句講同一件事) | 同檔 :22–23 | 摘要 WHY 句尾加「(已被取代:2026-10-03 裁定不做、結案)」。

- [低] K/Issues/F7端到端在CI上偶爾超過60秒.md:77 | 「使用者 2026-09-25 選做法 1 並把上限放寬到 120 秒」日期混在一起了:改選做法 1 是 2026-09-24,上限放寬到 120 秒才是 2026-09-25 | 同檔 :18「2026-09-24 使用者本人改裁:改走做法 1」;:13 摘要「同日改裁做法 1,2026-09-25 上限放寬到 120 秒」;:94 | 改成「使用者 2026-09-24 改選做法 1、2026-09-25 把上限放寬到 120 秒」。

- [低] K/Systems/執行迴圈.md:187 | 「協調者 10-03 原本寫成『執行前檢查也會擋』,是錯的」說得太滿:正式路徑的執行前檢查確實會擋舊政策版本的提案;它只是不在這支端到端測試的路徑上。根因寫「兩處守」也漏了這一道 | `src/rtb/executor/execution.py:363-364`(`proposal.policy_version != POLICY_VERSION` → `POLICY_VERSION_CHANGED`);`tests/executor/test_approval.py:445` 只 monkeypatch `approval.POLICY_VERSION`,而且只數 settle 放回幾份,執行前檢查看不到換版 | 改成「對這支測試的解釋是錯的:測試只換核可模組的政策版本常數,執行前檢查看不到換版;正式路徑上執行前檢查照樣會擋」;根因補一句「正式路徑另有執行前檢查,共三處」。

## 檢查過、確認正確的(約 120 句)

依類別抽幾樣(不逐句列):
- 提交與時間:0b2499a 18:43、98f247b 03:22、c5a34da 05:19、4cab35e 21:09、8ff8c95(09-24 起有分析端啟動命令列)、936d5b0(SMALL_F7_LIMIT_SECONDS 3→120、1.5 秒短時限測試)、eade8e7 的 `t >= MIN_TIMEOUT_SECONDS`、42ae513 的 117/94、b2fc512 把 EXPECTED_ENDPOINTS 改成 5 讀 + 送件、626cccb 改 narrate.py,phase14-demo 兩刪兩加、批號相同、是該目錄最後一筆提交、624df28、fcdcedf(兩張需求單 09-29 結案)。
- 程式現況:DSP ROUTES 11 支,稽核金鑰兩支;runner.py 沒有 environ;分析端與啟動器都不收、不帶 --ai-judge;Backend 有 claude_code 與 recording;MAPPED_ENUMS=17,各列舉的家對得上;`_RETIRED_DETAIL_KEYS` 只有 exam、answered_rounds,not_exercised 照樣對到 NOT_EXERCISED;TaskState=10,test_flow 沒碰 COMPLETED/BLOCKED/SUPERSEDED;TaskReader 六處;check_freshness 唯一呼叫者是 policy;_BLOCK_CODES 共 7 種,_PERMISSION_BLOCKS 共 4 種;KitServer 子類三支;MAX_GENERATION=3、MAX_CHANGE_RESTARTS=2、revision=1、MAX_REVISIONS_PER_TASK=50;SubmitStale 回到 COLLECTING_EVIDENCE、409 too_many_revisions 對 SubmitRejectedPermanently;維運結束代碼裡 9 只有 hypothesis 用;cvr 沒有呼叫端、pacing 固定傳 1/24、nine_rules 只用 exact_*;MODEL_LIMITS 與 INVESTIGATION_LIMITS 的數值;approval_uses 三個索引(第三個在 b34055f 加);RETENTION 2h、MAX_EVENTS_PER_CODE 200;核可表建在 inbox_store;scope_fingerprint 含政策版本;啟動器的 MODEL_VARIABLES 是 3 個設定加登入權杖,而且不匯入模型用戶端;launcher、flow 的 sha256 跟 claims 清單相符;page/state/present 已經沒有費用欄位;確認頁文字「這張表單不帶 AI 說明」;agent-flow 只有 README 引用。
- 測試綁定:新寫的 [test:] 都存在,而且會跑到句子說它驗的那段。test_f5_adversarial_name_preserves_rule_and_traceable_narrative 驗九條結論、9 次讀取、110、說明數字段不含名稱。test_one_exact_ratio_function_feeds_receipts_and_the_answer_key 在函式尾端無條件呼叫 answer_key_goes_through_exact_ratio。test_an_approval_does_not_hold_for_a_proposal_from_another_policy_version 用舊版本提案搭同一租戶指紋,拆掉比對那行斷言就會翻紅。改寫後的 test_an_injected_name_can_only_flip_propose_or_not 實跑 1 passed;ai_judge 對 PROPOSE 回 RuleContinue,舊的金額比對段確實走不到。
- 數字對照:phase13-investigation-adoption.md 的 36/23、中位 4236 毫秒、找不到錄製 0 筆;Phase14增量4 全套 3360 過、1 略過;test_ai_demo 20 支、narrate+flow 93 支(collect);F7 300 秒時限、16.1 秒紀錄;治理帳 code-phase14-inc2a 在 16:56、17:09、17:36 收斂;入庫錄製沒有假說呼叫者;Phase1 驗收 188 條。
- 圖譜一致性:d14、d1 都存在;[S1034] 已標 superseded;收件口那邊的 REVISIT 已改綁「第二個送件來源」;F1–F3 的驗證連結已從執行迴圈與提案收件口拿掉;各驗證紀錄的「2026-10-03 補」效期行和改過的 revalidate_when 跟正文對得上;被引用的節名都在(〈2026-09-25 第二次結案〉〈結案(2026-10-03)〉〈拿掉 AI 費用欄位〉〈頁面分區〉〈收件口拒絕代碼的分類〉〈使用者覆核(2026-09-23…)〉等)。沒找到其他「已裁定/已撤除」但別處還寫「待裁定」的句子(d4、速率限制、收據、平台版本都 grep 過)。
- 新搬出來的 REVISIT 行:除了上面 Phase11B 那條,其他(Phase0 11-21、F7效能 12-31、Phase3驗收 11-30/12-31、Mock-DSP 11-30、共用行程基礎 10-20、分析行程 10-20/12-31、確定性指標 11-21、靜態檢查閘 10-22、連線名額 Issue 11-30、Phase3 計劃 12-31)都還沒到期,條件也還沒成立。
- 沒抓到會因清單變動而指錯的位置指稱。原本寫的「開頭第一個/第二個重驗事件」「上一條更正」都已改成引名稱。

## 判不了的(程式碼答不了)

1. 使用者在 2026-10-03 的各項裁定:AI 說明只放報告(d1)、畫面只讀改成展示例外(d14)、速率限制先不做、平台版本缺口不補、收據撤除、SIGTERM 回頭條件的撤法。只能確認 decisions 欄位與 aef0f5d 的提交訊息有記,使用者本人有沒有這樣說查不到。
2. 共用HTTP伺服器連線名額 Issue 寫的「已交協調者」。
3. Phase14增量3驗證紀錄〈626cccb 重錄後的重驗〉的實跑結果(20 passed、93 passed、F7 20.3 秒、讀取 2700 次),以及一鍵展示的「2026-10-03 重跑 20.3 秒」。支數用 collect 對過是對的,執行結果本身查不到。
4. 存量筆記漂移 Issue「在暫存副本照配方改壞 approval.py,改壞前綠、改壞後紅」,以及執行迴圈「再拿掉指紋裡的政策版本,測試才紅」這兩個實驗。讀程式推得出來應該如此,但實驗本身查不到。
5. 模型用戶端:`~/.rtb/live-verification.json` 的內容(實測日、claude 2.1.283、哪一項通過)。這是帳號家目錄的檔案,沒有讀。
6. Phase14增量2a 的兩處時段推論:「r1 修前那次全套落在 16:00 前後」,以及 r2 那次全套落在 17:09 到 17:36 之間。收斂時間跟治理帳對得上,全套實際起訖時間查不到。
7. 靜態檢查閘說 CI 偶發失敗「就這五次」,這要看 CI 歷史。
8. Phase 13 [S1112] 寫的「協調者實跑確認」。
