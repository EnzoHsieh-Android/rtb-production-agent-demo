# B4 修正結果(2026-10-03)

工作目錄 /Users/enzo/rtb-lumos-update;筆記路徑都在 docs/rtb-production-agent-demo-knowledge/ 底下。

## 逐列處置

筆記:行 | 形狀 | 處置 | 怎麼修或為什麼沒修 | 驗證證據
---|---|---|---|---
Verification/Phase5驗收紀錄:42 | X9 | 已修 | 2026-10-02 更正括號的尾巴「開頭最後一個重驗事件已發生,本篇沒有重驗紀錄」改成點名那件事本身:「原本列在重驗事件裡的『分析行程加啟動程式時確認有給操作查詢』已發生,2026-10-03 從重驗事件移到開頭 valid_under 的『2026-10-03 補』那條,重驗處寫在那裡」;沒用位置指稱 | `git show aef0f5d -- …/Phase5驗收紀錄.md`(revalidate_when 由 4 項剩 3 項,那一項搬進 valid_under);`src/rtb/analyzer/runner.py:126-132` 有 operation_lookup
Verification/Phase13增量1驗收紀錄:34 | X9 | 已修 | 同一個括號直接改正(沒有再疊一個):原本的重驗事件「第一次用真的 claude 錄說明與假說的批次」只有說明那半已發生,重驗處見 valid_under 的「2026-10-03 補」那條;假說那半仍沒發生,入庫錄製裡沒有假說呼叫者,重驗事件已改成只等真 claude 錄到原因假說 | `src/rtb/modelledger_view.py:45` 假說呼叫者是 `ops_hypothesis`;recordings/model 各目錄的 caller:phase13-demo 3 investigation+3 narrative、phase13-investigation-eval 84 investigation、phase14-demo 2 narrative、phase15 3 rule_mining,沒有 ops_hypothesis
Issues/確認頁顯示AI說明時的收據還沒做:13 | H2 | 沒修:清了會再犯/工具不支援 | 摘要 DECISION 在開頭欄位 summary 裡;lumos 沒有改 summary 單行的指令,依規定不手改。錯誤原文:`擋下:summary 不能用 set 改,檔案沒動。set 只能改這些純量欄位:['about_code_stamp', 'created', 'pitfall_ask', 'pitfall_source', 'regen', 'responsibility', 'self_audit', 'signed_off', 'status', 'type', 'updated'],以及整欄換掉的 valid_under、revalidate_when(清單欄位用 lumos append;決策新增/翻案用 lumos decision-add / decision-supersede)`。要工具機制:一個能改 summary 裡單一前綴行的指令(例如 `lumos summary-set <節點> DECISION "<新句>"`)。另外,正文第 23 行 2026-10-02 括號裡的「補做或撤掉仍待使用者裁定」已就地改成「當時…還待裁定,2026-10-03 已裁定撤掉,見開頭的結案說明」(正文第 19 行橫幅本來就寫結案) | `python3 scripts/lumos set "Issues/確認頁顯示AI說明時的收據還沒做" summary x` 的輸出如左;`lumos decisions Projects/RTB_Phase11B大模型接入_計劃` d1(2026-10-03)
Systems/Mock-DSP:80 | P2 | 已修 | 拿掉已成立的 `[when-status:Projects/RTB_Phase4佇列與重新投遞_計劃=doing|done]`;改寫成「Phase 4 2026-09-23 已完成,這支測試仍沒補(附查證指令)、找不到會自然逼出它的後續事件,改綁日期」,新的 `REVISIT:2026-11-30` 要做的事:補兩個獨立行程搶同鍵測試、斷言輸家拿可重試錯誤;決定不補就寫理由、改記成刻意不做 | Phase4 計劃 `status: done`(42ae513 2026-09-23 標完成);tests/dsp/test_store.py:229、test_server.py:203 都是 threading;`grep -rn "兩個獨立行程\|cross_process" tests/` 無
Projects/RTB_Phase9可觀測與SLO_計劃:5 | F1 | 已修 | `lumos set … updated 2026-10-03` | aef0f5d(2026-10-03)改過正文(加 REVISIT 2026-11-15、改未排除段)與 related
Verification/事故F2_確認前當機不重複副作用:5 | F1 | 已修 | `lumos set … updated 2026-10-03` | aef0f5d 把 status 改 superseded、加 10-03 說明段
Issues/確認頁顯示AI說明時的收據還沒做:5 | F1 | 已修 | `lumos set … updated 2026-10-03`(今天也改了正文) | 7732b7e 2026-10-03
Systems/模型用戶端、規則模式探索評估、Mock-DSP、靜態檢查閘、Issues/Phase12需要可看任務階段與處置的HTML報告:5 | F1 | 已修(四篇)/原判有誤(規則模式探索評估) | 模型用戶端、Mock-DSP、靜態檢查閘、Phase12 Issue 今天都有改正文,`lumos set updated 2026-10-03`。規則模式探索評估沒改:aef0f5d 對它只改了 updated(09-27→10-02),最後一次實質改動是 70608a0(2026-10-02,改 v1 雜湊與預檢那幾句),所以 updated 2026-10-02 正確。附帶:靜態檢查閘與 Phase12 Issue 在 aef0f5d 也只動了 updated,最後實質改動同樣是 10-02,稽核員說「提交當天應寫 10-03」對這兩篇也不成立,只是今天我改了才變 10-03 | `git show aef0f5d -- <各檔>`(用排除 +++/--- 的過濾看全部增刪行);`git show 70608a0 -- Systems/規則模式探索評估.md`
Systems/Mock-DSP:63 | E1 | 已修 | 開頭句改成「端點全集以 `src/rtb/dsp/server.py` 的 `ROUTES` 為準」並附可重跑查詢,接著列出全部 11 個端點(含作廢、逐日、過去調整、依時間找游標、依游標列操作,後兩支註明要稽核金鑰) | src/rtb/dsp/server.py:101-114 ROUTES 共 11 條
Systems/Mock-DSP:6 | F1 | 已修 | `lumos set responsibility` 補上「逐日成效與過去調整(含補值表)的存取與唯讀端點、展示種子、要稽核金鑰的列操作唯讀端點」,不負責那半改成「比率等指標計算」 | src/rtb/dsp/store.py:67 `operation_budget_backfill`;server.py:206 `_require_audit_key`;src/rtb/dsp/seed.py 在本篇 about_code
Systems/靜態檢查閘:8-9 | F1 | 原判有誤 | 測試檔在 lumos 的每支檔有家檢查裡本來就豁免,不需要家 | scripts/lumos:7205-7207 docstring「豁免層也同一套:排除 glob、測試檔、.lumos/config 的 ignore——這些每支檔有家不要求有家」
Systems/靜態檢查閘:34 | P2 | 已修 | 「若之後有不穩…要回頭看」改成寫明已發生並各自修掉的五次:連線名額測試偶發逾時(09-22)、F7 超過 60 秒(09-24,09-25 放寬 120 秒)、Linux 殭屍行程群組、核銷期限浮點進位、並行預留固定等待(09-24/25,在模型用戶端的實務隱患);REVISIT 2026-10-22 改成「把這五次之外的偶發失敗列出來、各自開單或記進那支檔的家」 | Issues/共用HTTP伺服器連線名額測試在CI偶發逾時(created 09-22)、Issues/F7端到端在CI上偶爾超過60秒、600d769(F7 上限 120 秒)、8238dfb(2026-09-24 Linux 殭屍群組修正)、Systems/模型用戶端 的三條 PITFALL
Systems/靜態檢查閘:28(連帶 .github/workflows/ci.yml:30) | C1 | 程式要改 | 筆記第 28 行本身沒寫 60 秒;錯的是 ci.yml 的註解,不在我能改的範圍 | `.github/workflows/ci.yml:30`「F7 的 60 秒上限在那裡」;`tests/executor/test_f7_end_to_end.py:97` `< 120`
Systems/模型用戶端:167 | W1 | 已修(設定來源那半)/要人裁(4000 那半) | 「`--setting-sources` 目前傳空字串,能不能用以實測為準」劃掉,註明本機啟用紀錄(實測日 2026-09-26、claude 2.1.283、空暫存 HOME)設定來源那項通過、對照組附註 connected,程式仍傳空字串,換機器或升級要重跑。注意稽核員寫 09-25,啟用紀錄檔上的日期是 09-26。4000 那半沒動:實測量到固定附加輸入 381(≤4000),要不要把常數改成實測值是取捨,交人裁 | `~/.rtb/live-verification.json`(唯讀):checks.setting_sources_suppress_user_settings=True、notes.settings_control=connected、fixed_input_tokens_seen=381、checked_on=2026-09-26;src/rtb/modelclaude.py:50 `SETTING_SOURCES = ""`;src/rtb/modelcore.py:60 `CLAUDE_FIXED_INPUT_TOKENS = 4000`
Issues/Phase12需要可看任務階段與處置的HTML報告:38(連帶 37) | D1 | 已修 | 第 38 行收據需求加「已被取代:Phase 11B 決策 d1(2026-10-03)讓說明不進核可表單,收據不做,[S1034] 標作廢」;第 37 行加 2026-10-03 更正:「核可畫面有模型說明」前提被 d1 取代,確認頁寫明不帶 AI 說明,逐項確認數字那半照舊 | `lumos decisions Projects/RTB_Phase11B大模型接入_計劃` d1;src/rtb/demo/page.py:1092「這張表單不帶 AI 說明」
Issues/Phase12需要可看任務階段與處置的HTML報告:39 | H3 | 已修 | 後半句加更正:第 5 版起模型後端改本機 Claude Code、沒有模型金鑰;展示啟動器不匯入模型用戶端,用自己的子行程環境白名單(Phase 12 [S1003]),見 Systems/一鍵展示 | `grep -rn "子行程環境\|不含模型金鑰" src/rtb`:只有 modelclaude.py:607 與 launcher/__init__.py:99 `child_env`;launcher 的 import 沒有模型用戶端;Phase11B 計劃第 395 行第 5 版
Issues/Phase12需要可看任務階段與處置的HTML報告:32 | S1 | 已修 | 句後加「(2026-10-03 更正:這是 2026-09-24 開單時的狀況;同日提交 8ff8c95 起分析端有正式啟動命令列,見 Systems/分析行程流程與檢查點)」 | `git show 8ff8c95`(2026-09-24「分析端有了正式啟動命令列」);runner.py 的家是 Systems/分析行程流程與檢查點
Issues/Phase12需要可看任務階段與處置的HTML報告:41 | W1 | 已修 | 拿掉 `REVISIT:2027-01-31 …`,留一句說明:需求已寫進 Phase 12 計劃(開頭就引這篇)、計劃已完成、這篇 2026-09-29 結案 | Phase12 計劃 `status: done`、第 28 行引本篇;`git show fcdcedf` 把本篇 status open→resolved

