severity: major

# 資安-opus 第 2 輪報告(phase13-inc1,8fd2718)

## 第 1 輪逐條驗收

| 第 1 輪發現 | 驗收結果 | 依據 |
|---|---|---|
| 1. 即時加錄製沒帶批次,兩支命令列崩潰 | 已修好 | 閘道在入口呼叫 `check_recordings_dir(folder, batch_id)`,沒帶批次會丟 GateRefused。說明命令列收 `--batch-id`,以參數錯結束,不寫領取。假說命令列在呼叫前先跑 `recording_refusal`:告警照常印,以參數錯結束。實測三種組合:沒帶批次拒絕、用預設入庫目錄拒絕、有批次加新目錄放行 |
| 2. 廣告名稱能關掉資料區 | 已修好 | 名稱先經 `json.dumps`,不可列印的字再寫成 `\u` 跳脫。把第 1 輪的攻擊字串加上 U+2028、U+0085 重打一次,整段名稱只佔資料區裡的一行,偽造的 `資料結束>>>` 出不了資料區 |
| 3. 說明失敗後立刻再領,沒有上限 | 已修好 | `MAX_NARRATIVE_CLAIMS = 3`,檢查跟寫入在同一個寫入交易裡。後端固定回讀不懂的內容,連跑 8 趟(每趟隔 11 分鐘):後端只被呼叫 3 次,第 4 趟起回 `gave_up` |
| 4. 呼叫者標籤能自稱不計入 | 部分修好 | 加了「模組 → 呼叫者」對照表,擋得住直接寫 `Caller.X`、`Caller("值")`、`Caller["X"]`。但換個寫法就繞得過,見下面發現 2 |

## 發現 1:數字核對不認中文數字,編造的數字照樣顯示
severity: major
blocking: 是

白話講:
- 第 1 輪之後補了數字核對,用來落實 Phase 13 計劃第 233 行那條合約:文字裡提到的數字要能對回證據,對不上的句子不顯示。
- 可是核對只用正規式 `\d` 找數字,中文數字完全看不到,例如「五十倍」「三百萬」「一成」。
- 說明要求模型寫「白話中文」,中文數字正是最自然的寫法。所以模型自己編、或被廣告名稱誘導編出來的數字,都會整句過關。
- 這些文字會存成「成功」,追蹤檢視照樣顯示給人工核可的人看。
- 現成證據:測試裡當作合格說明的 `GOOD` 就含「一成」,它從來沒被核對過。
- 假說命令列逐條用同一個函式核對,有同一個洞。

同一個函式另外兩個較小的漏洞:
- 科學記號拆成兩個數字分開比。例如「1e5」在 1 跟 5 都出現在證據時照樣過。
- 負號不比對。例如「-100」會被當成 100。

