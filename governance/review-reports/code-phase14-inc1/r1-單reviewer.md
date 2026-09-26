severity: minor

# Phase 14 增量 1 代碼審(r1,單一外部審查員)

審材:`governance/review-reports/code-phase14-inc1/r1-snapshot.patch`(14 檔,逐 hunk 讀完)。代碼部分與 `git diff origin/main..HEAD -- src tests claims` 逐檔一致;origin/main..HEAD 另外出現的 `scripts/lumos` 等差異來自 origin/main 在分支點(b9d8a70)之後的新提交,不是本分支的改動,不在審查範圍內。

## 結論先講

- **新舊標準答案的差異都在裁定 6、7 的範圍內,原 72 筆逐筆沒有變。** 我用 72 筆原案例加上 2,880 筆隨機擾動案例(改列內缺值、no_data、少列、days_ago 重複、分母零、歷史列缺時間或沒時區、過去調整缺值或負數、長窗缺值、1 小時無價值),拿 origin/main 的 `answer/gold` 跟新版逐筆比:
  - 原 72 筆:0 筆不同。
  - 擾動案例:1,014 筆不同,**每一筆都是新版變成「九格之外的證據不足」**(cell=None、verdict=insufficient)。
  - 其中一部分是舊版原本就會丟 TypeError 的輸入(預算缺值、committed_at 缺值或沒時區)。
  - 另外 6 筆是「近期調整列排在缺值列前面」,新版判成 `recent_budget_change`,舊版丟例外。
  - 沒有任何一筆從證據不足變成值得加或不值得加。
  - 其餘差異(no_data、缺天數或天數重複、列內缺值、長窗轉換缺值)都對得上計劃第 82 行和裁定 6、7 的文字。
- **「不改正式行為」這個承諾成立。**
  - `investigation.py` 只把 `RECENT_DAYS = 3` 改成指向領域層的同一個常數。
  - `SYSTEM_PROMPT` 的 sha256 在 origin/main 和 HEAD 都是 `5620ff3b…a70a8a`(我在兩棵樹各自實算過)。
  - 收據的逐日分段 file: `src/rtb/analyzer/investigation.py:273` 仍然排除 no_data 的天。這是 [S1408] 刻意保留的「提示與收據比程式規則寬鬆」,不是回歸。
  - `claims/*.json` 的新雜湊和實檔 sha256 一致;`tools/verify_claims.py claims` 回報 5 條宣稱、78 支證據測試全過。
- **測試會翻紅。** 我對 `nine_rules.py` 做了 14 個變異:10 個被測試抓紅(no_data、`<` 改 `<=`、`<=` 改 `<`、切點 `>` 改 `>=`、長窗缺值、break 改 continue、缺天數、分母零、逐日缺值、any 改 all)。存活的 4 個見發現 1 和發現 5。
- **72 筆回歸是真的逐筆比對。**
  - 每筆比 `ic.answer(case) is case.cell`,而 `case.cell` 是舊版生成器在入庫時用舊 `answer` 驗過的值,所以等於跟舊答案比。
  - 另外逐格比對寫死的答案表。
  - 既有的 `ic.generate() == investigation_set.CASES` 和 render 比對也同時綠。
- 其他檢查:ruff 與 mypy(改動檔)乾淨;`tests/domain`、`tests/eval`(相關檔)、`test_trust_boundary`、`test_verify_claims`、static checks 都過。合跑時有 7 個 `fixture 'store' not found`,單獨跑那兩支檔 42 筆全過,是指定多個路徑時 conftest 載入的問題,跟本次改動無關。

## 發現 1:歷史列缺時間、過去調整缺預算這兩支新分支沒有測試;歷史判定結果依列的順序而不同
severity: minor
blocking: 否

引句:「if row.committed_at is None or row.committed_at.tzinfo is None:」
引句:「if row.budget_before is None or row.budget_after is None:」

