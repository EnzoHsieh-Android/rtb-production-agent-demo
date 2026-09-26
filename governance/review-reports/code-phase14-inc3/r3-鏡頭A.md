severity: minor

# 代碼審 r3 鏡頭A(末輪:全部修正——分析端、展示、錄製驗收、測試)

審材:`governance/review-reports/code-phase14-inc3/r3-delta.patch`,13 個檔逐 hunk 讀完;需要時對照 r3-snapshot 與現行工作樹。

本機跑過的驗證:
- 相關子集:`tests/analyzer/test_instrumented.py`、`test_rule_round.py`、`test_f5_end_to_end.py`、`test_boundaries.py`、`test_runner.py`、`tests/model/test_shared_entry.py`、`tests/eval/test_investigation_eval.py`、`tests/test_spawn_boundary.py`、`tests/ops`。結果 363 passed。其中錄製重播照測試本身的做法帶暫存帳本,沒有寫 ~/.rtb。
- `ruff check src tests` 全過;`mypy src` 沒有問題。
- claims 五份的 scope sha256 與工作樹逐檔比對,0 筆不符。
- 工作樹除了 `docs/.governance-log.jsonl` 之外都乾淨,pyproject 已提交。

## 第 2 輪驗收(逐項)

- **鏡頭A-1、外家finder-1、資安-1(百分比換寫法就能繞過)**:已驗收。
  - 改成先做 NFKC 正規化,再認數字後面的 `%`、`٪`、percent、per cent、pct、(個)百分點,以及數字前面的「百分之」。千分號與萬分號一律整句拿掉。
  - r2 三席列出的寫法,本機逐一重跑都已整句拿掉:`﹪`、`٪`、全形數字、百分之500、percent、per cent、pct、個百分點、‰、‱。
  - 對得回的寫法照留,例如「百分之10」「10 個百分點」「10 percent」,不會一看到百分比就全刪。
  - 宣稱、計劃〈拆增量〉3 的註記③、Systems/模型用戶端 第 273 行的 PITFALL 三處,都改成「擋得住的就是這張列舉,沒列到的寫法仍只比數值」,不再講過頭。
  - 還有幾種沒列到的寫法照樣會過關:「500 趴」、`500​%`(中間夾零寬字元)、「500 百分比」、`㌫`。這些都落在宣稱自己寫明的「沒列到的寫法」之內,而且提案金額照舊是程式算的 110,所以不另立發現。唯一例外見下面的發現 1。
- **鏡頭A-2(dsp_evidence_source 成了死碼)**:已驗收。
  - `dsp_evidence_source` 與 `InstrumentedEvidenceSource` 兩支都刪了,src 與 tests 裡沒有任何殘留的呼叫端。
  - test_rule_round 改用 `dsp_client.make_client` 自己造舊版兩讀的列。
  - `dsp_client.py` 的說明改成指向 `rule_source`,Systems/分析行程流程與檢查點 第 62 行的 RULE 也改寫成現行的 `rule_source`。
  - [S50] 的三支測試改走 `rule_source` 的 A 步,斷言照實改成兩個端點各記一筆。`DSP_EVIDENCE` 列舉成員留著讀舊列,註解有講明。
- **鏡頭A-3(展示頁面摘要)**:已驗收。第 20 行的 WHY 已改成「照展示狀態記的 `decided_by` 寫」,和同一篇的 PITFALL 一致。
- **r1 鏡頭1-2 提醒的 pyproject 未提交**:已驗收。這次改的是 ruff 訊息「只准評估執行器用」,檔案已經提交,工作樹沒有差異。

## 攻擊正式路徑:有沒有模型輸出能影響要不要提案

結論:**還是找不到。** 本輪的增量只動到 instrumented、modelclient、task_store 的註解、dsp_client 的說明、claims 與測試,沒有碰分析端的決策路徑。
- `runner.py` 匯入的只有 dsp_client、flow、inbox_client、instrumented、policy、rule_round。flow 不匯入 investigation 或 ai_judge。
- `investigation.py` 只是詞彙模組,不匯入 modelgate。
- 全 src 只有 `rtb/eval/investigation_eval.py` 匯入 ai_judge。評估的 Judge 重播照樣能跑,test_investigation_eval 全綠。
- instrumented 刪掉的兩支包裝只負責記錄呼叫紀錄,和決策無關。刪掉後,證據來源只剩規則輪的 `rule_source`。
- 說明核對用的證據(`trusted`)不含廣告名稱(`src/rtb/analyzer/narrate.py:138`)。所以就算名稱裡寫「500%」,也不會把 500 變成「證據裡的百分比」。
- 九條規則路徑完好:F5 端到端測試綠,結論、讀取次數與金額 110 都和正常名稱那次相同。

