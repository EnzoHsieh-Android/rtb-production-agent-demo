severity: major

## 發現 1:成本豁免仍會被成本「沒量」擋下
severity: major
blocking: 是

引句:「cost = () if limits.cost_exempt else (」

`operational_problems` 雖跳過成本門檻比較，卻先要求 `row.measures()` 的每一欄都有量測；其中仍包含成本。裁定要求這個決策點只看品質、延遲與失敗率。file: `src/rtb/eval/adoption.py:178`；file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase13AI參與決策_計劃.md:500`

例子：`cost_exempt=True`、成本為 `Measure.not_measured("自研模型未計價")`，其餘量測全合法且低於門檻 → 預期不因成本產生問題 → 實際回報「比較表有沒量或不合法的欄位」。重現：以這組 `OperationalLimits` 和量測列直接呼叫 `operational_problems`；這是純記憶體函式，不需模型。

## 發現 2:缺一份錄製時比較表仍宣稱模型已量
severity: major
blocking: 是

引句:「shown = ("沒量(錄製不全)" if report.model_row is None or mine is None」

只要其他正常案例有一次讀到錄製，`model_row` 就不是 `None`；即使某格缺了一份錄製、該案例已退回程式規則，比較表仍把該格的 `class_correct/n` 印成模型成績。這違反 patch 自述的「錄製不全就寫沒量」。批次驗收會變紅，但報告中的模型成績仍是錯的。file: `src/rtb/eval/investigation_report.py:155`；file: `src/rtb/eval/investigation_report.py:271`

例子：同格四筆正常案例中三筆錄製齊全、一筆缺錄製 → 預期該格模型欄顯示「沒量(錄製不全)」 → 實際顯示如 `3/4`，把退回規則的結果算作模型成績。重現：用假模型錄一批、移走其中一筆正常案例的錄製，再以 `--verify` 重播並查看比較表；不需呼叫真模型。

2 條,blocking 2。