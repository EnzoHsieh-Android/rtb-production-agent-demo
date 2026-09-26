severity: minor

# 代碼審 r3 鏡頭A(全部修正差異:正確性、時序、帳本隔離、測試殺傷力)

審材:`governance/review-reports/code-phase14-inc2b/r3-delta.patch`(逐 hunk 讀完,2192 行),必要時對照 r3-snapshot 與工作樹原始碼。
結論一句話:這一輪沒有找到會讓系統誤提案、或讓錄製模式再寫進真帳本的 blocking 問題;r2 的 8 條加 3 項新增修正都驗收通過。另有 4 條新的 minor,都是測試保護不夠或說明不一致,不觸發新一輪。

實跑紀錄(都在本工作樹或 scratchpad 複本,沒寫 ~/.rtb):
- r2 修正綁定的 10 支測試:`10 passed`。
- `tests/dsp tests/domain` 加 F4/F5 端到端與信任邊界:`660 passed`。
- 72 筆評估錄製重播 `rtb.eval.investigation_eval --verify --ledger <scratchpad>/ledger-r3A.db`:結束代碼 0,「找不到錄製:0 筆」,「驗收:通過」。
- 變異測試(在 scratchpad 複本 `r3A/` 裡做):細節寫在發現 1、2。

---

## 第 2 輪修復驗收

1. 架構對齊-1(`check_history` 同款):驗收通過。現在廣告編號必填(測試用 `inspect` 斷言沒有預設值),頂層形狀連編號一起驗,多一個欄位 `extra` 就拒收,核過的編號留在回傳值。呼叫端 `dsp_client.py:500` 有傳 `campaign`;`rule_round` 從原始回應只讀 `history`/`summary`/`truncated`,所以多一個編號欄不影響轉換。72 筆重播錄製鍵沒變(見上方實跑)。
2. 鏡頭1-1/鏡頭2-2(否決標記只標首次定案):驗收通過。`first_rule_round_after_ai_propose` 比的是 `investigation_rounds.seq` 與 `rule_events.seq`,兩者都是 `tasks.seq` 同一個序號空間(file: `src/rtb/analyzer/task_store.py:98`、`:111`),比大小有意義。定案那一刻自己的 decided 事件還沒提交,所以只看得到先前的定案,判斷正確。這個改動只改細因前綴、不改 NoAction/Proposal 結果,不會影響要不要提案。
3. 鏡頭2-1(規則事件表毀損轉 FAILED):驗收通過。`rule_events` 包成 `CorruptedHistoryRow`,測試把 `round_id` 改成 `'garbage'` 後蒐證步會轉 FAILED。
4. 外家 finder-1(跨日原因優先):驗收通過。`_invalid_query` 第一道就查 `_other_day`。評估案例不給 `read_at`,所以 72 筆不受影響。副作用:正式路徑上跨日加第 3 條同時命中時,細因從第 3 條改記成 `day_boundary`,兩者都是不提案,不影響錢。
5. 說明一致:驗收通過。grep「AI 直接提案/否決是增量 3/前置過濾不動」只剩改寫後、標了已失效的句子。
6. [S1407] 補測試:驗收通過,屬於補綁定。propose 與退回兩條路都斷言只有一輪 A/B/C、只定案一次、只呼叫一次模型、提案 7 筆證據參照。這支在 r1 的程式上就是綠的,驗證紀錄也照實寫了。
7. 錄製模式沒帳本時,模型閘道拒絕:驗收通過,而且是失敗安全的——入口漏給帳本時會拒絕啟動,不會退回真帳本。
8. 命令列入口改用暫存帳本:行為驗收通過。src 裡已經沒有任何錄製模式分支再退回 `live_ledger_path()`(剩下的呼叫都是即時模式、核銷、實測或唯讀統計)。`record.py` 改回真帳本時 `test_recorded_entries_never_touch_the_account_ledger` 會翻紅。但 narrate/runner 兩處沒有測試保護,見發現 2。
9. 展示驅動給情境帳本:驗收通過。沒開 AI 時也帶 `--recorded-ledger <情境目錄>/model-ledger.db`,說明與假說兩支命令列都收這個參數。