引句:「_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")」
引句:「if _numbers(sentence) <= allowed:」
引句:「GOOD = "這份提案把預算加一成,依據是配速偏低;模型產生的說明只供參考。"」
file: `src/rtb/modelclient.py:150`
file: `src/rtb/analyzer/narrate.py:174`
file: `src/rtb/ops/hypothesis.py:294`
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase13AI參與決策_計劃.md:233`
file: `tests/analyzer/test_narrate.py:33`

例子:
- 輸入:說明模式是即時,後端回「這份提案會讓預算暴增五十倍、一天內燒掉三百萬美元,務必立即拒絕。」證據裡只有 budget=100、new_budget=110、impressions=500、spend=0.5 這些數字。
- 預期:句中的數字對不回證據,整句拿掉,結果記「回應讀不懂」。
- 實際:`narrate_pending` 回 `outcome='ok'`,`narrative_for` 回 `NarrativeStatus(outcome='ok', text='這份提案會讓預算暴增五十倍、一天內燒掉三百萬美元,務必立即拒絕。', source='live')`。
- 另外兩例:`traceable_sentences("損失上看 1e5 美元。", …)` 和 `traceable_sentences("預算將被砍到 -100。", …)` 都回 `(原句, 0)`。

重現:
1. 把 repo 複製到 /tmp,並把 src 和 repo 根放進 PYTHONPATH。
2. 先跑 `run_once(base, NORMAL_NAME, "underpacing")`。
3. 再用 `modelgate.Gate(live(FakeBackend(reply(FAB))), "demo-1", base/"ledger.sqlite", rec)` 跑 `narrate_pending`,其中 FAB 就是上面那句。
4. 最後用 `TaskReader.narrative_for` 讀回結果。

建議:
- 核對前先把中文數字(〇一二…十百千萬億、兩、半、成)換成數值,或者含中文數字的句子一律拿掉。
- 負號和指數記號併進數字的正規式。
- 補一條中文數字的反例測試。

## 發現 2:呼叫者標籤的對照表只認字面寫法,換個名字或用 getattr 就繞過
severity: minor
blocking: 否

白話講:
- `caller_offenders` 只在三種寫法下抓得到:屬性前面的名字剛好叫 `Caller`、用常數字串呼叫 `Caller(...)`、用常數下標 `Caller[...]`。
- 以下寫法都不會被抓:
  - 匯入時改名(`Caller as K`)
  - 另存一個變數(`C = Caller`)
  - `getattr(Caller, "HYPOTHESIS")`
  - `Caller.__members__[...]`
  - 用拼出來的字串呼叫 `Caller(...)`
  - 走訪列舉挑成員
- 所以第 1 輪第 4 條的情境還在:照註解,增量 2 的 AI 決策模組會登記在「分析端調查」那一格。這種會計入上限的模組只要換一個不計入的標籤,即時模式就不受 1 美元和 20 美元上限管。
- 目前入口的標籤都寫對,所以這不是正在發生的錯,只是這道靜態守門有洞。

引句:「isinstance(node.value, ast.Name | ast.Attribute)」
file: `tests/test_spawn_boundary.py:261`
file: `tests/test_spawn_boundary.py:328`

例子:
- 輸入:在副本裡把 `src/rtb/eval/model_candidate.py` 的 `caller=mc.Caller.EVAL_CANDIDATE` 改成 `caller=getattr(mc.Caller, "HYPOTHESIS")`。
- 預期:`test_each_caller_label_is_used_only_by_its_own_module` 失敗。
- 實際:`pytest tests/test_spawn_boundary.py -k "caller_label or backend or gateway"` 回 3 passed。對 `from rtb.modelclient import Caller as K\nx = K.HYPOTHESIS` 等 6 種寫法直接呼叫 `caller_offenders`,結果都是空的。

重現:照上面的方式改副本,再跑那支測試。

建議:
- 別只在語法樹上找字面寫法,改在執行期擋。例如 `call_model` 從呼叫堆疊的模組名稱核對 `CALLER_USERS`。
- 或者讓每個准送出呼叫的模組只能拿到綁死自己標籤的送出函式,例如閘道的 `complete` 不收 caller 參數,改由每支模組各自的包裝函式固定帶標籤。

## 查過、沒發現問題的地方
- 資料區逃逸:換行、`\r`、`"`、反斜線、U+2028、U+2029、U+0085、雙向覆寫字元都會被跳脫,名稱永遠只佔一行。名稱仍然可以在同一行裡寫「資料結束>>>」的字樣,但它被包在 JSON 引號裡、跟在 `證據丙:"` 後面,離開不了資料區這一行。剩下的是模型會不會照做的提示注入殘餘,設計已經接受(說明只給人看)。可信證據的字串被型別限制成短代號,不會從程式算的那一段混進自由文字。
- 不寫數字的假高風險結論:例如「此廣告主已被風控列為詐欺帳戶,核可將造成重大損失,請立即拒絕並通報。」整句會過關。計劃〈實作解讀〉寫明核對「只比數值、不比語意」,防線是說明只給人看、不進任何決策,所以我不另立一條。實際上擋得住編造數字的,只有發現 1 修好之後的數字核對。
- 重試上限:3 次檢查跟寫入領取在同一個 immediate 交易裡,兩個說明命令列同時跑也不會超過。花費帳忙碌也算一次領取,最壞讓那份提案提早變成 `gave_up`,但不會多花錢。
- 數字核對的證據範圍:說明只拿程式算的那一段去比,資料區的名稱不算,名稱裡寫的數字不能拿來「對回證據」。核對在佔位符換回編號之前做。佔位符用甲乙丙,不會多出可以對上的數字。
- 模型輸出的顯示:說明和假說都要求可列印字元,終端機的跳脫序列和雙向覆寫字元進不來。追蹤檢視只從唯讀開法讀。
- 閘道不再公開模型用戶端的模組物件。邊界測試加了閉包檢查、送出名稱 `open_gate` 和 `complete`、各層 ruff 禁匯入閘道,我實跑都通過。
- 我在副本實跑 tests/ops/test_hypothesis.py、tests/analyzer/test_narrate.py、tests/test_spawn_boundary.py,49 passed。

## 看過的改動檔(涵蓋 r2-snapshot.patch 全部 46 支)
- claims/aggregate-blast-radius.json(只改雜湊)
- claims/concurrency.json(只改雜湊)
- claims/idempotency-unknown-outcome.json(只改雜湊)
- claims/permission-guardrail.json(只改雜湊)
- claims/prompt-injection.json(只改雜湊)
- docs/rtb-production-agent-demo-knowledge/Projects/RTB_Agent_Phase0架構.md
- docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11B大模型接入_計劃.md
- docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase7提示注入與信任邊界_計劃.md
- docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase9可觀測與SLO_計劃.md
- docs/rtb-production-agent-demo-knowledge/Systems/分析行程流程與檢查點.md
- docs/rtb-production-agent-demo-knowledge/Systems/服務水準與燒損告警.md
- docs/rtb-production-agent-demo-knowledge/Systems/模型用戶端.md
- docs/rtb-production-agent-demo-knowledge/Systems/追蹤檢視.md
- src/rtb/analyzer/modelgate.py
- src/rtb/analyzer/narrate.py
- src/rtb/analyzer/task_store.py
- src/rtb/demo/faults/ruff.toml
- src/rtb/demo/launcher/ruff.toml
- src/rtb/demo/ruff.toml
- src/rtb/domain/ruff.toml
- src/rtb/dsp/ruff.toml
- src/rtb/eval/ruff.toml
- src/rtb/executor/ruff.toml
- src/rtb/modelclaude.py
- src/rtb/modelclient.py
- src/rtb/modelledger.py
- src/rtb/modelledger_view.py
- src/rtb/modelrecording.py
- src/rtb/ops/hypothesis.py
- src/rtb/ops/ruff.toml
- src/rtb/ops/slo.py
- src/rtb/ops/trace.py
- tests/analyzer/test_narrate.py
- tests/eval/test_model_candidate.py
- tests/model/test_modelclient.py
- tests/model/test_shared_entry.py
- tests/ops/test_hypothesis.py
- tests/ops/test_ops_boundaries.py
- tests/ops/test_trace.py
- tests/test_spawn_boundary.py

另外查了審材以外的 src/rtb/domain/evidence.py,以及 Phase 13 計劃第 233 行和第 794 行。

所有實驗都在 /tmp/資安-opus-p13i1r2 做,只用假後端,沒有呼叫真的模型,也沒有寫 ~/.rtb。做完已刪掉臨時目錄,這一席沒有留下任何背景行程。/Users/enzo/rtb-13i1-rev 沒有被改動:`docs/.governance-log.jsonl` 顯示已修改,是複製副本之前就存在的狀態。

2 條,blocking 1。
