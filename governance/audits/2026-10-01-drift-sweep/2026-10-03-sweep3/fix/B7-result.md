# B7 修正結果(2026-10-03)

工作目錄 /Users/enzo/rtb-lumos-update。沒有任何 git 寫入指令;沒改 src/tests/tools/claims/scripts/.lumos/CLAUDE.md;沒跑 doctor、drift、全套測試。

## 每列處置

`筆記:行 | 形狀 | 處置 | 怎麼修或為什麼沒修 | 驗證證據`

- Projects/RTB_Agent_Phase0架構:196(核准通道)+ 第 164 行圖 | X9 | 已修 | 「核准通道」那句 2026-10-02 補充裡的「正式路徑照這樣做」後面加 2026-10-03 更正括號:正式路徑沒有獨立核准檔,Phase 6 增量 3 起核可表由收件口模組建在執行行程自己的資料庫(只增不改);簽章、執行端驗過才採信照舊,實際綁的欄位指到 [[Systems/寫入能力憑證]]。圖下加一段 2026-10-03 更正,講圖上「核准檔(獨立 SQLite)」沒做。照規則 3 同篇〈行程切分〉「沒有提供的」那句「動核准檔」也加了更正括號 | src/rtb/executor/approve.py:49 `InboxStore(args.db)`、`--db` 就是收件口資料庫;src/rtb/executor/inbox_store.py:224 `CREATE TABLE IF NOT EXISTS approvals`、:1506 `add_approval`;src/rtb/executor/execution.py 從同一個 store 讀核可;Phase 6 計劃第 294 行「核可表由收件口模組統一建表(只增不改)」位在〈增量 3 設計〉段;全圖譜 grep「核准檔|核可檔」別篇 0 筆
- Projects/RTB_Agent_Phase0架構:195(畫面只讀的更正) | X9 | 已修 | 「這算不算翻掉決策 d4 待使用者裁定」改成:使用者 2026-10-03 裁定為展示例外,d4 改由 d14 取代(正式環境照舊只讀,一鍵展示頁可觸發與簽核可,只在本機展示環境、經同一道執行閘與核可驗證),指到開頭 decisions 的 d14。決策欄位沒動 | 同篇開頭 d4 `valid: false`、`superseded_by`、`ended: 2026-10-03`;d14 `decided: 2026-10-03`、`valid: true`
- Projects/RTB_Agent_Phase0架構:239(〈決策〉WHY 只讀) | X9 | 已修 | 「跟這條的關係待使用者裁定」改成:使用者 2026-10-03 裁定記成展示例外,這條改由決策 d14 取代 | 同上;全圖譜 grep「d4」「畫面只讀 / 待使用者裁定」:別篇只有 Issues/存量筆記漂移等工具修復:85 把「畫面只讀」當成漂移類型的例子,不是待裁定句,不用改
- Projects/RTB_Agent_Phase0架構:115(d1–d13) | X9 | 已修 | 直接改正那個 2026-10-02 更正括號:「d1–d13」改成「開頭 decisions 欄位的各條決策」,不寫上限,免得再加決策又過期 | 開頭有 d14;全圖譜 grep「d1–d13」0 筆
- Projects/RTB_Agent_Phase0架構:109(pytest RETIRE-IF) | P2 | 已修 | 句尾補「(2026-10-03:Phase 1–4 已做完,條件沒成立——Phase 4 驗收那次提交 42ae513 的 tests 底下已有 117 處 fixture 或參數化標記(其中參數化 94 處),查法 `git grep …`)」。只講 Phase 4 驗收那一刻的範圍,沒有拿現在全套的 398 處來講(398 包含 Phase 5 以後的測試) | `git grep -c -E "@pytest.fixture|pytest.mark.parametrize" 42ae513 -- tests` 合計 117(33 支檔);參數化單獨 94;42ae513 是「docs: Phase 4 驗收紀錄,佇列與重新投遞標成完成」;現在 `rg` 是 398(113 支檔),稽核員的數字正確但範圍比 RETIRE-IF 大
- Projects/RTB_Agent_Phase0架構:331(驗收指令還沒驗) | U1 | 已修 | 照規則 5 不改原句,句後加「(2026-10-03 更正:2026-09-22 Phase 1 已用這條指令實際驗過,188 條通過,見 [[Verification/Phase1驗收紀錄]])」 | Verification/Phase1驗收紀錄:30「驗收指令 `.venv/bin/python -m pytest -q`:188 條通過」(2026-09-22,提交 45af951)
- Systems/寫入能力憑證:38(檔案說明句結構亂) | X9 | 已修 | 改寫成:本篇管四支檔。寫入能力憑證本身兩支:capabilitykit.py(格式機制與讀金鑰)、capability_signer.py(簽發器、安全讀租戶設定與內容快取);人工核可兩支:approval.py(核可聲明、驗章、範圍指紋)與管理工具 approve.py,見〈人工核可憑證與管理工具〉 | about_code 四支;src/rtb/executor/approval.py 開頭說明「簽、驗章、範圍指紋、有效判斷」;capability_signer.py:137–150 `_config_cache`、`read_tenants`
- Systems/寫入能力憑證:6(responsibility) | F1 | 已修 | 用 `lumos set … responsibility` 補上「讀租戶設定的內容快取」與「人工核可(核可聲明、驗章與範圍指紋,核可管理工具)」;不負責的部分照舊 | 同上;正文〈人工核可憑證與管理工具〉〈讀租戶設定的內容快取〉兩節
- Systems/寫入能力憑證:26(TEST 摘要漏檔) | G1 | 已修 | TEST 行補上 tests/executor/test_approval.py(人工核可、管理工具,以及簽發與處理待核可共用簽發器快取)、tests/executor/test_config_cache.py(讀租戶設定的內容快取) | `rg -l "def <名>"`:test_the_approval_tool_signs_with_its_own_key、test_admin_and_executor_agree_on_the_scope_fingerprint、test_the_capability_never_outlives_the_approval_it_used、test_signing_and_awaiting_approvals_share_the_signers_cache 都在 tests/executor/test_approval.py;test_the_tenant_config_is_revalidated_whenever_its_bytes_change、test_the_config_cache_never_skips_the_ownership_and_mode_checks 在 tests/executor/test_config_cache.py
- Issues/Phase11後接入大模型API的三個階段:42(REVISIT 已做完) | W1 | 已修 | 刪掉 REVISIT 行,改成〈結案(2026-09-29)〉段:需求已由 [[Projects/RTB_Phase11B大模型接入_計劃]] 接手(該計劃〈這份計劃在解決什麼〉引用本篇、三個接入點都列入範圍),計劃已完成(status: done);寫明回頭條件已做完、2026-10-03 拿掉;接入點 2 的說明放哪裡後來改裁,以計劃的決策紀錄為準 | `git show fcdcedf`(訊息「內容已寫進對應計劃,計劃也做完了,改成結案」);開頭 status: resolved;Phase 11B 計劃 status: done、第 31 行引用本篇
- Verification/Phase10驗收紀錄:24(說沒有重驗紀錄) | X9 | 已修 | 照規則 5 直接改正那個寫錯的 2026-10-02 更正括號:「本篇沒有重驗紀錄」改成「重驗在 [[Verification/Phase14增量4驗證紀錄]](2026-09-27 重跑、報告重產)」 | Verification/Phase14增量4驗證紀錄〈怎麼重跑〉Phase 10 那條 `rtb.eval.record` 指令、〈重播數字〉Phase 10 段(300 筆,五格標舊資料不足);同篇開頭 valid_under 2026-10-03 那條也這樣寫
- Projects/RTB_Phase14正式規則照九條判斷_計劃:100(帶 --ai-judge 的寬限) | X9 | 已修 | 那個 2026-10-02 更正括號改正成:增量 3 起 `--ai-judge` 參數與 AI 步寬限一起撤除——分析端命令列不收這個參數、啟動器也不帶,寬限只剩規則模式那一種 | src/rtb/analyzer/runner.py:73–78 只收 --db/--dsp-url/--inbox-url/--timeout-seconds/--interval-seconds/--owner(allow_abbrev=False);src/rtb/stepbudget.py:11–13;src/rtb/demo/launcher/__init__.py:139–149 `stop_grace_seconds` 只看 --timeout-seconds;src/rtb/demo/driver.py:280 `analyzer_args`。全圖譜 grep「帶 --ai-judge」:別篇的句子都在已標「Phase 14 增量 3 起撤除」的歷史段或 Phase 13 已完成計劃,不用轉
- Projects/RTB_Phase14正式規則照九條判斷_計劃:405(spec-gate 相依測試紅沒記結果) | P2 | 已修(範圍縮小) | 句尾補:「(2026-10-03 補結果:沒有留下 spec-gate 重跑的紀錄;2026-09-27 在能綁本機埠的工作樹跑全套 3360 過、1 略過,見 [[Verification/Phase14增量4驗證紀錄]]——當時那九支沒列名,沒能逐支對照)」。沒有照稽核員建議直接寫成「結果:全套過」,因為那九支當時沒列名、治理帳也查不到 spec-gate 重跑紀錄,只能講全套過這一件 | Verification/Phase14增量4驗證紀錄〈其他檢查〉最後一條「全套(2026-09-27,本工作樹):3360 passed、1 skipped」;`grep "Phase14正式規則" docs/.governance-log.jsonl` 只有 check-s5、delguard,沒有 spec-gate 結果

