severity: major

### 1. 一筆候選結果仍可借大量現行規則樣本通過品質門檻
severity: major
blocking: 是
引句:「untouched = [c.cell.value for c in cells if not set(c.paths) & _THROUGH_CANDIDATE]」

file: `src/rtb/eval/scoring.py:209`
file: `src/rtb/eval/adoption.py:159`

觸發情境：每個非「值得加」格放入 73 筆正式樣本，但只有 1 筆走 `CANDIDATE`、其餘 72 筆走 `CODE_RULE`；「值得加」格同樣只有 1 筆走候選、其餘 15 筆走現行規則。讓所有現行規則答案正確，再提供完整比較表與已裁定門檻。

會出什麼錯的行為：`production_report()` 只檢查每格的路徑集合與候選路徑有沒有交集，品質指標與 Wilson 下界卻仍以該格全部樣本計算。唯讀直譯器實測得到五格全數進入已驗證清單；路徑計數分別是四格 `{candidate: 1, code_rule: 72}`、值得加格 `{candidate: 1, code_rule: 15}`。因此候選品質實際只觀察一筆，卻能借現行規則的 72／15 筆結果滿足最低樣本數與品質門檻，正是「一筆候選冒充整格候選品質」。

建議修法：品質指標的分母只能包含確實經過候選的案例，或要求正式候選評估的每一筆都不得走 `CODE_RULE`；若退回結果仍計入最終系統品質，還須把路徑計數、退回率比較列及正式報告綁到同一次評估，避免獨立填入的比較列掩蓋退回比例。補一筆候選加 72 筆現行規則不得驗證的測試。

### 2. `__import__` 經本地別名仍能繞過五層禁令
severity: major
blocking: 是
引句:「if called in dynamic:  # 代碼審第 2 輪:任何動態匯入呼叫都算,不只認字面常數」

file: `tests/eval/test_evaluation.py:397`
file: `tests/eval/test_evaluation.py:416`

觸發情境：受保護目錄加入 `loader = __import__`，再呼叫 `loader("rtb.eval")`；另一個等價寫法是 `from builtins import __import__ as load` 後呼叫 `load("rtb.eval")`。

會出什麼錯的行為：掃描器只依呼叫位置的名稱是否恰為 `__import__` 判斷，沒有追蹤上述別名。唯讀 AST 實測兩種寫法的 `_eval_imports()` 都回傳空清單；其中 `loader = __import__; loader("rtb.eval")` 經 analyzer 的 Ruff 設定也顯示 `All checks passed!`，但執行時確實會匯入評估套件，故第 2 輪要求的五層隔離仍可被繞過。

建議修法：在受保護五層直接拒絕任何載入 `__import__` 名稱的 AST 節點，並拒絕從 `builtins` 匯入或別名化 `__import__`；加入上述兩種探針，確認原始碼掃描與 Ruff 組合都會擋下。