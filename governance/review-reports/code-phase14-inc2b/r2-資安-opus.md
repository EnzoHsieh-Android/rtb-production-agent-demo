severity: clean

# 代碼審 Phase 14 增量 2b 第 2 輪:資安席(opus 頂替)

審查範圍:`r2-delta.patch`(必要時對照 `r2-snapshot.patch` 與 repo 現況,唯讀)。
立場:攻擊者視角,只報能被利用的洞;不報 DoS。

## 第 1 輪兩條的驗收

### 驗收 1:開 AI 時名稱注入誘使模型提案、蓋新政策版本 → 已修好

引句:「or (facts.settled_by_base and not self.raw_replay) \」
引句:「return AiOutcome(RuleContinue(), record)」

佐證 file: `src/rtb/analyzer/ai_judge.py:101`、`src/rtb/analyzer/ai_judge.py:158`、`src/rtb/analyzer/instrumented.py:150`、`src/rtb/analyzer/flow.py:436`

- 前置過濾補上第 1/2 條:暫停、1 小時異常、判斷點輸入建不起來,都在問模型之前用 `_rule` 結案。`_rule` 只拿基本三筆跑 `policy.explain`,這些出口全是不提案。
- 模型答 propose 不再直接建提案,改回 `RuleContinue`,並記下一列 AI 的 CONCLUSION。
- 這列讓 `inv.progress().used` 變真,下一次蒐證走 `rule_source`,開新規則輪 A/B/C 全量重讀。分析那一步 `_in_rule_round` 命中,走 `held_rule(rule_round.decide)`,不再經 AI 決策函式。
- 規則輪 A 步用重讀的現況重判暫停與異常(`_base_outcome`)。B/C 讀四查詢後交給九條,與沒開 AI 時同一支決策。
- 我逐條找過 `ai_judge` 裡所有會產生 `ProposalDecision` 的出口,只剩 `_rule` 與 `_fallback`。兩者都走正式規則,也都不讀模型輸出。
- 結論:開 AI 時的提案集合是規則輪提案集合的子集。模型只能讓結局更保守(do_not_propose、stop_insufficient 直接不提案),不能讓規則沒判值得加的廣告產生提案。
- 提案由規則輪照九條建,帶 `nine-rules-v1` 也名實相符了。

### 驗收 2:操作歷史查詢沒核對廣告編號 → 已修好

引句:「if body.get("campaign_id") != campaign_id:」
引句:「ToolEndpoint.DSP_HISTORY), campaign)」

佐證 file: `src/rtb/analyzer/dsp_client.py:345`、`src/rtb/analyzer/dsp_client.py:502`、`src/rtb/dsp/server.py:167`、`src/rtb/dsp/server.py:169`、`src/rtb/analyzer/task_store.py:22`

- 正式讀取在 `_query` 帶入任務的廣告編號,頂層編號不符就整份不收,結果是無效、不存原始回應,與另外三種查詢一致。
- 核過才拿掉 `campaign_id`,再照既有形狀白名單驗與存。規則輪讀回的是這份核過的原始回應。
- DSP 端截斷與不截斷兩種形狀都帶了頂層編號。
- `src/` 內 `check_history` 只有這一個呼叫端。不帶編號的形狀只剩評估與單元測試。
- task_store 檔頭已改成「決策路徑只有規則輪定案讀它」,跟實情一致。

## 新修正的攻擊面

### 名稱注入能不能讓 AI 提案繞過規則輪

不能:

- 名稱只進 `CAMPAIGN_TEXT`。規則輪與 `policy` 不讀它的內容。`rule_source` 的讀取計畫由已提交的規則步驟重算,不取 AI 選過的查詢。
- 注入能左右的只有模型答案:
  - propose:開規則輪,由九條定案。
  - do_not_propose、stop_insufficient:不提案。
  - 選查詢:在 AI 自己那幾輪多讀幾次,不進定案。
- 注入讓模型亂答導致驗證失敗:走 `_fallback`,同樣是規則輪。
- `held_rule` 用 `_ai_proposed` 看最後一輪調查紀錄,只用來改規則事件細因的前綴。它不改決策結果,前綴也是固定代碼,名稱文字進不來。

### raw_replay 能不能在正式路徑被開啟

不能:

- `raw_replay` 是 frozen dataclass 欄位,預設 False。
- 正式入口 `runner._judge_for`(`src/rtb/analyzer/runner.py:312`)建 `Judge` 時沒傳這個參數,命令列也沒有對應旗標。
- 全 `src/` 只有 `rtb/eval/investigation_eval.py:132` 設 True,而 `src/rtb` 內沒有評估套件以外的模組匯入 `rtb.eval`(已 grep)。
- 測試 `tests/analyzer/test_ai_judge.py:450` 斷言預設是 False。
- 縱深上,就算有人在正式路徑誤開 `raw_replay`,影響也只是暫停或異常的廣告會多問一次模型。模型的 propose 照樣走 `RuleContinue`,規則輪 A 步重判暫停與異常後結案,仍拿不到提案。

## 逐類檢查紀錄

1. **不可信輸入到危險操作**
   - 名稱注入到提案:見上,已被規則輪擋住。
   - `rule_source` 改成先讀查詢、最後重讀現況與 1 小時指標。A/C 比對因此涵蓋查詢期間的變動,改善了讀取時間差,沒有新入口。
   - 新增的 `_invalid_query` 把負數、跨窗不自洽、晚於決策時刻的歷史與調整列一律判證據不足,方向保守。偽造未來時間戳想躲過第 3 條「最近 3 天調過預算」,也會落到證據不足。
   - 規則事件細因的新前綴 `ai_propose_vetoed:` 接的仍是封閉代碼。
   - SQL 沒有新增字串組裝。
   - 評估端 `ai_said_propose` 直接記 WORTH,只影響離線報告。評估集是固定案例,不是攻擊者入口。
2. **登入與權限**
   - 開 AI 時的提案現在都由規則輪建,帶的政策版本名實相符。執行端與核可的版本比對、權限憑證、上限守衛都沒有改動。
   - `--hold-submit` 仍由 `_held` 與 `held_rule` 兩個出口攔。
   - `raw_replay` 碰不到正式路徑,見上。
3. **密鑰與個資**
   - 蒐證那一步新增 `CorruptedHistoryRow → FAILED`,`error_detail=repr(exc)` 的內容是資料庫列值,不含權杖。
   - `LOGIN_TOKEN_ENV` 只改了註解。沒有新的日誌輸出或密鑰讀取。
4. **加密與傳輸**
   - DSP 讀取沿用同一支 `_read`,讀取次數不變(C 步仍是 5 讀)。
   - 沒有新端點類型,也沒有關閉任何驗證。
5. **執行邊界**
   - 沒有新的子行程、指令組裝或反序列化方式。
   - `rule_round.progress` 與 `collect_plan` 改成純函式,仍不寫入、不讀時鐘。
   - 評估套件照舊不被正式程式匯入。

## 總結

第 1 輪兩條都修好了:開 AI 時模型的 propose 一律交給規則輪照九條重判,暫停與異常在問模型前就結案;歷史查詢也核對了頂層廣告編號。這輪新修正沒找到可被利用的洞。名稱注入最多讓結局更保守,`raw_replay` 只有評估用得到,就算誤開也繞不過規則輪。
