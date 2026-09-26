severity: minor

# Phase 14 增量 1 代碼審(r2,單一外部審查員,第 2 輪)

審材:`governance/review-reports/code-phase14-inc1/r2-delta.patch`(本輪修正差異,逐 hunk 讀完)、
`r2-snapshot.patch`(全量參照)。核對指令:`PYTHONPATH=src .venv/bin/python -m pytest -q tests/domain
tests/eval` → 420 passed;另跑 `.venv/bin/ruff check`、`.venv/bin/mypy` 於 `src/rtb/domain/nine_rules.py`、
`src/rtb/eval/investigation_cases.py` 及兩支測試檔,均無警告。

## 一、上輪 9 條逐條驗收

1. 歷史列缺時間、過去調整缺預算沒測試、歷史判定依列序而不同——修對。`_history_decision`
   改成掃完全部列再用 `recent |= row.committed_at > cutoff` 判,任一列缺時區立刻回證據不足,
   跟列序無關;新增 `test_history_missing_time_is_insufficient_in_either_row_order`、
   `test_any_recent_history_row_wins_after_all_rows_are_checked`、
   `test_history_timezone_without_offset_is_insufficient`、
   `test_past_adjustment_missing_budget_is_insufficient` 四支測試都對到具體案例,全綠。
2. [S1403] 綁定的測試沒驗到收據捨入那一半——修對。測試換成 `daily([(2500,1250)]*4,
   [(3334,833),(3333,833),(3333,832)])`,precise drop 50.04% 但收據算出 `-50.0`,並直接斷言
   `receipt["conversion_rate_change"] == "-50.0"`;計劃筆記與條款例句也同步改成 50.04%/-50.0%。
3. "標準答案不讀決策規則"的說法和守衛已名不符實——修對。模組說明改成明講「標準答案與後續正式規則
   刻意同源於 `rtb.domain.nine_rules`……同源 36/36 不作品質證據」,測試裡的舊註解也同步改寫,不再
   宣稱獨立於決策規則之外。
4. `decide()` 沒驗 `now` 時區——修對。`decide` 開頭加 `if not is_aware(now): raise ValueError("now
   必須帶時區")`,新增 `test_naive_decision_clock_is_rejected_explicitly` 驗證訊息與行為。
5. 負數值在逐日、過去調整兩邊處理不一致、被標成「缺值」——修對。新增 `RuleReason.INVALID_ROW_VALUE`
   與 `_invalid_daily_row`/`_invalid_adjustment_row`,逐日與過去調整都先掃全列擋負數與欄位不自洽,
   三支新測試(`test_invalid_daily_row_is_insufficient_before_segment_aggregation`、
   `test_invalid_past_adjustment_is_insufficient`、
   `test_invalid_older_adjustment_cannot_be_hidden_by_a_newer_rule_hit`)覆蓋到位。
6. eval 生成器檔案自稱「不讀決策規則」但已直接匯入——修對。同第 3 條,docstring 與
   `test_no_generated_case_sits_on_a_rounding_boundary` 裡的註解都改成承認同源,不再自我矛盾。
7. 新檔十個 frozen dataclass 全加 `slots=True`,同層既有慣例從不用——修對。全部十個類別的
   `slots=True` 都拿掉,一致改回 `@dataclass(frozen=True)`,跟 `worth.py`/`metrics.py` 等同層檔案的
   慣例一致。
8. 新的證據資料類別沒有建構期驗證、`# noqa: S101` 沒理由——修對。十個類別全部補上 `__post_init__`
   逐欄驗證並丟 `ValueError`,四處 `# noqa: S101` 也都補了理由註記(`- 異常輸入已先排除`、`- 同上`、
   `- 四查詢已核對`),跟 `worth.py`/`metrics.py` 的既有寫法一致。
9. 同名 `_segment_rate` 在領域層與 eval 層各寫一份——修對。領域層私有函式改成公開的
   `segment_rate`(語意不變,仍先查缺值再交給 `metrics.exact_ratio`),eval 層的 `_segment_rate`
   與配套的 `_total` 整支刪掉,`trend_rate_change` 改成直接呼叫 `rules.segment_rate`,新增
   `test_eval_uses_the_domain_segment_rate` 用 monkeypatch 斷言 eval 真的呼叫到領域層那一份、
   分組是 `(4,5,6,7)`/`(1,2,3)`。

九條全部修對,沒有回頭看到「掛著改了但沒改到位」的情形。

## 二、修正引入的新問題

先交代四個指定懷疑點的走查結果,再列唯一站得住的新發現:

- **`__post_init__` 拒收評估/正式合法值?** 逐一構造 `Window`、`DailyRow`、`AdjustmentRow`、
  `HistoryRow`、`RuleEvidence` 的邊界值(`None`、`0`、負數、`True`/`False`、超過 `MAX_INT`)跟
  `tests/eval` 目前的資料型別(全部是 `int`/`float`/`None`,由 `_hour`/`_days`/`_adjustment` 產生)
  比對,沒有一個現有輸入會被新驗證擋下——420 筆測試全綠也印證了這點。真正的缺口見下面發現 1
  (方向相反:不是拒收,是誤收)。