## 轉給別篇

1. Verification/Phase13增量3驗收紀錄:31 —— 2026-10-02 更正括號寫「開頭第一個重驗事件已發生——2026-09-25 已入庫 phase13-investigation-eval…」,但 aef0f5d 把那個事件(「第一次用真的 claude 錄調查評估批次並入庫時」)從 revalidate_when 拿掉、改寫進 valid_under 的「2026-10-03 補」;現在開頭第一個重驗事件是「系統提示、標準答案或採用門檻再改時」,括號指錯了。建議照本篇 Phase5/Phase13增量1 的寫法,把位置指稱改成點名那件事本身,並說重驗處見 valid_under 的「2026-10-03 補」那條。證據:`git show aef0f5d -- …/Verification/Phase13增量3驗收紀錄.md`。
2. Projects/RTB_Phase11B大模型接入_計劃:227(選做,低)—— 「設定來源參數目前傳空字串…可行的組合以協調者實測⑦為準」是已完成計劃的歷史句;可在句後加「(2026-10-03 更正:本機啟用紀錄 2026-09-26 實測設定來源那項通過,空字串可用,見 [[Systems/模型用戶端]])」。

## 程式要改

- `.github/workflows/ci.yml:30` 註解「不拉長 checks 那個工作的時間(F7 的 60 秒上限在那裡)」:F7 上限 2026-09-25 已放寬成 120 秒(`tests/executor/test_f7_end_to_end.py:97`、提交 600d769)。建議改成「(F7 端到端有時間上限,見那支測試)」拿掉數字,或改成 120 秒。改了要確認接線檢查測試(test_static_wiring)仍綠。

## 各篇 lint 結果(`python3 scripts/lumos lint <節點>`)

- Projects/RTB_Phase9可觀測與SLO_計劃 — 0 問題
- Verification/事故F2_確認前當機不重複副作用 — 0 問題
- Issues/確認頁顯示AI說明時的收據還沒做 — 0 問題
- Issues/Phase12需要可看任務階段與處置的HTML報告 — 0 問題
- Systems/Mock-DSP — 0 問題
- Systems/模型用戶端 — 0 問題
- Systems/靜態檢查閘 — 0 問題
- Verification/Phase5驗收紀錄 — 0 問題
- Verification/Phase13增量1驗收紀錄 — 0 問題

沒動的指派篇:Systems/規則模式探索評估(見上,updated 本來就對)、Verification/Phase14增量2b驗證紀錄、Issues/Phase14後筆記漂移清理(B4.txt 沒有分到它們的列)。
Phase5驗收紀錄、Phase13增量1驗收紀錄原本沒有 updated 欄位,`lumos set updated 2026-10-03` 替它們補上了。
