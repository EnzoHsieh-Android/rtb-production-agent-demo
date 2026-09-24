severity: major
以下是完整審查報告(已依格式檔規則撰寫)。

---

severity: major

## 審查方法簡述
除了讀程式碼,我在 `/private/tmp/.../scratchpad/p10probe/` 下寫了三支探針腳本(不動工作樹),用 `PYTHONPATH=src` 對照凍結舊規則 `tests/analyzer/frozen_policy_e8b26f6.py` 與新的 `policy.decide()`/`policy.explain()`:
- probe1:狀態欄餵其他字串與非字串型別(`"ACTIVE"`、`"unknown"`、`True`、`1`、空白字串等)、曝光/點擊餵浮點小數、欄位餵字串數字、spend/revenue 餵超過 `MAX_INT` 的極大浮點與 `1e300`、多欄位餵負零、曝光正點擊零、負預算——共 20+ 組,逐筆比對 `frozen.decide` 與 `policy.decide`/`explain` 的型別、金額、有效期、原因碼、例外種類。**全部一致,沒有找到行為差異。**
- probe2:讓候選函式分別丟 `KeyboardInterrupt`、`SystemExit`、`GeneratorExit`、`MemoryError`、`AssertionError`、`RecursionError`,確認 `route()` 的例外邊界。
- probe3:逐欄比對 `WorthInput._is_amount` 與 `src/rtb/analyzer/dsp_client.py` 的 `STATE_FIELDS`/`METRICS_FIELDS` 白名單判準。
另外在 `/Users/enzo/rtb-p10i1` 用 `PYTHONPATH=src /Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest` 跑過全套測試:**1825 passed**,無紅燈。

`decide()`/`explain()` 在所有測過的輸入上都一致(`decide` 就是 `explain(...)[0]`,結構上保證一致,且探針沒有找到反例);`_judge()` 的 `except WorthInputInvalid` 退回路徑目前確實把「值不落格」的輸入攔下、統一改用舊版 `_has_delivery` 邏輯算,因此即使下面第 1 條的驗證落差存在,`decide()` 對外行為在我測過的範圍內仍未改變。以下兩條是我找到、值得記錄的落差。

### 1. 判斷點輸入驗證跟宣稱「跟 DSP 白名單一樣」不一致,[S715] 測試名稱名不副實
severity: major
blocking: 是
引句:「分析端 DSP 用戶端的白名單一樣:布林、非有限數、絕對值超過資料庫整數上限、其他型別一律拒絕。」
file: `src/rtb/domain/worth.py:5`(宣稱處)、`src/rtb/analyzer/dsp_client.py:67-81`(實際白名單)、`tests/analyzer/test_worth_check.py:179`([S715] 測試名為「跟 DSP 白名單一樣會拒的都拒」,但只驗了雙方共同會拒的壞值,沒驗方向性差異)

觸發情境(已用 probe3 實測,六個欄位逐一對照):
- `impressions=3.5`、`clicks=12.0`、`conversions=2.5`:`WorthInput` 用 `is_plain_number`(整數或浮點都收),`dsp_client.METRICS_FIELDS` 對這三欄用 `_is_count_or_none`,要求 `is_plain_int`——**DSP 白名單會拒的浮點,`WorthInput` 卻收下**。
- `budget=-50`:`dsp_client.STATE_FIELDS["budget"]` 是 `_is_int_between(0, value)`,負數會被拒;`WorthInput._is_amount` 只檢查 `abs(value) <= MAX_INT`,**負預算會被接受**。
- `spend=10**19`、`revenue=10**19`:`dsp_client.METRICS_FIELDS` 對這兩欄用 `_is_finite_or_none`,完全沒有 `MAX_INT` 上限,只要求有限數;`WorthInput._is_amount` 卻加了 `abs(value) <= MAX_INT` 的限制——**DSP 白名單會收的巨大有限數,`WorthInput` 反而拒收**(方向剛好相反)。

行為面:目前因為 `policy._judge()` 把 `WorthInputInvalid` 整個接住、退回舊版 `_has_delivery` 邏輯(用 probe1 逐案驗證過,`decide()`/`explain()` 輸出都跟凍結舊規則一致),所以**這條落差今天不會讓 `decide()` 產生錯誤結果**。但它破壞了 `worth.py` 檔頭明講、且 `[S715]` 測試名稱承諾的「跟 DSP 白名單一樣」這個合約——這個合約本身是假的。後面接上真正候選(增量 2)時,任何依賴這個假設寫候選邏輯的人,會遇到:本該被 DSP 擋掉、卻鑽進 `WorthInput` 的浮點計數與負預算;以及本該被候選看到、卻因超過 `MAX_INT` 而被錯誤打回現行規則的巨額 spend/revenue。

建議修法:讓 `WorthInput.__post_init__` 針對每一欄直接重用(或抽出共用函式給)`dsp_client.py` 對應欄位的檢查(`_is_int_between`/`_is_finite_or_none`),而不是六欄共用同一支 `_is_amount`;或者如果刻意要做防禦更嚴格的獨立驗證,就把檔頭與測試名稱改成如實描述(例如「比 DSP 白名單更嚴格」),不要宣稱「一樣」。

### 2. 候選退回路徑用同一個 `except Exception` 吞掉所有程式錯誤與資源耗盡例外
severity: minor
blocking: 否
引句:「候選是可替換的外部判斷:任何失敗都退回現行規則,不讓它改變流程狀態」
file: `src/rtb/analyzer/policy.py:130`

觸發情境(probe2 實測):讓候選丟 `KeyboardInterrupt`/`SystemExit`/`GeneratorExit` 時,`route()` 正確不吞、往外傳——這部分沒問題,因為它們是 `BaseException` 而非 `Exception` 子類。但同一個 `except Exception:` 也把 `AssertionError`(斷言失敗,通常代表候選本身有程式錯誤)、`MemoryError`、`RecursionError`(系統資源耗盡/失控遞迴)一併吞下,一律靜默退回 `code_rule()`,只留一個 `RoutePath.FALLBACK_EXCEPTION` 列舉值,沒有另外告警或分流。

這是檔案裡明講的設計選擇(「任何失敗都退回現行規則」),今天沒有真正候選掛上去,所以不影響現在的行為;但增量 2 一旦接上真候選,候選內部的型別誤用、None 未防呆等程式錯誤,或系統級的記憶體耗盡,都會被同一套「外部判斷失敗」語意吸收,不會有任何提高警覺的訊號,只能靠事後去看 `RoutePath` 分布才能發現候選一直在報錯或系統在告急。

建議修法:把系統級例外(至少 `MemoryError`、`RecursionError`)排除在「候選失敗」的正常語意之外(比照 `TimeoutError` 另立分支往外丟,或至少額外記錄/告警),避免跟「候選判斷不出來」混在同一個退回路徑裡。