- **沒測試:** 把這兩支分支改成跳過(變異 M10、M12)後,`tests/domain/test_nine_rules.py` 和 `tests/eval/test_investigation_eval.py` 共 32 筆全綠。
  - 現在這兩個輸入過不了 DSP 白名單(file: `src/rtb/analyzer/dsp_client.py:231`、`src/rtb/analyzer/dsp_client.py:245`)。
  - 但計劃第 94 行明訂增量 2「無法證明調整前預算的舊列……須標為缺證據」,所以 `budget_before=None` 在下一增量就會是真實輸入,現在就沒有守衛。
- **依順序:** 同樣一組歷史列,排列不同結果就不同:

```
(rec, miss) -> cell=RECENT_BUDGET_CHANGE, reason=recent_budget_change
(miss, rec) -> cell=None, reason=missing_row_value, query=check_change_history
```

  verdict 都是證據不足,但格與診斷原因不同。增量 2 要把診斷細因落地時,同一筆資料會因 DSP 回的列序不同而分到不同的類別。
- **建議:**
  - 補兩個翻紅案例:歷史 `committed_at=None` 或沒時區;過去調整 `budget_before=None`。
  - 在領域筆記寫明歷史列是先命中還是先驗證完整。

## 發現 2:[S1403] 綁定的測試沒有驗到「收據捨入」那一半
severity: minor
blocking: 否

引句:「[S1403] 齊值分母零跳過,精確掉 50.1% 要命中,即使收據顯示捨入值。」

- 這支測試的輸入 `daily([(1000, 1000)] * 4, [(1000, 499)] * 3)` 實算出的收據是 `-50.1`,不是捨入到 `-50.0`。
- 所以「即使收據顯示捨入值」這句話在這個測試裡沒有被驗到:精確值和收據字串落在門檻的同一側。
- 真正驗到捨入陷阱的是舊測試裡掉 50.04% 的案例(收據顯示 -50.0、精確值 < -1/2)。
- [S1403] 條款例句「精確下降 50.1%……即使顯示收據捨入為 50%」也有同樣問題:一位小數的收據不會把 50.1 寫成 50。
- **建議:** 把測試輸入換成收據會捨到 -50.0 的精確下降,例如沿用 50.04% 那組,或修正條款例句。

## 發現 3:「標準答案不讀決策規則」的說法和守衛,在本增量後已經名不符實
severity: minor
blocking: 否

引句:「比照 Phase 10 的生成器:不讀決策規則與 AI 決策模組(測試掃匯入)」
引句:「from rtb.domain import nine_rules as rules」

- 模組說明、以及 `test_no_generated_case_sits_on_a_rounding_boundary` 裡的註解,仍然說生成器和標準答案「不讀決策規則」。
- 但標準答案現在整條由 `rtb.domain.nine_rules` 產生,而增量 2 的正式 `policy.code_rule` 也要改呼叫同一個模組。
- 掃描守衛(file: `tests/eval/test_investigation_eval.py:352`)只擋 `rtb.analyzer`,所以兩邊同源它仍然會綠,原本「標準答案獨立於待測規則」的用意已經被繞開。
- 計劃確實承認同源(36/36「不作品質證據」),所以這不是行為錯誤,而是模組說明與測試註解對不上。
- **建議:** 改寫這兩處說明,註明標準答案和正式規則同源,並指回計劃〈評估與報告〉的同源警語。

## 發現 4:表態「時間帶時區:na」可被反駁——決策時鐘本身沒驗時區
severity: minor
blocking: 否

引句:「def decide(worth: WorthInput | None, evidence: RuleEvidence, now: datetime) -> RuleDecision:」

- 歷史列有驗時區(沒時區的列判證據不足),但 `now` 沒有驗。重現:

```
decide(_worth(), history=[update_budget @ NOW-1d], datetime(2026,9,25))
-> TypeError can't compare offset-naive and offset-aware datetimes
```

