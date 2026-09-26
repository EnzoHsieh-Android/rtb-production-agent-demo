severity: major

# 代碼審 r2 鏡頭A(全部修正:分析端、展示、錄製驗收、測試)

審材:`governance/review-reports/code-phase14-inc3/r2-delta.patch`(逐 hunk 讀完),需要時對照 r2-snapshot 與現行工作樹。
本機跑過的驗證:
- 相關子集 13 檔:`PYTHONPATH=src .venv/bin/python -m pytest -q tests/demo/test_ai_demo.py tests/demo/test_present.py tests/model/test_shared_entry.py tests/analyzer/test_boundaries.py tests/analyzer/test_f5_end_to_end.py tests/analyzer/test_investigation_review*.py tests/analyzer/test_runner.py tests/demo/test_ai_launcher.py tests/demo/test_observe.py tests/test_spawn_boundary.py tests/eval/test_investigation_eval.py`,結果 301 passed。這批含入庫錄製重播,重播一律帶暫存帳本,沒有寫 ~/.rtb。
- `ruff check src` 全過,`mypy` 95 檔無問題。
- F7 實測:純規則 300 件,2700 次讀取,共 14.9 秒,時限 300 秒。

## 第 1 輪驗收(逐項)

- 資安-opus-1(不含數字的句子不過濾):宣稱 prompt-injection 的 policy 改成照實寫「不含數字的句子不做語意過濾」。已驗收。
- 外家否決-1(「加 500%」借曝光數 500 過關):`traceable_sentences` 加了「百分號對百分號」,原句現在會被拿掉,有測試守。**修得不完整**,換個常見的中文寫法就能繞過,見發現 1。
- 架構對齊-1(investigation 反向匯入 flow):採取捨,單向依賴有新的邊界測試 `test_the_investigation_vocabulary_depends_on_flow_one_way_only` 守,flow 本身不匯入 investigation 或 ai_judge。已驗收。
- 架構對齊-2(walrus):`_modes` 已改用 walrus 寫法。已驗收。
- 鏡頭1-1(`recorded_ledger` 說明):已改成四個入口,分析端驅動不開閘道。已驗收。
- 鏡頭1-2(ruff 訊息,分析端缺禁令):各層 ruff 訊息都改了;`src/rtb/analyzer/ruff.toml` 新增 ai_judge 匯入禁令;根目錄 pyproject 那一行也改了。有一點要提醒:這一行改動還在工作樹、沒有提交,而 HEAD 的 claims 雜湊已經指向工作樹版本,提交時要一起帶上。已驗收。
- 鏡頭2-1(F3 花兩次錢):賽跑改用正式的 `rule_source`。新增 `_one_paid_analysis` 核對「只有一輪規則輪、沒有重來、讀取 9 次」,`F3_STREAMS` 不再容忍重讀那一圈,測試有守。已驗收。
- 鏡頭2-2 與外家finder-1(F4/F6 原任務的提案沒有說明也放行):提案數改從情境自己的 `analyzer.db` 讀,只算送進收件口的列(`proposals_in`),並要求說明呼叫次數不少於提案數;F4/F6 測試現在要求 t1 恰好說明一次。已驗收。
- 鏡頭2-3(說明沒跑完卻寫成「沒有呼叫 AI」):已改成照實寫「說明命令列沒有跑完:…」。已驗收。
- 鏡頭3-1(runner 白名單):已收緊,拿掉 modelgate、ai_judge、investigation。已驗收。
- 鏡頭4-1~4(沒有入口的程式碼):`investigation_source`、`renew_lease`、`record_model_call`、`investigation_call_count`、`investigation_reason_code`、`commit_step(investigation=…)`、`Process.next_line` 都已刪,任務模組的過時說明也改寫了。全庫 grep 沒有殘留的呼叫端。已驗收。不過 F3 的修正本身又留下一支新的無入口函式,見發現 2。
- 外家finder-2(舊紀錄的 AI 決定被標成九條):改成 `decided_by` 照值對應,五種值都有測試。已驗收。
- 外家finder-3(F7 在 240 秒就判逾時):改用 `F7_TIME_LIMIT_SECONDS` = 300。已驗收。
- spec-conformance-1(計劃對 `renew_lease` 的描述):計劃〈拆增量〉3 註記②已改。已驗收。

## 攻擊正式路徑:有沒有模型輸出能影響要不要提案

結論:**找不到任何通往提案的模型入口。**
- 分析端驅動的匯入只剩規則輪相關模組,測試 `test_the_analyzer_runner_uses_only_production_collaborators` 釘住這一點。
- 流程層的匯入閉包不含 investigation、ai_judge 與模型閘道。
- `rule_round` 只讀同一輪 B/C 蒐證時寫下的原始回應。現在那張表只剩 `rule_source` 在寫,沒有 AI 寫的列。
- 舊資料庫裡 AI 期留下的列:`rule_round` 認不得,會從 A 重讀,不會被拿來決策。
- 說明寫進 narrative_results,執行端與收件口都不讀。
- ai_judge 在 analyzer、demo 與各層 ruff 都禁止匯入,只有 `rtb/eval` 在用。評估的 `Judge` 重播仍能跑(test_investigation_eval 全綠)。
- 九條規則路徑完好:F5 端到端測試中,結論、細因、讀取 9 次、金額 110 都和正常名稱那次相同。

