severity: minor

驗證範圍與方法:手算比對逐格計分(分母/誤提案率/類別正確率/召回率)、Wilson 95% 下界公式與門檻邊界(n=16/73 的精確臨界)、`route()`「最後有效答案」計分邏輯;獨立重跑 `python -m rtb.eval.record` 逐格比對 `governance/eval/phase10-worth-adoption.md`;重跑生成器逐值比對常數模組並抽查 12 種單一故障的欄位變動量;用 AST 掃描與 ruff 探針重現五個目錄的匯入禁令。跑 `PYTHONPATH=src .../pytest`:1794 passed、1 failed(`tests/executor/test_f7_end_to_end.py::test_f7_many_small_increases_stop_at_the_aggregate_limit`,執行端聚合上限的計時類測試,與評估套件無關,不在本次鏡頭範圍內,僅供留意)。

以下兩點是本次找到、值得記錄的落差,但都不影響已產出的評估數字或採用決定的正確性:

### 1. 「clicks>impressions」故障不是單一欄位故障,跟生成器自己宣稱的不一致

severity: minor
blocking: 否

引句:「在正常資料上只打一個故障(花費欄的故障在 `generate` 打)。」(`src/rtb/eval/generator.py:85`)

`_inject()` 對 `clicks>impressions` 故障除了改 `clicks` 外,還多執行 `fields["conversions"] = 0`(`src/rtb/eval/generator.py:89`)。實測:當該格基準情境(`_normal` 用 `WorthCell.DELIVERY_WITH_VALUE` 造出、`conversions_positive=True` 的索引,如 1、2、4、5)已經有正的 `conversions` 時,注入這個故障會同時改動 `clicks` 與 `conversions` 兩欄,而不是文件宣稱的「只打一個故障」。實際已提交的常數集裡,`anomaly-10`(唯一落在 `clicks>impressions` 的一組;`FAULTS[10]`)基準本該是「轉換正營收零」型態,結果被清成 `conversions=0, revenue=0.0`(對照 `governance/review-reports/code-phase10-inc2/r1-snapshot.patch:373`)。

不影響:`is_anomalous` 只要求「點擊多於曝光」成立就足以歸為資料異常,`conversions=0` 本身不構成第二個違規,所以歸格、標準答案、評分結果都沒有錯;[S717] 的測試也只驗證兩個布林條件與非負性,沒有斷言「只變動一欄」,因此測試維持全綠。純粹是生成器行為跟自己文件、跟計劃「輪流造單一故障」的描述對不上,會讓人誤以為 `anomaly-10` 組驗到的是「乾淨的單一違規」,實際上它同時失去了一個「轉換正營收零」邊界樣本的代表性(該邊界改由其他索引頂替,仍在測試裡驗到,不影響 [S717] 覆蓋度)。

建議:`clicks>impressions` 分支拿掉 `fields["conversions"] = 0` 這行(前面已用生成的 `impressions/clicks/conversions` 關係推導過,新 `clicks` 必然大於原 `conversions`,不需要額外歸零),讓 12 種故障都真的只動一欄。

### 2. 匯入禁令對 `importlib.import_module` 這類動態匯入沒有防線

severity: minor
blocking: 否

引句:「names = ([a.name for a in node.names] if isinstance(node, ast.Import)」(`governance/review-reports/code-phase10-inc2/r1-snapshot.patch:1336`,對應 `tests/eval/test_evaluation.py:252`)

`test_nothing_outside_the_eval_package_imports_it` 的雙層防線——五個目錄 `ruff.toml` 的 `TID251` banned-api,加上這支只掃 `ast.Import`/`ast.ImportFrom` 節點的原始碼掃描——都是靜態語法層級的檢查。實測:在 `/tmp` 隔離目錄放一支等同 `analyzer` 佈局的檔案,內容為 `importlib.import_module("rtb.eval.eval_set")`,對 `src/rtb/analyzer/ruff.toml` 跑 `ruff check` 只出現 `I001`(import 排序),沒有 `TID251`;用專案這支測試同樣的 AST 邏輯掃描該檔案,`importers` 回傳空列表。也就是說,`analyzer`、`executor`、`dsp`、`ops` 四層(`domain` 因為另有「領域層不得動態匯入」的通用禁令而剛好連帶擋住)若真的寫出 `importlib.import_module("rtb.eval...")` 或 `__import__("rtb.eval...")`,現有兩層防線都測不出來。

不影響現況:目前原始碼裡沒有這種動態匯入,合成集本身也不因此外洩(規則作者要故意寫這種呼叫才會踩到),而且 `rtb.dsp`/`rtb.executor`/`rtb.ops` 既有的禁令本來就有同樣的靜態分析侷限,不是這次新引入的獨有缺口。列出來是因為題目明確要查「匯入禁令有沒有漏」,這是唯一一個真的測得出來的漏洞,留給之後要不要補一條「禁止 `importlib`/`__import__` 動態匯入」的邊界測試時參考。