---

## 發現 1:觀察器「只套 AI 之後第一輪」的修正沒有任何測試守著
severity: minor
blocking: 否

引句:「return not any(opened[-1] < at < seq and event.event == rule_round.Event.DECIDED」

- 失敗場景:我在複本把 `_right_after_ai` 改回 r1 的判法(只要之前有 AI 開輪就回真,即 `return bool(opened)`)。`tests/demo` 加上 `tests/analyzer` 裡用 `-k "observ or basis or demo or f4 or f6 or 409"` 選出的 612 支全綠;唯一的紅是已知的入庫展示錄製那支。
- 意思是:409 之後重讀的新輪,展示頁會不會又被畫成「省略新鮮度與配速」,目前沒有測試會翻紅。
- 圖譜〈一鍵展示〉r2 段這條也沒有附 `[test:]`。
- 只影響展示畫面,不影響提案,所以是 minor。

## 發現 2:說明與分析端驅動的「錄製沒帶帳本用暫存帳本」兩處沒有測試殺傷力,而測試說明宣稱涵蓋了
severity: minor
blocking: 否

引句:「recorded_ledger=_recorded_ledger(args, errors))」

引句:「評估重播、原因假說、Phase 10 評估、說明命令列在錄製模式沒帶帳本時,都不退回帳號家目錄那一本」

- 變異 a:把 narrate 與 runner 裡的 `scratch = modelgate.recorded_scratch_ledger()` 換成 `modelgate.live_ledger_path()`,等於讓錄製重播寫回真帳本。`test_narrate.py`、`test_runner.py`、`test_model_entry_contracts.py`、`test_suite_isolation.py`、`test_modelclient.py`、`test_shared_entry.py` 共 106 支全綠。
- 變異 b:自然退回的寫法,兩處改回 `recorded_ledger=args.recorded_ledger`。上述 6 支測試檔再加上 `tests/model` 全部、`test_investigation_e2e.py`、`test_ai_launcher.py`,共 `198 passed, 1 skipped`。
- `test_recorded_entries_never_touch_the_account_ledger` 的說明寫涵蓋「說明命令列」,但它只跑了 `record.run` 與 `investigation_eval.run`,假說也只是 `del hypothesis`。
- 變異 b 在實際執行時會被閘道拒絕、不會寫真帳本,所以現況安全;變異 a 這種回歸則沒有網子接住。r2 的事故起因正是入口退回真帳本。
- 建議:narrate 與 runner 各補一支「錄製模式、沒帶帳本,跑完真帳本不存在、標準錯誤有暫存帳本路徑」的測試。

## 發現 3:暫存帳本提示在還不知道模式時就印出,即時模式也會印「錄製模式……不碰帳號家目錄那一本」
severity: minor
blocking: 否

引句:「scratch = modelgate.recorded_scratch_ledger()」

- 重現:`narrate._recorded_ledger(Namespace(ledger=None, recorded_ledger=None), buf)` 與 `runner._recorded_ledger(...)`,兩者都印「錄製模式沒有指定帳本,這一趟用暫存帳本(不碰帳號家目錄那一本):/var/folders/…」,而且暫存目錄已經建好。
- 這兩個函式在 `open_gate` 判模式之前就被呼叫(file: `src/rtb/analyzer/narrate.py:254`、`src/rtb/analyzer/runner.py:262`)。判成即時時,說明與分析端驅動實際寫的是帳號家目錄那一本,標準錯誤卻說不碰它,會誤導看花費的人。
- 每次啟動都會在系統暫存區留一個 `rtb-recorded-ledger-*` 目錄,不清掉。
- 對照:`investigation_eval` 有用 `gate.ledger == scratch` 判斷完才印,兩邊做法不一致。
- 另外:計劃 r2 段寫「路徑印在標準錯誤或 notices」,但 `record.py` 的暫存帳本完全不印路徑。

## 發現 4:否決判斷的純函式說明寫「展示觀察器也用」,觀察器其實另寫了一套
severity: minor
blocking: 否

引句:「"""純函式(展示觀察器也用):AI propose 之後還沒有規則輪定案過。"""」