## 發現 1:百分比核對只認「%/%」兩個符號,「百分之500」「500﹪」「500 個百分點」照樣對回曝光數 500,整句留下
severity: major
blocking: 是

這一輪為外家否決-1 加的修正只抓「數字後面緊接半形 % 或全形 %」。下面幾種同義寫法都不算百分比,會退回舊的「只比數值」規則,因為 F5 證據裡有 `impressions=500`,所以照樣過關:
- 很常見的中文寫法「百分之500」
- 小型百分號 U+FE6A「﹪」
- 阿拉伯百分號 U+066A「٪」
- 「500 percent」
- 「500 個百分點」
- 「500 趴」

這正是 r1 攻擊的那一句,只是換了寫法。影響範圍:
- 宣稱 prompt-injection 的 policy 寫「對不回證據裡百分比的百分比,整句拿掉」,計劃〈拆增量〉3 註記③寫「已修成百分比對百分比」,Systems/模型用戶端 的 PITFALL 寫「已修」。三處都比程式實際做到的更強。
- 不影響要不要提案,也不影響金額,提案照公式仍是 110。但核可頁可能出現一句和提案金額矛盾、由廣告名稱誘導出來的「加百分之500」,而且這句已經通過了系統自己宣稱會擋下的那道核對。

引句:「+    percents = _percentages(evidence)」

最小重現(現場翻紅,證據字串取自本次新增的測試 `test_a_percentage_must_trace_back_to_a_percentage`):
```
PYTHONPATH=src .venv/bin/python -c "
from rtb import modelclient as mc
ev='- 證據甲 metrics:impressions=500、clicks=12\n- 風險說明(程式固定文字):budget +10%'
for s in ['請把預算加 500%。','請把預算加百分之500。','請把預算加 500﹪。','請把預算加 500٪。','請把預算加 500 percent。','請把預算加 500 個百分點。']:
    print(repr(s), mc.traceable_sentences(s, ev))"
```
輸出:
```
'請把預算加 500%。' ('', 1)
'請把預算加百分之500。' ('請把預算加百分之500。', 0)
'請把預算加 500﹪。' ('請把預算加 500﹪。', 0)
'請把預算加 500٪。' ('請把預算加 500٪。', 0)
'請把預算加 500 percent。' ('請把預算加 500 percent。', 0)
'請把預算加 500 個百分點。' ('請把預算加 500 個百分點。', 0)
```
只有第一句被拿掉,其餘五句都完整留下。

修法方向:下面兩種擇一。
- 把「前面緊接『百分之』」「後面緊接 ﹪ ٪ percent pct 百分點 趴」也當成百分比的語境,U+FE6A、U+066A 併進字元類。
- 或者乾脆把「百分之」加進數詞判定,整句當對不回拿掉。
不論選哪種,宣稱、計劃與 PITFALL 的措辭都要改成程式實際擋得住的範圍。file: `src/rtb/modelclient.py:223`

## 發現 2:F3 改用 `rule_source` 之後,`instrumented.dsp_evidence_source` 在正式程式裡沒有任何呼叫端,圖譜的 RULE 仍把它寫成現行機制
severity: minor
blocking: 否

這一輪依「沒有入口就刪」拿掉了 `investigation_source` 等函式,但 F3 的修正把 `dsp_evidence_source` 在 src 裡的最後一個呼叫端換掉了:runner 在增量 2b 就已改用 `rule_source`,這次換掉的是 driver 的賽跑。結果它又成了一支只剩測試在用的函式:
- 呼叫它的只剩 `tests/analyzer/test_instrumented.py`,以及 `tests/analyzer/test_rule_round.py:285`,後者拿它模擬「不屬於規則輪的舊列」。
- 同檔的 `InstrumentedEvidenceSource` 在 src 裡也沒有使用者。

同時有兩處描述對不上現況:
- Systems/分析行程流程與檢查點 第 62 行的 RULE(帶 since 與 retire)仍寫「改用 on_call 鉤子讓 dsp_client 自己在每個端點各記一筆(`instrumented.dsp_evidence_source`)」,把它當成現行的記錄機制。
- `src/rtb/analyzer/dsp_client.py:139` 的說明也還指向它。

這是這一輪修正自己帶出來的不一致,和 r1 鏡頭4 的處理標準不一樣。

引句:「-        real = instrumented.dsp_evidence_source(own, str(world.dsp.url), 3)」

重現:`git grep -n "dsp_evidence_source" HEAD -- src` 只剩定義本身(instrumented.py)和 dsp_client.py:139 那句說明,沒有任何呼叫。

