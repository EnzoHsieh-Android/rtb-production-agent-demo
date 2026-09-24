severity: major

# 資安-opus 第 3 輪報告(phase13-inc1,baac4c5)

## 第 2 輪逐條驗收

| 第 2 輪發現 | 驗收結果 | 依據 |
|---|---|---|
| 1. 數字核對不認中文數字,編造的數字照樣顯示 | 點名的三個洞都修好了,但同一條合約還有別的繞法 | 下面這些寫法現在都整句拿掉:「五十倍」「三百萬」、簡體的「万」「亿」「两」、「廿」「卅」「兆」、`1e5`、`5E+5`、`-100`。可是數字中間夾空白、撇號或底線,就能把一個編造的大數拆成幾個證據裡本來就有的小數,整句照樣過,見發現 1 |
| 2. 呼叫者標籤的對照表只認字面寫法 | 部分修好 | 閘道在開閘時就綁死呼叫者,送出時不再收;建請求、開閘道時帶的呼叫者,必須是字面的 `Caller.成員`。第 2 輪那 6 種寫法,現在直接寫在 `ModelRequest(caller=…)` 裡都抓得到。但建好之後再換標籤就抓不到,見發現 2 |

## 發現 1:數字中間夾空白、撇號或底線,編造的大數就能拆成證據裡的小數過關
severity: major
blocking: 是

白話講:
- 合約是:文字裡提到的數字要能對回證據,對不上的句子不顯示(Phase 13 計劃第 233 行)。
- 抓數字的正規式只認三種寫法:連續數字、半形逗號分千位、小數點。
- 空白、撇號、底線都會把數字切斷。例如「1 100 500」被當成 1、100、500 三個數,不是 1100500。
- 說明的證據段裡本來就有 1、10、12、100、110、500 這些數。所以模型只要照這種分組寫法編一個大數,每一組都對得上,整句就存成「成功」,追蹤檢視會顯示給人工核可的人看。
- 這幾種分組寫法都是可列印字元,擋不可列印字元的那道檢查擋不到:
  - 一般空白(國際上常見的千位分隔寫法)
  - 撇號(瑞士寫法)
  - 底線(程式寫法)
  - 全形數字配空白也一樣
- 假說命令列逐條用同一個函式核對;它的證據裡數字更多,更容易湊。
- 附帶一個同源的小洞:
  - `500k`、`500M` 只比 500。「k」「M」直接改了數量級,不只是單位。
  - 「—100」(長破折號)、「‐100」(U+2010 連字號)不會被當成負數。

引句:「_NUMBER = re.compile(r"(?:(?<![0-9A-Za-z])[-\u2212\uff0d])?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")」
引句:「if not _unreadable_numerals(sentence) and _numbers(sentence) <= allowed:」
file: `src/rtb/modelclient.py:152`
file: `src/rtb/modelclient.py:191`
file: `src/rtb/analyzer/narrate.py:175`
file: `src/rtb/ops/hypothesis.py:295`

例子:
- 證據段:用 `run_once(base, NORMAL_NAME, "underpacing")` 產生。`prompt_for` 回的程式算那一段,抓出的數字是 {0.5, 1, 5.0, 10, 12, 100, 110, 500}。
- 輸入:即時模式,假後端回「這份提案若核可,當天會燒掉 1 100 500 美元,務必立即拒絕。」
- 預期:1100500 對不回證據,整句拿掉,結果記「回應讀不懂」。
- 實際:`narrative_for` 回 `NarrativeStatus(outcome='ok', text='這份提案若核可,當天會燒掉 1 100 500 美元,務必立即拒絕。', source='live')`。
- 同一個流程換成下面三種寫法,也都回 `outcome='ok'`:
  - `1'100'500`
  - 全形的 `１ １００ ５００`
  - `500k`
- 直接呼叫 `traceable_sentences("損失 1_100_500。", 同一段證據)`,拿掉的句數是 0。
- 對照組:`損失 12,500 元。`、`損失 1,100 元。` 都有被拿掉,可見只有逗號分組會被併成一個數。

重現:
1. 把 repo 複製到 /tmp,PYTHONPATH 設成 `src:.`。
2. 跑 `run_once(base, NORMAL_NAME, "underpacing")`。
3. 用 `modelgate.Gate(live(FakeBackend(reply(FAB))), modelgate.Caller.NARRATIVE, "demo-1", base/"ledger.sqlite", rec)` 跑 `narrate_pending`,FAB 就是上面那句。
4. 用 `TaskReader.narrative_for` 讀回結果。