## 發現 1:宣稱寫「認 percent」,但英文最常見的形容詞寫法「500-percent」照樣借曝光數 500 過關
severity: minor
blocking: 否

引句:「再認數字後面的 % 或阿拉伯百分號 U+066A、percent/per cent(含 percentage)、pct、」

`_PERCENT_AFTER` 要求數字後面只能隔空白就接 percent。可是英文寫「a 500-percent increase」時,中間是連字號;「500 pcts」這種複數寫法也一樣認不出來。這兩種都會退回只比數值,再借 F5 的曝光數 500 過關。

宣稱 policy 明寫「認 … percent/per cent/pct」。讀的人會以為所有 percent 的寫法都擋得住,但最常見的形容詞寫法擋不住,這是一處內部不一致。

不影響要不要提案,也不影響金額(照舊 110),所以列 minor。

重現:
```
PYTHONPATH=src .venv/bin/python -c "
from rtb import modelclient as mc
ev='- 證據甲 metrics:impressions=500、clicks=12\n- 風險說明(程式固定文字):budget +10%'
for s in ['加 500 percent。','加 500-percent。','加 500 per-cent。','加 500pcts。']: print(repr(s), mc.traceable_sentences(s, ev))"
```
輸出:
```
'加 500 percent。' ('', 1)
'加 500-percent。' ('加 500-percent。', 0)
'加 500 per-cent。' ('加 500 per-cent。', 0)
'加 500pcts。' ('加 500pcts。', 0)
```

修法方向:把分隔字元從 `\s*` 放寬成 `[\s\-‐-―]*`,並把 `pct(?![a-z])` 改成 `pcts?(?![a-z])`。另一條路是維持現狀,在宣稱裡加註「連字號與複數寫法不認」。

file: `src/rtb/modelclient.py:239`

## 圖譜鏡頭:固定席逐條判

派工尾端沒有附筆記,以下是我自己跑 `lumos impact --diff origin/main..HEAD` 取得的固定席 14 篇。

- **Mock-DSP ★INVARIANT★**(冪等鍵只套用一次、版本不符拒收、全有或全無):不影響。本輪只有測試夾具會起模擬 DSP 做 GET 讀取,沒有寫入路徑。
- **一鍵展示**:不影響。本輪沒碰 demo 的程式碼,r2 已驗收的 F3/F4/F6/F7 行為照舊。
- **任務流程領域模型 ★INVARIANT★**:不影響。task_store 只改了一行列舉註解,狀態轉換與圍籬都沒動。
- **分析行程流程與檢查點 ★INVARIANT★**:不影響。刪掉的兩支包裝不在 `flow.advance()` 的正式接線上。第 62 行的 RULE「client 不碰 TaskStore、由 instrumented 負責記錄」仍然成立,措辭也已改到現行的 `rule_source`。
- **展示頁面**:不影響。摘要矛盾已修,本輪沒改頁面程式。
- **模型用戶端**:行為變更就在這裡。「數字對不回就整句拿掉」的合約只會變嚴,不會放寬:多認的百分比寫法都是拿掉的方向,千分號、萬分號一律拿掉。PITFALL 照實寫了列舉範圍,和程式一致;唯一的小落差見發現 1。
- **正式九條判斷領域規則**:不影響。nine_rules 與 rule_round 都沒動,F5 測試綠。
- **評估與Jev決策點**:不影響。ai_judge 只剩評估在匯入,這支 ruff 訊息改成「只准評估執行器用」,和實際匯入一致。重播測試綠。
- **服務水準與燒損告警**:假說也共用 `traceable_sentences`,新寫法只會多拿掉,不會多放行。所以「數字對不回就不顯示」的合約不受破壞。
- **追蹤檢視**:不影響。本輪沒碰 ops。
- **共用行程基礎 ★INVARIANT★**:不影響。`dsp_client.make_client` 的 `on_call` 逐端點記錄照舊,本輪只改了說明文字。
- **執行迴圈 ★INVARIANT★**、**提案收件口 ★INVARIANT★**:不影響。執行端與收件口都沒動。
- **確定性指標計算 ★INVARIANT★**:不影響。metrics 沒動。

表態紀錄:「百分比核對擋得住的就是這張列舉」我當成可反駁的宣稱去驗。列舉內的寫法全數擋下,推翻不了;但 percent 一項沒有涵蓋連字號與複數寫法,見發現 1。

## 總結
第 2 輪三條發現與 pyproject 提交都已修好並驗收。本輪沒有新增任何通往提案的模型入口;九條規則路徑、評估重播、暫存帳本相關測試全綠。只剩一條不擋推送的小落差:「500-percent」這類連字號寫法仍會過關。