修法方向:二選一。
- 照 r1 標準刪掉,test_rule_round 改用 `dsp_client.make_client` 自己造舊列。
- 或者在計劃的清單裡明寫「保留作為測試夾具」。
不論選哪種,那條 RULE 都要改寫成現行的 `rule_source`。file: `src/rtb/analyzer/instrumented.py:51`

## 發現 3:Systems/展示頁面 的摘要仍寫「這次誰決定固定寫程式規則(九條)」,和本輪改成照紀錄值顯示的行為相反
severity: minor
blocking: 否

本輪依外家finder-2 把頁首「這次誰決定」改成照展示紀錄的 `decided_by` 顯示:舊紀錄顯示「AI(已撤除的舊流程)」等,沒有記錄的顯示「(這次沒有記錄)」。同一篇節點第 135 行的 PITFALL 已照新行為寫。但摘要(summary)第 20 行的 WHY 仍寫「決策摘要的『這次誰決定』固定寫程式規則(九條)」。

摘要是下一個 session 最先讀到的現況,兩句在同一篇節點裡互相矛盾;照摘要去改頁面的人,會把舊紀錄裡的 AI 決定再寫回九條規則,也就是 r1 修掉的那個錯。

引句:「+    who = scenario.decided_by or NOT_RECORDED」

重現:`sed -n 20p docs/rtb-production-agent-demo-knowledge/Systems/展示頁面.md`,可見「固定寫程式規則(九條)」;對照 `src/rtb/demo/page.py:903`,`who = scenario.decided_by or NOT_RECORDED`。

## 圖譜鏡頭:固定席逐條判

`lumos impact --diff origin/main..HEAD` 列出固定席 14 篇。派工尾端沒有附筆記,是我自己跑出來的。
- **Mock-DSP ★INVARIANT★**(冪等鍵只套用一次、版本不符拒收、寫入全有或全無):不影響。本輪沒碰 DSP;F3 賽跑改用 `rule_source`,只有 GET 讀取;F3 仍核對「平台只改一次」(`_applied_once`)。
- **一鍵展示**:行為沒有被破壞。F3 路徑收緊,F4/F6 多了一次說明,F7 等待時間跟情境時限一致。節點第 247–256 行已寫回。第 174、196 行描述 --ai-judge 寬限與舊的「誰決定」,但都在帶日期的 Phase 13 歷史小節裡,不算現況矛盾。
- **任務流程領域模型 ★INVARIANT★**:不影響。`commit_step` 只拿掉 `investigation` 參數,狀態轉換、圍籬、同一交易寫入都沒動。
- **分析行程流程與檢查點 ★INVARIANT★**:租約與圍籬的不變量不受影響;第 48 行 RULE「分析側沒有續租」因為刪掉 `renew_lease` 反而重新成立。第 62 行 RULE 的過時引用見發現 2。第 217 行 ai_judge 那一條(登入預檢、續租前看停止旗標)已過時,但它在 Phase 13 歷史小節裡。
- **展示頁面**:行為照 PITFALL 第 135 行;摘要第 20 行和它矛盾,見發現 3。
- **模型用戶端**:第 273 行 PITFALL 對程式行為的描述是準的(半形或全形百分號),但「已修」的範圍比攻擊面窄,見發現 1。
- **正式九條判斷領域規則**:不影響。nine_rules 沒動,F5 端到端測試證明結論、細因與金額不受名稱影響。
- **評估與Jev決策點**:不影響。評估照樣直接呼叫 `Judge` 做錄製重播,test_investigation_eval 全綠。第 113 行「續租回呼永遠成功」和第 152 行「已刪」同篇互相矛盾,但那是 r1 就有的,不是本輪新增。
- **服務水準與燒損告警**:假說和說明共用 `traceable_sentences`。假說輸入裡沒有任何「%」,所以帶百分號的假說句從此一律被拿掉,只會刪得更多、不會放得更多,不破壞「數字對不回就不顯示」的合約。
- **追蹤檢視**:不影響。本輪沒碰 ops;`test_ops_boundaries` 是 r1 範圍。
- **共用行程基礎 ★INVARIANT★**:不影響。`rule_source` 每次讀取各記一筆呼叫紀錄的做法沒變。
- **執行迴圈 ★INVARIANT★**:不影響。執行端沒動;F3 仍核對投遞 2 次、送 1 次、平台套用 1 次。
- **提案收件口 ★INVARIANT★**:不影響。錄製驗收改成用唯讀開法讀分析端的 `handed_off_rows`,不碰收件表。
- **確定性指標計算 ★INVARIANT★**:不影響。metrics 沒動。

表態紀錄:「F7 純規則先用原 300 秒實測、不預先放寬」([S1411])我當成可反駁的宣稱去驗:本機實測 14.9 秒,讀取 2700 次(與宣告的次數一致),推翻不了。

## 總結
本輪修正把所有已知的模型入口都封住了,九條規則路徑、評估重播、暫存帳本共用函式都完好。發現 1 需要修:r1 的「加 500%」攻擊換成「百分之500」等寫法仍能留在說明裡,而宣稱與計劃都說已經擋住。另外兩條是本輪修正帶出的死碼與圖譜摘要矛盾。