- `first_rule_round_after_ai_propose` 只有 `ai_judge` 自己呼叫(grep src/tests)。觀察器用的是自己的 `_right_after_ai`(file: `src/rtb/demo/observe.py:292`),而且判準不同:它把「AI 退回」也算開輪,純函式只看「AI 答 propose」。
- 圖譜〈分析行程流程與檢查點〉r2 段也寫它是「純函式」,讀者會以為兩邊共用一份。
- 這跟 r2 架構對齊-1 在意的「同一規則另立第二套」是同一類問題。建議把說明改成實況,或讓觀察器改用共用函式。

---

## 圖譜固定席(lumos impact --diff origin/main..HEAD,13 席)

hook 沒有附固定席,以下是自己跑出來的;★INVARIANT★ 是用 `lumos contracts` 逐條對照判的。

- Systems/Mock-DSP(4 條合約:冪等鍵、原子寫入、F1 DSP 側、版本拒收):不影響。這次 delta 在 `server.py` 只改了一行註解,寫入路徑沒動;`tests/dsp` 全綠。
- Systems/分析行程流程與檢查點(F5 注入;每步落地成新歷史列):不影響。F5 方面,`check_history` 只收緊了輸入,名稱文字的處理沒變,F5 端到端測試綠。落地方面,規則事件改動只在讀取端包例外、轉 FAILED,仍經既有的單列提交,沒有新的寫入路徑。
- Systems/任務流程領域模型(提案白名單、證據新鮮度、終點狀態、領域匯入白名單):不影響。`nine_rules._other_day` 只用 `datetime`/`UTC`,沒有新的匯入;毀損轉 FAILED 走的是既有合法轉換;domain 測試綠。
- Systems/共用行程基礎(故障注入旗標與只綁本機;分析端送不出故障標頭):不影響。delta 沒碰 httpkit 與伺服器建構。
- Systems/執行迴圈(F1/F2/F3/F4/F7):不影響。執行端沒改。
  - F4 的「另開接續任務重讀再規劃」這一環,現在 AI 模式 409 之後的重讀輪只少了否決前綴,決策結果不變。
  - F3 的「只有一方花分析費」這一環,錄製模式改寫暫存帳本,即時模式的花費帳路徑沒變。
- Systems/提案收件口(F6 死信重跑每關、同修訂只收一份、修訂加 1):不影響。收件口與提案內容都沒動;`build_proposal` 只改了說明文字。
- Systems/確定性指標計算(算不出值不變成 0):不影響。這次沒改指標計算;跨日改成回證據不足,正是「不知道」那一邊。
- Systems/一鍵展示:說法與實作相符(驅動一律給情境帳本、觀察器只套第一輪),但觀察器那條沒有測試,見發現 1。
- Systems/模型用戶端(沒有登記合約):閘道錄製模式沒帳本就拒絕,是有意翻掉舊測試「沒給帳檔就是家目錄那一本」,計劃與 Issue 都寫了出處。即時模式「帳寫死家目錄」的行為不變(`test_a_live_gate_ignores_the_recorded_ledger` 綠)。說明文字的問題見發現 3、4。
- Systems/正式九條判斷領域規則:「跨日細因優先」與筆記相符,72 筆格與答案不變(重播通過)。
- Systems/評估與Jev決策點:Phase 13 與 Phase 10 評估改用暫存帳本,與筆記相符。`record.py` 沒印路徑,筆記只宣稱評估命令列會印,本身沒有矛盾(計劃的泛稱見發現 3)。
- Systems/服務水準與燒損告警:假說命令列錄製沒帶帳本時,把暫存帳本路徑記進 notices,與筆記相符,且有測試。
- Systems/追蹤檢視:只動了 `test_ops_boundaries` 的名字白名單,多了兩個只准假說命令列用的模型用戶端名字,不放寬維運檔的寫入權。不影響。

表態記錄(棧別效能題):這次 delta 沒有效能相關的宣稱。`recorded_scratch_ledger` 每次呼叫 `mkdtemp`,屬於一次性的啟動成本,不在熱路徑上;殘留目錄的問題已併進發現 3。
