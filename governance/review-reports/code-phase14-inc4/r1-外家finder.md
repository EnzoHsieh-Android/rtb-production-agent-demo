severity: major

## 發現 1: 錄製不全時，合計仍把缺失當成 AI 無有效答案

severity: major  
blocking: 是

引句:「raw_cells.append(_stats(cell, [(r.ai_raw, r.no_answer) for r in mine], mine))」

逐格列在錄製不全時會顯示「沒量」，但新合計與開頭摘要仍彙總這些案例，將找不到錄製算進「無有效答案」及 36 筆分母。唯讀重現中，讓一筆名稱正常案例缺錄製後，逐格列顯示沒量，合計卻變成「有效答案 22/36、答對 11、無有效答案 14」；理由又稱這批沒有模型回應可算。這些合計不能作為完整批次的模型成績。佐證：`src/rtb/eval/investigation_report.py:347`、`:405`、`:475`；現有部分錄製測試見 `tests/eval/test_investigation_eval.py:913`。

翻紅重現：在 `test_a_partly_recorded_batch_is_not_reported_as_measured` 取得 `text` 後，檢查 `### 合計(名稱正常)` 段含「錄製不全」，目前會失敗；逐格已有此標示，合計沒有。合計應一併標為未量或明確標為不完整資料。

## 發現 2: Phase 10 把未執行的模型子集寫成「跑了」

severity: minor  
blocking: 否

引句:「跑了 210 個情境(子集每格 14 組、每組 3 個變體)」

同一節寫「模型一次都沒被呼叫到」及「沒有批次紀錄」，因此「跑了 210 個情境」會讓讀者誤以為模型已完成評估。這是子集的情境數，宜寫「候選子集含 210 個情境」。佐證：`governance/eval/phase10-worth-adoption.md:72`、`:76`、`:78`；字句由 `src/rtb/eval/record.py:303` 產生。

## 發現 3: 新測試未守住提交報告與歷史錄製的一致性

severity: minor  
blocking: 否

引句:「report = ir.build_report(ie.run_set(cases, RawVsVeto(cases)))」

新增的核心斷言使用合成回答 `RawVsVeto`，能測分類邏輯，但改錯已提交報告中的 23/36、11/19 或延遲數字，這些測試仍會通過。對將寫入 README 的數字，建議增加唯讀錄製重算與報告關鍵數字的比對。佐證：`tests/eval/test_investigation_eval.py:429`、`governance/eval/phase13-investigation-adoption.md:5`。

本次自行讀取錄製重算：Phase 13 為有效答案 23/36、答對 12、誤提案 11/19、無有效答案 13；40 次呼叫延遲中位約 4236 毫秒、p95 約 7244 毫秒，雙胞胎變動為 13/10/0。Phase 10 五格分類亦與報告一致。本審查未啟動背景工作；所有自行啟動的唯讀命令均已結束，未寫檔。