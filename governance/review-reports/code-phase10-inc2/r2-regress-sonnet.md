severity: clean
以下是完整報告全文(已依格式規則書寫,severity 單獨一行放在檔首):

---

severity: clean

前輪 10 條(併成 9 件)逐條讀碼確認全部真的修好,對應防線各做變異檢查(含實作員 20 道之外自己補的幾道)全數翻紅,增量 1 最終版 `ValidatedCells` 新形狀在評估套件裡用法正確,報告數字重跑一致。沒有找到需要開新代號的問題。

## 1. 逐條驗收(讀碼,不只看測試)

- **x1-1 正式/合成報告改成獨立型別**:`src/rtb/eval/scoring.py:122-150` 的 `ProductionReport` 用 `__slots__` + 必填簽發者哨兵(`_PRODUCTION_ISSUER`),只有 `production_report()` 建得出來;`adoption.decide_adoption` 改成 `report = evidence if isinstance(evidence, ProductionReport) else None`(`src/rtb/eval/adoption.py:176`),不再靠 `Report.kind` 字串判斷。`HiddenSetProvenance` 帶版本、雜湊、抽樣/標註出處、候選版本、`candidate_predates_disclosure` 五項且逐欄驗非空字串。已修好。
- **x1-2 NaN 被當完整量測**:`Measure.__post_init__`(`src/rtb/eval/adoption.py:58-63`)呼叫 `_finite_nonnegative`(`:45-47`),用 `math.isfinite(value) and value >= 0` 擋掉 NaN、Infinity、負數,另擋 `bool`(避免 `True`/`False` 混進數值)。`OperationalLimits` 也套同一支檢查。已修好。
- **x1-3 比較表全域一列冒充逐格,缺中位延遲門檻**:`ComparisonRow` 現在本身帶 `cell: WorthCell` 欄位,`decide_adoption` 的 `candidate` 參數改成 `Mapping[WorthCell, ComparisonRow] | None`,`_row_problems` 在逐格迴圈內用 `candidate.get(cell)` 個別檢查(`src/rtb/eval/adoption.py:191`);`OperationalLimits` 新增 `latency_median_us` 且在 `checks` 清單裡真的比對(`:148`)。已修好。
- **x1-4 正式報告永遠沒有 Wilson 下界、非值得加格少算一項**:`Metric` dataclass 現在同時存 `numerator`/`denominator`/`lower_bound`;`_metrics()`(`src/rtb/eval/scoring.py:153-160`)對「不誤提案的比例」與「類別正確率」兩項指標都用 `with_bounds` 旗標算下界,採用函式的 `_quality_problems` 只讀報告存好的下界(`src/rtb/eval/adoption.py:158-167`)。已修好。
- **x1-5 預算變體重抽花費**:`generate()` 改成 `perturbed_spend` 只在 `variant == "spend"` 時套用,`budget` 變體用原始 `spend`(`src/rtb/eval/generator.py:122-125`,引句:「`use_spend = perturbed_spend if variant == "spend" else spend`」)。已提交的 `eval_set.py` 也同步重新生成(對照 delta patch,`paused-00-budget` 的花費從重抽值改回跟 base 一樣)。已修好。
- **x1-6 採用時仍印不採用理由**:`Adoption.reasons` 在 `decide_adoption` 尾端改成 `() if adopt else (...)`(`src/rtb/eval/adoption.py:204`);`record.render` 改成 `if adoption.adopt:` 印「已驗證的格」、否則才印「不採用的理由」段落(`src/rtb/eval/record.py:109-112`)。已修好。
- **x1-7 / eval-2 匯入禁令看不到動態匯入**:`tests/eval/test_evaluation.py` 的 `_eval_imports()` 新增 `ast.Call` 分支,辨識 `__import__`、`importlib.import_module`(含 `from importlib import import_module as im` 別名),對 analyzer/executor/dsp/domain/ops 五個目錄各打 5 種探針(direct/relative/dunder/import_module/aliased)。已修好(測試層級的掃描能力補齊;ruff 靜態規則本身仍抓不到動態字串,這是所有目錄既有的通用侷限,不是這次獨有缺口,r1 已註明不算新增問題)。
- **eval-1 「點擊多於曝光」故障誤把轉換一起歸零**:`_inject()` 的 `clicks>impressions` 分支已拿掉 `fields["conversions"] = 0`,只改 `clicks`(`src/rtb/eval/generator.py:87-88`)。`tests/eval/test_evaluation.py:114-116` 另補一條斷言「至少有一筆 clicks>impressions 的轉換是正的」釘住不回歸。已修好。
- **arch-1 record.py 命令列不合專案慣例**:`src/rtb/eval/record.py` 改成 `run(argv, *, out) -> int`(有 `EXIT_OK = 0`)與 `main(argv) -> None: raise SystemExit(run(argv))` 兩層形狀(`:142-159`),跟其餘 8 支 `__main__` 模組同形狀。已修好。

## 2. 測試殺傷力(在 /tmp 副本逐一變異,清完再跑對應測試)

跑法:`cp -R /Users/enzo/rtb-p10i2 /tmp/rtb-p10i2-audit`,逐一用 `sed`/Python 改一行關鍵防線,`PYTHONPATH=src .../pytest -q tests/eval`,確認翻紅後用 `git -C /tmp/rtb-p10i2-audit checkout -- <file>` 還原(全程沒碰 `/Users/enzo/rtb-p10i2` 工作樹)。