已修 13 列(其中第 196 行那列連帶修了第 164 行的圖,並照規則 3 多修同篇「動核准檔」一句)。沒修 0 列。

## updated

用 `lumos set <節點> updated 2026-10-03` 更新了改過的五篇:Projects/RTB_Agent_Phase0架構(原本就是 10-03)、Systems/寫入能力憑證、Issues/Phase11後接入大模型API的三個階段、Verification/Phase10驗收紀錄、Projects/RTB_Phase14正式規則照九條判斷_計劃。

任務說明列給我的其他幾篇(F7效能_計劃、RTB_Phase10評估與Jev決策點_計劃、Phase1驗收紀錄、正式九條判斷領域規則、調查實演、事故F7_總曝險到門檻就停、現行規則只看有沒有投放就提案)B7.txt 裡沒有它們的列,我沒動。

## 轉給別篇

- Verification/Phase12增量2驗收紀錄:18、Verification/Phase13增量3驗收紀錄:31:2026-10-02 更正括號用了「開頭第一個重驗事件已發生」這種位置指稱(規則 2 禁止)。建議改成寫出那個重驗事件本身的內容。只是 grep 時順便看到,沒細驗,也不在我的清單裡。

## 程式要改

無。

## lint

- Projects/RTB_Agent_Phase0架構:0 問題
- Systems/寫入能力憑證:0 問題
- Issues/Phase11後接入大模型API的三個階段:0 問題
- Verification/Phase10驗收紀錄:0 問題
- Projects/RTB_Phase14正式規則照九條判斷_計劃:0 問題
