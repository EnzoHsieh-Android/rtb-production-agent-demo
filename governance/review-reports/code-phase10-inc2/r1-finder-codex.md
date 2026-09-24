severity: major

### 1. 合成集只要改一個字串就能簽發已驗證清單
severity: major
blocking: 是
引句:「if report.kind != PRODUCTION:」
觸發情境：把合成集的 `score(...)` 結果傳給 `build_report(PRODUCTION, ...)`，再提供表面完整的量測列。`Report.kind` 只是呼叫端可任意指定的字串，沒有正式環境抽樣、人工標註、隱藏集隔離或候選版本證明。實測 300 筆合成集因此讓 `delivery_with_value` 進入非空已驗證清單，違反 [S708]、[S710] 及「合成集不能產生已驗證清單」的規則。file: `src/rtb/eval/scoring.py:88` file: `src/rtb/eval/adoption.py:131`
建議修法：不要以字串判定證據來源；正式報告應使用獨立型別或受控建構器，強制攜帶並驗證隱藏集版本與雜湊、抽樣與人工標註出處、候選版本及揭露狀態。採用函式只接受這種完整的正式報告型別。

### 2. NaN 會被當成完整量測並通過採用閘
severity: major
blocking: 是
引句:「if self.measured and (self.value is None or self.reason is not None):」
觸發情境：把成本、延遲及各失敗率設為 `Measure.of(float("nan"))`。`Measure` 只排除 `None`，而後續的 `nan > bar` 永遠為假，因此所有欄位都被視為已量且未超標。用 16/16 的值得加格實測可得到 `adopt=True` 和非空已驗證清單。file: `src/rtb/eval/adoption.py:44` file: `src/rtb/eval/adoption.py:110`
建議修法：`Measure` 與 `OperationalLimits` 建構時拒絕 NaN、無限大及負值；比率另限制在 `[0,1]`。採用檢查應採 fail-closed，遇到任何非有限或越界數值都加入阻擋理由。

### 3. 比較表是全域一列，未量的逐格指標會被冒充成已量
severity: major
blocking: 是
引句:「shared = _operational_problems(candidate, limits)」
觸發情境：候選只在某一格量過成本、延遲與失敗率，但呼叫端提供一個 `ComparisonRow`。程式在進入逐格迴圈前只檢查這個全域列一次，之後把同一份量測套給所有品質過關的格；資料模型甚至沒有格欄位，無法表達某格沒量。這會讓未實測格進入已驗證清單，違反規格要求的「每一格」比較表及 [S708]。此外 `OperationalLimits` 只有 p95 延遲門檻，中位延遲雖有量測欄位卻完全不比門檻。file: `src/rtb/eval/adoption.py:59` file: `src/rtb/eval/adoption.py:75` file: `src/rtb/eval/adoption.py:134`
建議修法：把比較資料改成 `WorthCell -> ComparisonRow`，在逐格迴圈內檢查該格所有欄位；每格缺列或缺值都阻擋。門檻也應逐項建模，至少補上中位延遲門檻。

### 4. 正式報告永遠沒有 Wilson 下界，且非值得加格少算一項下界
severity: major
blocking: 是
引句:「paths=dict(Counter(c.path for c in cases)), lower_bound=None)」
觸發情境：即使呼叫 `build_report(PRODUCTION, ...)`，每格的 `lower_bound` 仍固定是 `None`，人讀報告也沒有下界欄；採用函式則在別處臨時計算。非值得加的四格只算類別正確率下界，沒有產生規格要求的 `1 − 誤提案率` 下界。結果是採用決定所依賴的統計值不在結構化報告或決定紀錄中，無法核對 [S708]；單一 `lower_bound` 欄位也無法容納該格的兩項指定品質指標。file: `src/rtb/eval/scoring.py:49` file: `src/rtb/eval/scoring.py:75` file: `src/rtb/eval/adoption.py:120`
建議修法：把每項指標建模成包含分子、分母、點估計與 Wilson 下界的結構；正式報告同時計算並保存召回率，或 `1 − 誤提案率` 與類別正確率的下界。採用函式只讀報告中已保存的逐項下界，紀錄產生器也逐項輸出。

### 5. 預算擾動同時改了花費，沒有驗到只改預算的不變量
severity: major
blocking: 是
引句:「if variant != "base" and not fault.startswith("spend:"):」
觸發情境：生成 `budget` 變體時，程式先換預算，隨後因為它不是 `base` 又依新預算重抽花費。實際生成的 100 組中有 97 組預算變體也改了花費，不符合 [S713] 的「同一筆輸入只改配速或預算」。依賴 `spend / budget` 的錯誤候選可能因兩欄一起縮放而保持答案不變，使擾動報告假綠。file: `src/rtb/eval/generator.py:121`
建議修法：三個變體分支明確處理：`base` 原值、`spend` 只改花費、`budget` 只改預算。測試應逐欄比較每組變體，斷言除指定欄位外完全相同，而不只檢查變體名稱齊全。

### 6. 採用時仍會輸出不採用理由與缺證據清單
severity: major
blocking: 是
引句:「*[f"- {reason}" for reason in NOT_ADOPTED_REASONS],」
觸發情境：未來正式報告有任一格通過，使 `adoption.adopt=True`。紀錄標題會寫「結論：採用 Jev」，但隨後仍無條件輸出「不採用的理由」、兩條固定不採用理由及整份缺證據清單。實測產生的同一份紀錄同時包含「採用 Jev」與「缺正式環境紀錄、人工標註與候選實測」。file: `src/rtb/eval/record.py:85`
建議修法：所有結論、理由和缺證據項目都從同一個 `Adoption` 結果衍生；採用時不輸出全域不採用理由，只列未驗證格及其實際缺口。另應驗證傳入的報告、比較表與採用結果具有相同評估執行識別或雜湊。

### 7. 五個目錄都能用動態匯入繞過禁令
severity: major
blocking: 是
引句:「else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])」
觸發情境：在 analyzer、executor、DSP、domain 或 ops 中寫 `__import__("rtb.eval.eval_set")`。五份 Ruff 設定全部回報通過；原始碼掃描也只處理 `ast.Import` 和 `ast.ImportFrom`，不檢查呼叫節點。因此五個受禁目錄都能讀取評估集，破壞 [S712] 的隔離與防外洩目的。`importlib.import_module(...)` 在未禁止動態匯入的四個目錄也能繞過。file: `tests/eval/test_evaluation.py:238`
建議修法：邊界掃描加入對 `__import__`、`importlib.import_module` 及其別名呼叫的辨識，並在五份設定中一致禁止動態匯入；測試要對五個目錄分別投放直接匯入、相對匯入和兩種動態匯入探針。

測試註記：指定的 eval 測試以 `-s` 避開唯讀沙盒的暫存輸出限制後為 12 passed。全套測試有 509 passed，但因環境沒有可寫暫存目錄而出現 1463 個 setup errors，另有 35 failures，故全套結果不能作為有效綠燈。