注意:句子裡不能有「一」。「一天」「一旦」會被當成中文數字,整句先被拿掉。

建議:
- 兩個數字中間只隔空白、撇號、底線、U+202F 這類分組字元時,把它們當成同一個數再比對。
- 或者更簡單:只要看到「數字、分組字元、數字」的樣式,整句就當對不回。
- 數字後面緊接 `k/K/M/B` 也照這樣處理。
- 補上 `1 100 500`、`1'100'500` 的反例測試。

## 發現 2:呼叫者綁在入口之後,還能用 replace、參數展開或 __setattr__ 換掉
severity: minor
blocking: 否

白話講:
- 這輪的修法分兩半:
  - 靜態檢查:只看 `open_gate`、`ModelRequest`、`Gate` 這三個名字的呼叫,檢查 `caller=` 那一格。
  - 執行期:不核對是哪支模組送出的。
- 以下寫法三道靜態檢查(`caller_offenders`、`caller_argument_offenders`、`backend_offenders`)都抓不到:
  - 先照規矩建好請求或閘道,再用 `dataclasses.replace(r, caller=getattr(mc.Caller, "HYPOTHESIS"))` 換掉。
  - `ModelRequest(**{"caller": …})`,`caller` 不在逐個列出的關鍵字裡。
  - `Gate(**dict(caller=…))`。
  - `object.__setattr__(r, "caller", …)`。
  - `list(mc.Caller)[1]`。
- 會計入上限的只有「評估候選」和「即時實測」兩個呼叫者,其他三個不計入。所以評估候選那支模組照上面的寫法換成「維運假說」,1 美元和 20 美元上限就管不到它。
- 目前各入口的標籤都寫對了,所以這不是正在發生的錯,只是守門還有洞。

引句:「CALLER_TAKERS = frozenset({"open_gate", "ModelRequest", "Gate"})  # 收呼叫者標籤的建構與開閘道」
引句:「CAPPED_CALLERS: frozenset[Caller] = frozenset({Caller.EVAL_CANDIDATE, Caller.VERIFICATION})」
file: `tests/test_spawn_boundary.py:284`
file: `src/rtb/modelledger.py:197`
file: `src/rtb/eval/model_candidate.py:173`

例子:
- 輸入:把「先照規矩建 `mc.ModelRequest(caller=mc.Caller.EVAL_CANDIDATE, …)`,再 `dataclasses.replace(r, caller=getattr(mc.Caller, 'HYPOTHESIS'))`,再 `mc.call_model(r, …)`」當成 `rtb.eval.model_candidate` 的程式碼。
- 預期:三道檢查至少一道回報。
- 實際:三道都回 `[]`。
  - 參數展開、`object.__setattr__`、`list(mc.Caller)[1]` 三種寫法也都是 `[]`。
  - 以 `rtb.analyzer.narrate` 身分跑 `modelgate.Gate(**dict(..., caller=getattr(modelgate.Caller, 'INVESTIGATION'), ...))`,同樣是 `[]`。
  - 只有 `ModelRequest(*args)` 這種位置參數展開抓得到。

重現:在副本裡從 `tests.test_spawn_boundary` 匯入這三個函式,對上面的程式碼 `ast.parse` 之後直接呼叫。

建議:
- 在 `call_model` 裡依呼叫堆疊的模組名稱,核對 `CALLER_USERS`。
- 或者讓 `ModelRequest` 的 caller 只能由各模組專屬的建構函式產生,不公開 `caller` 欄位的寫入。

## 發現 3:最上層模組用同層相對匯入轉手說明或假說命令列,ruff 和邊界測試都不抓
severity: minor
blocking: 否

白話講:
- 這輪把 `rtb.analyzer.narrate`、`rtb.ops.hypothesis` 加進 7 個 ruff 設定的禁令表:demo、demo/launcher、demo/faults、domain、dsp、eval、executor。實測這幾層寫絕對匯入或上一層的相對匯入都會被擋(TID251 或 TID252)。
- 維運層靠 `test_ops_boundaries` 擋,實測抓得到。分析端靠匯入閉包測試擋。
- 最上層的共用模組(`src/rtb/*.py`,例如 httpkit、sqlitekit)吃的是 pyproject 那張禁令表,那張表只禁 `rtb.demo`。
- AST 那道檢查只拿 `node.module` 原字串比對,不處理相對匯入的層數。`from .analyzer import narrate` 的 module 是 `analyzer`,拼出來是 `analyzer.narrate`,不在 `SENDING_ENTRIES` 裡。
- 所以最上層任何一支檔都能寫 `from .analyzer import narrate` 再呼叫 `narrate.run(...)`,而 `.run` 不在送出名稱清單裡。
- 更糟的是,各層本來就准匯入這些共用模組。例如 dsp 經 `httpkit.narrate.run(...)` 就能轉手送出。這條在 dsp 的禁令表上看不出來。