- **不帶時區 `now` 丟 `ValueError` 會不會炸掉既有呼叫點?** 全庫只有
  `src/rtb/eval/investigation_cases.py:150/151/154`(用模組常數 `NOW = datetime(2026, 9, 25,
  tzinfo=UTC)`)和 `tests/domain/test_nine_rules.py`/`tests/eval/test_investigation_eval.py`
  (同樣用帶時區的 `NOW`)呼叫 `rules.decide`;`src/rtb/analyzer/investigation.py:104` 只借用
  `rules.RECENT_DAYS` 這個常數,完全沒呼叫 `decide`。目前沒有任何呼叫點會傳入沒時區的 `now`,
  不會炸。
- **歷史判定改成掃完全列後的語意?** 讀 `_history_decision` 新版邏輯:非 `update_budget` 的列一律
  `continue`(不驗時區),`update_budget` 列裡任一筆缺時區立刻整體判證據不足(跟位置無關),否則
  掃完全部列用 `or` 判有沒有落在 3 天窗內。跟計劃筆記新增的一句「歷史預算調整先掃完全部列,任一列
  缺時間就保守回無格證據不足,否則任一近期列才命中第 3 條,與列序無關」逐字對得上,且與該篇筆記
  同時新增的 `test_any_recent_history_row_wins_after_all_rows_are_checked` 兩種順序都驗過。沒有發現
  掃全列語意跟只在乎「最終結果」的既有呼叫方式衝突的地方。
- **負數判證據不足會不會動到 72 筆?** 72 筆由固定種子的生成器(`_hour`/`_days`/`_adjustment`)造出,
  數值只用 `rng.randint`/`round`/`_money`,沒有一條路徑會產生負數或 `clicks>impressions` 這類不自洽
  值,`_invalid_daily_row`/`_invalid_adjustment_row` 對 72 筆全部回 `False`;
  `test_missing_row_values_are_insufficient_without_changing_the_72_cases` 逐筆比對後仍是綠的,
  雜湊也沒變。不影響。

## 發現 1:`Window`/`DailyRow` 的金額驗證比同層 `WorthInput` 與上游 DSP 白名單更寬鬆,新加的字串分支沒有測試覆蓋
severity: minor
blocking: 否

引句:「if isinstance(value, str):」

佐證 file: `src/rtb/domain/nine_rules.py:94`(`_check_amount` 全函式見 91–100 行)

本輪新增的 `_check_amount` 除了用 `is_finite_or_none` 驗 `int`/`float`,還多接受一個 `isinstance(value,
str)` 分支,只要 `Fraction(value)` 解析成功就放行。實際走一遍:

```
rules.Window(100, 10, 1, "5/2", "1e3")
-> Window(impressions=100, clicks=10, conversions=1, spend='5/2', revenue='1e3')  # 接受
```

同一次 patch 明講「跟同層『型別保證、不靠呼叫端』的既有慣例看齊」(上一輪發現 3 的修法),但同層的
`WorthInput.spend` 型別是 `int | float | None`(沒有 `str`),用一樣的 `is_finite_or_none` 判準,直接
拒收字串:

```
WorthInput(status=..., spend="5", ...) -> WorthInputInvalid: spend 不合分析端 DSP 用戶端白名單的判準
```

上游 `src/rtb/analyzer/dsp_client.py:69/70/235/236` 對 `spend`/`revenue`(含逐日、長窗、過去調整前後)
一律用 `is_finite_or_none` 過白名單,同樣不收字串。也就是說,目前唯一會走到 `Window`/`DailyRow`
建構子的兩條路(`dsp_client` 產生的正式證據、`investigation_cases.py` 的評估案例)都不會給字串,
`_check_amount` 的字串分支目前是死碼,但 `Fraction()` 本身很寬鬆(科學記號 `"1e3"`、分數
`"5/2"`、底線分隔 `"1_000"`、全形/阿拉伯數字都會被接受,只有 `"inf"`/`"nan"`/空字串會被擋),
一旦以後真的有輸入源不經過 `dsp_client` 的白名單直接建構這兩個類別(例如計劃裡提到的增量 2
「無法證明調整前預算的舊列」這類改走別的重建路徑),這個分支就會放行一批 `WorthInput`
與白名單都會拒收的怪格式字串。`DailyRow.spend`/`revenue` 這兩欄不是擺著不用:
`_invalid_daily_row`(`src/rtb/domain/nine_rules.py:298-306`)會把它們 `Fraction()` 化去判正負,
所以字串分支不是純裝飾,而是真的會被判定邏輯讀到。`tests/domain/test_nine_rules.py` 目前沒有任何一
筆用字串建構 `Window`/`DailyRow` 的金額欄位,這個分支完全沒有測試覆蓋。

建議:要嘛把 `Window`/`DailyRow` 的金額驗證收斂成跟 `WorthInput`/DSP 白名單一致(只認
`int`/`float`/`None`),把型別註記的 `str` 一併拿掉;要嘛留著字串支援但補一條測試釘住哪些格式該收、
哪些該拒,並在領域筆記講清楚這個分支是為了哪個未來輸入源準備的。

## 總結

新增檢查九條全部驗證修對,唯一站得住的新問題是金額欄位的字串解析分支比同層既有慣例寬鬆且未經測試,
目前不影響 72 筆或任何既有呼叫點,建議收斂或補測試而非阻擋合入。
