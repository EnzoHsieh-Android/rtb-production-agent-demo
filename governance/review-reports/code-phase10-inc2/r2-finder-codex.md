severity: major

### 1. 公開工廠可把任意計分結果簽成正式報告
severity: major
blocking: 是
引句:「cells, confusion, changed = _parts(scored, with_bounds=True)」
觸發情境：呼叫公開的 `production_report()`，傳入由合成案例手工建立的 `ScoredCase`，把每格答案設成標準答案、路徑全部設為 `CODE_RULE`，再填入非空的出處文字；全程沒有正式抽樣、人工標註或候選呼叫。工廠仍會取得私有簽發者並建立 `ProductionReport`，採用閘只看型別與下界，結果五格全部進入已驗證清單。實測正式報告的所有 `paths` 都只有 `code_rule`，但 `validated.cells` 仍含五格；`sha256="not-a-hash"`、`sampling_source="synthetic rows"` 也被接受。私有簽發者因此只擋直接呼叫建構式，無法防止呼叫者無意把錯的資料送進公開工廠。file: `src/rtb/eval/scoring.py:112` file: `src/rtb/eval/scoring.py:195` file: `src/rtb/eval/adoption.py:176`
建議修法：不要讓一般呼叫者直接把裸 `ScoredCase` 升格。建立受控的正式評估執行物件，綁定隱藏集識別、候選版本／呼叫、抽樣與標註證明；正式計分入口應確認候選確實被執行，並拒絕 `CODE_RULE`／退回結果冒充候選品質。採用閘再核對報告、比較表與同一次候選評估的識別。

### 2. 比較表的 mapping key 與列內評分格沒有綁定
severity: major
blocking: 是
引句:「reasons += _row_problems(None if candidate is None else candidate.get(cell), limits)」
觸發情境：只建立一列 `ComparisonRow(cell=PAUSED, ...)`，再用 `{cell: same_row for cell in WorthCell}` 把同一列掛到五個 key。採用迴圈只依 key 取列，完全不檢查 `row.cell` 是否等於正在判斷的格；實測五格全部通過，儘管所有列內的 `cell` 都是 `paused`。這讓只量過一格的成本、延遲及失敗率冒充每格都有量，重現第 1 輪「全域一列套全部格」的問題。file: `src/rtb/eval/adoption.py:79` file: `src/rtb/eval/adoption.py:185`
建議修法：逐格判斷前強制 `row.cell is cell`，不一致即視為該格沒有候選實測；最好由建構器從列序列建立 mapping，拒絕重複格、錯鍵及缺格。另應把候選版本或評估執行 ID 放入列並與正式報告核對。

### 3. 揭露狀態接受字串，`"false"` 反而會放行
severity: major
blocking: 是
引句:「elif not report.provenance.candidate_predates_disclosure:」
觸發情境：從 JSON、命令列或其他未做 runtime schema 驗證的邊界建立 `HiddenSetProvenance(candidate_predates_disclosure="false")`。資料類別不驗證這欄必須是 `bool`；非空字串在 Python 為真，因此 `not "false"` 是假。實測這個值的型別是 `str`，採用結果仍有五個已驗證格，等於把「候選不早於揭露」解讀成可採用。file: `src/rtb/eval/scoring.py:102` file: `src/rtb/eval/adoption.py:179`
建議修法：`HiddenSetProvenance.__post_init__` 使用 `type(value) is bool` 驗證揭露欄；外部輸入先經明確 schema 解析，不接受字串真值。補 `"false"`、`"true"`、`0`、`1`、`None` 的拒絕測試。

### 4. 尚待覆核的品質門檻已能簽出採用結果
severity: major
blocking: 是
引句:「# 暫用、待使用者覆核(計劃〈設計〉)。」
觸發情境：品質門檻仍處於 patch 自己標明的「待使用者覆核」狀態，但程式已把 `0.80`、`0.95` 固定寫進 `BARS`。呼叫者只要傳入任意非 `None` 的 `OperationalLimits`，這兩個尚未裁定的品質門檻就會被視為有效；前述反例以 `OperationalLimits(1, 1, 1, 1)` 得到五格已驗證清單。這違反「門檻未定時一格都不採用」的 fail-closed 條件。file: `src/rtb/eval/adoption.py:25` file: `src/rtb/eval/adoption.py:31`
建議修法：在使用者正式裁定前，把品質門檻也建模成未設定狀態，並由採用閘統一檢查；或等決策紀錄完成後才把常數升格為有效政策。若門檻來自呼叫端，應使用帶版本與核可來源的完整 `AdoptionPolicy`，不能只靠「傳了數字」代表已裁定。

### 5. 動態匯入掃描仍漏掉常見別名與組字串寫法
severity: major
blocking: 是
引句:「if called in dynamic and node.args and isinstance(node.args[0], ast.Constant):」
觸發情境：受禁目錄使用 `im = importlib.import_module; im("rtb.eval")`、`__import__("rtb", fromlist=["eval"])`、`importlib.import_module("rtb." + "eval")`，或先把 `"rtb.eval"` 放入變數再呼叫。逐一把這四種 AST 傳給 `_eval_imports`，回傳都為空；Ruff 的靜態 banned-api 也不會涵蓋這些動態目標。結果 analyzer、executor、DSP、domain 或 ops 仍可匯入評估套件，破壞 [S712] 的隔離合約。file: `tests/eval/test_evaluation.py:349` file: `tests/eval/test_evaluation.py:368`
建議修法：受保護層若沒有合法動態匯入需求，直接禁止 `__import__` 與 `importlib`／`import_module`，比有限模式列舉可靠；否則掃描器至少要追蹤簡單賦值別名、解析常數字串串接及 `__import__` 的 `fromlist`，並把上述四種寫法加入五個目錄的探針。

驗證註記：Wilson 邊界本身正確，15/15、16/16、72/72、73/73 的下界分別約為 0.796117、0.806392、0.949349、0.950008，`<` 比較可讓剛好等於門檻通過。指定 pytest 原命令因唯讀環境沒有可用暫存目錄而在初始化失敗；改用 `-s -p no:cacheprovider` 後，評估子集 20 passed。全套為 517 passed、35 failed、1463 errors，大量錯誤源於相同的不可寫暫存目錄限制。