引句:「entries = {f"{module_name}.{n}" for n in names} | {module_name}」
file: `tests/test_spawn_boundary.py:417`
file: `pyproject.toml:19`

例子:
- 輸入:在副本新增 `src/rtb/probe_top.py`,內容是 `from .analyzer import narrate`,加上一個呼叫 `narrate.run(["--db", "x"])` 的函式。
- 預期:ruff 或邊界測試失敗。
- 實際:`ruff check src/` 回 All checks passed。整套測試(扣掉 test_live_guards)只有 1 支失敗:`test_running_the_hash_helper_as_a_script_says_how_to_run_it`。拿掉探針後它照樣失敗,跟探針無關。
- 對照組:同樣手法放在 `src/rtb/ops/` 底下,會被 `test_the_ops_package_is_read_only_and_imported_by_nobody` 抓到。

重現:照上面在副本新增那支檔,再跑 `ruff check src/` 與 `pytest tests`。

建議:
- `backend_offenders` 改用 `tests/analyzer/test_boundaries.py` 的 `_resolve_from`,先把相對匯入接成完整模組名再比對。
- 或者在 pyproject 的禁令表也列上 modelgate、narrate、hypothesis,並加上 TID252 的同層相對匯入限制。

## 查過、沒發現問題的地方
- 全形數字:`１１０` 會被當成 110,對得上;`９９９` 對不上,整句拿掉。
- 其他十進位數字:數學粗體數字、阿拉伯-印度數字都照數值比,對不上就拿掉。
- 上標、圈數字、分數、`0x1F4` 都會被拿掉。
- 中文與大寫數字:含它們的句子一律拿掉,連同簡體與異體字。實測「两」「倆」「仨」「廿」「卅」「兆」「万」「亿」都拿掉了。
- 系統提示也補了一句,要求一律用阿拉伯數字。
- 呼叫者綁在閘道:
  - `Gate.complete` 已經不收 caller。
  - `open_gate` 對非列舉成員丟 GateRefused。
  - 說明命令列開閘時帶的是 `Caller.NARRATIVE`。
- 領取上限的例外:不計入上限的四種結果類別(帳忙、上限拒絕、設定錯誤、沒有錄製)都保證沒有呼叫模型、花費 0,設定錯誤的類別說明也寫明「確定還沒呼叫模型」。只能讓一直失敗的提案一直重領,不會多花錢。
- 錄製目錄檢查:
  - 改用 lstat,只有「不存在」放行,符號連結、非目錄、讀不到都拒絕。
  - 目錄檢查和讀取改用同一套 `validated`,讀不回來的檔在開錄前就會被拒絕。
- 假說命令列:入口檢查移到呼叫前只做一次,呼叫之後不再重判。附帶一個觀察:沒有告警又撞上即時加錄製檢查時,結束代碼是參數錯,卻沒印原因。這只是介面問題,不另立一條。
- 我在副本實跑 tests/test_spawn_boundary.py、tests/ops/test_ops_boundaries.py、tests/analyzer/test_boundaries.py、tests/ops/test_hypothesis.py、tests/model:170 passed、1 skipped。

## 看過的改動檔(涵蓋 r3-snapshot.patch 全部 39 支)
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
- tests/model/test_live_guards.py
- tests/model/test_modelclient.py
- tests/model/test_shared_entry.py
- tests/ops/test_hypothesis.py
- tests/ops/test_ops_boundaries.py
- tests/test_spawn_boundary.py

審材以外另外查了這些:
- src/rtb/analyzer/ruff.toml
- pyproject.toml 的禁令表
- src/rtb/modelcore.py 的結果類別
- src/rtb/eval/model_candidate.py
- tests/analyzer/test_boundaries.py 的相對匯入解析

所有實驗都在 /tmp/資安-opus-p13i1r3 做,只用假後端,沒有呼叫真的模型,也沒有寫 ~/.rtb。做完已刪掉臨時目錄,沒有留下背景行程。/Users/enzo/rtb-13i1-rev 沒有被改動:`docs/.governance-log.jsonl` 顯示已修改、審材目錄顯示未追蹤,兩者都是開工前就存在的狀態。

3 條,blocking 1。