- 本增量只有評估用帶時區的 `ic.NOW` 呼叫它,所以目前不會發生。
- 但這個模組做時間比較,「純同步計算、無外呼」不構成時區題 na 的理由。增量 2 接正式時鐘時,這裡會是例外而不是封閉的診斷原因。
- **建議:** `decide` 開頭對沒時區的 `now` 直接拒絕(或回 INPUT_INVALID),再補一條測試。
- 棧別其他題答 na 我同意:沒有 I/O、沒有 async、沒有外呼,也不做金額運算。Window 的 spend/revenue 只存不算。

## 發現 5:不合理值(負數)在逐日和過去調整兩邊處理不一致,且被標成「缺值」
severity: minor
blocking: 否

引句:「if change is not m.Reason.NO_DENOMINATOR and isinstance(change, m.Reason):」

- **過去調整:** `before_conversions=-1` 時 `exact_change` 回 INVALID_DATA,判證據不足,但 reason 標成 `missing_row_value`。領域層的封閉原因列舉裡沒有「不合理」這一類。
- **逐日:** 單列 `clicks=-5`(最近三天其中一天)時,分段先加總才算比率,負數被吸收,最後判 `DELIVERY_WITH_VALUE`、值得加(實跑確認)。
- 兩者都過不了現行 DSP 白名單(`is_count_or_none`),所以目前碰不到;變異 M13、M15 存活也反映這一點。
- 計劃第 90 行要求「評估的案例轉接也要經同一純核對函式」是增量 2 的事。在那之前,這個模組對不合理值沒有一致的語意。
- **建議:** 在領域筆記標明「不合理值由讀取層擋,領域層不保證」;或者加一個 `INVALID_ROW_VALUE` 原因,並逐列檢查負數。

## 圖譜鏡頭(固定席 8 篇,逐條判)

我自己跑了 `lumos impact --diff origin/main..HEAD`,dispatch 尾端沒有附筆記。

- **Systems/分析行程流程與檢查點(★INVARIANT★ F5、每步落地):不影響。**
  - `investigation.py` 只改常數的來源,值仍是 3;提示位元組與收據不變,`policy.py`、`flow.py`、`task_store.py`、`dsp_client.py` 都沒動。
  - F5 的 kill recipe 目標沒動;`test_trust_boundary` 全過。
- **Systems/正式九條判斷領域規則(新節點):大致成立,有兩處落差。**
  - 「三天切點與精確門檻不得因收據捨入而改動」這條 RULE 成立。
  - 正文「必要列內缺值……均回九格之外的證據不足」在歷史列缺時間和預算缺值上沒有測試(發現 1)。
  - TEST 欄列的 `test_exact_thresholds…` 沒驗到捨入(發現 2)。
- **Systems/評估與Jev決策點:成立。**
  - 「原 72 筆逐筆維持」經差分與回歸確認;「閉包名單只增純領域模組」與 `PHASE13_ALLOWED` 的 diff 一致。
  - 「不讀決策規則」的舊敘述已失準(發現 3)。
- **Systems/任務流程領域模型(★INVARIANT★ 領域層匯入白名單):不影響。**
  - `nine_rules.py` 只匯入 collections.abc、dataclasses、datetime、enum、fractions、types 和 rtb.domain;既有的白名單測試 `test_domain_layer_imports_only_an_allowlist_of_pure_standard_library_modules` 通過。
  - 提案解析、新鮮度、狀態機三條沒動。
- **Systems/確定性指標計算(★INVARIANT★ 算不出不變 0):不影響,而且有被遵守。**
  - 新模組只經 `exact_ratio`、`exact_change` 取值,用 isinstance 判 Fraction 或 Reason,沒有把 Reason 當成 0 或拿去比大小。
  - `metrics.py` 沒動。
- **Systems/Mock-DSP、Systems/執行迴圈、Systems/提案收件口(冪等、F1–F4、F6、F7、修訂序號):不影響。** 本增量沒碰 DSP、執行端、收件口的任何檔,也沒改正式送件路徑。

總結:最嚴重為 minor,共 5 條,blocking 0 條