對應 9 條逐一驗殺傷力(全部翻紅):
1. `adoption.py` 拿掉 `isinstance(evidence, ProductionReport)` 判別 → `test_a_synthetic_report_can_never_be_adopted` 紅(`AttributeError: 'SyntheticReport' object has no attribute 'provenance'`)。
2. `_finite_nonnegative` 拿掉 `isfinite`/`bool` 檢查、只留 `value>=0` → `test_measures_and_limits_reject_non_finite_and_negative_values[inf]` 與 `[True]` 紅。
3. `_row_problems` 的 `checks` 拿掉延遲中位那一行 → `test_a_slice_is_validated_only_when_every_bar_is_met` 紅。
4. `production_report` 硬改成 `with_bounds=False` → 該測試與 `test_the_adoption_decision_is_no_without_measured_validated_slices` 兩支紅。
5. `_metrics` 只給 `CLASS_ACCURACY` 算下界、`NO_FALSE_PROPOSAL` 恆 `None` → 同上兩支紅。
6. `generate()` 把 `use_spend` 條件從 `variant == "spend"` 改成 `variant != "base"`(budget 變體也重抽花費)→ `test_the_eval_set_is_pinned_by_hash`、`test_the_generator_covers_every_anomaly_and_matches_the_committed_set` 紅。
7. `_inject` 的 `clicks>impressions` 分支補回 `fields["conversions"] = 0` → 同一支生成器測試紅(逐值比對 committed 常數模組不符,以及「至少一筆轉換為正」斷言落空)。
8. `record.render` 拿掉 `if adoption.adopt` 判斷、兩段都印 → `test_the_adoption_decision_is_no_without_measured_validated_slices` 紅(「不採用的理由」出現在採用文本裡)。
9. `decide_adoption` 的 `reasons=() if adopt else (...)` 改成恆印理由 → `test_a_slice_is_validated_only_when_every_bar_is_met` 紅。
10. `record.run` 拿掉 `return EXIT_OK` → `test_the_record_command_line_has_the_project_shape` 紅(`None == 0` 失敗)。
11. `tests/eval/test_evaluation.py` 的 `_eval_imports` 拿掉 `ast.Call` 分支(退回只掃 Import/ImportFrom)→ `test_nothing_outside_the_eval_package_imports_it` 自己的探針斷言先紅(`dunder`/`import_module`/`aliased` 三種探針测不到),證實這支測試真的在守動態匯入偵測邏輯本身,不是空測試。

實作員 20 道之外自己另補、也全部翻紅的兩道:
12. `decide_adoption` 拿掉「報告沒有這一格」的檢查(`cell not in cells`),讓報告缺一整格時預設當過關 → `test_a_slice_is_validated_only_when_every_bar_is_met` 的 `low_recall`(只給一格的正式報告)案例紅(本該空清單卻驗出四格)。
13. `decide_adoption` 拿掉 `candidate_predates_disclosure` 檢查 → 同一支測試裡「候選版本不早於揭露」案例紅(本該空清單卻驗出全部五格)。

跑完每道之後都跑過一次全套(`PYTHONPATH=src .../pytest -q`),還原後全套 1803 passed(這次跑沒有重現 r1 提到的 F7 端到端計時測試偶發紅,屬機器負載雜訊,與這次鏡頭無關)。

## 3. 回歸:增量 1 最終版 `ValidatedCells` 新形狀

- `src/rtb/analyzer/policy.py:77-95`:`ValidatedCells` 是普通類別(非 dataclass)、`__slots__ = ("_cells",)`、建構式必填簽發者哨兵 `_VALIDATED_CELLS_ISSUER`(空清單 `NONE` 也一樣帶哨兵建)。
- 評估套件三處用法都對得上這個新形狀:`scoring.score()` 用 `ValidatedCells.NONE if trial is None else trial`(`src/rtb/eval/scoring.py:57`);`adoption.decide_adoption` 用兩參數建構式 `ValidatedCells(frozenset(...), _VALIDATED_CELLS_ISSUER)`(`src/rtb/eval/adoption.py:194-195`);`record.py` 兩處量延遲都傳 `ValidatedCells.NONE`(`:54`、`:59`)。全專案掃描確認只有 `analyzer/policy.py`(`NONE` 常數)與 `eval/adoption.py`(採用函式)兩處呼叫 `ValidatedCells(...)` 建構式,測試 `test_evaluation_calls_the_candidate_without_validating_anything` 用 AST 掃描原始碼樹釘住這一點,沒有殘留舊 API(如 `dataclasses.replace`)用法。
- 報告數字重跑:`PYTHONPATH=src .../python -m rtb.eval.record` 在 `/tmp` 副本重跑,逐格分子分母、指標、錯誤子型、比較表品質欄、結論與缺證據清單跟 `governance/eval/phase10-worth-adoption.md` 逐字相同;唯一有差的是延遲中位/p95 兩個數字(本機單次量測本來就會抖動,報告自己也註明「只當量級參考」,不影響採用結論)。

## 補充

- 全套測試:baseline `PYTHONPATH=src .../pytest -q` 在 `/Users/enzo/rtb-p10i2`(唯讀,沒有修改工作樹)跑出 1803 passed,沒有紅燈。
- 沒有發現需要開新代號的問題;上一輪兩個「不影響現況」的落差(生成器文件措辭、動態匯入的靜態分析通用侷限)都已在這輪處理或維持原判,沒有新增缺口。
