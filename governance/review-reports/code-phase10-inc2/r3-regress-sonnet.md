severity: major
以下是完整報告全文(已依格式規則書寫,severity 單獨一行放在檔首):

---

severity: major

本輪判讀 r2-intake.md 的 6 件收貨(x1-1~x1-5、arch-1),讀 r3-delta.patch(cce84fd..8c004ad)逐條核對修法,對每條新防線在 `/tmp` 副本做變異(改掉防線關鍵行,確認對應測試翻紅、還原後再跑),並依火力指示重點檢查:(1) 受保護五層禁 `importlib` 後有沒有擋到合法用途,(2) 正式報告「每格至少一筆經過候選」的判準夠不夠格擋住現行規則冒充候選品質,(3) 揭露旗標、雜湊格式的拒絕是不是在建構當下發生。全套測試在 `/Users/enzo/rtb-p10i2`(唯讀,未改工作樹)用 `PYTHONPATH=src /Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest` 跑出 **1815 passed**,沒有紅燈,沒有既有合法用途被 `importlib` 禁令擋到(五層目錄底下的原始碼掃描不到任何 `importlib`/`__import__` 用法,只有 `ruff.toml` 自己的 banned-api 宣告)。

## 一、逐條驗收(讀碼 + 變異,已修好的不留 severity 字樣)

- **x1-2(比較表 mapping key 與列內 `cell` 沒有綁定)**:`src/rtb/eval/adoption.py:192-194` 在 `decide_adoption` 逐格迴圈裡加 `if row is not None and row.cell is not cell: row = None`,同一列掛在別格鍵下時視同該格沒量。在 `/tmp` 副本把這三行改回原本的一行 `reasons += _row_problems(None if candidate is None else candidate.get(cell), limits)`,`test_a_comparison_row_counts_only_for_its_own_cell` 翻紅(五格全被冒充驗證,應只驗 `PAUSED`)。**已修好。**
- **x1-3(`candidate_predates_disclosure` 不驗型別,字串 `"false"` 為真)**:`src/rtb/eval/scoring.py:123-124` 在 `HiddenSetProvenance.__post_init__` 加 `if type(self.candidate_predates_disclosure) is not bool: raise ValueError(...)`,`type(x) is bool` 用嚴格型別比對,`0`/`1`/`"true"`/`"false"`/`None` 都會被擋(不是 `isinstance`,不會被 `bool` 子類或其他真值繞過)。在 `/tmp` 副本拿掉這兩行,`test_the_disclosure_flag_must_be_a_real_bool` 五個參數化案例全翻紅。**已修好。**
- **x1-4(品質門檻標「暫用、待使用者覆核」卻已生效)**:`src/rtb/eval/adoption.py:26-27` 的註解改成「使用者 2026-09-24 裁定定案(計劃〈使用者裁定〉)」,常數值(`0.80`/`0.95`/`16`/`73`)沒變——這條本來就是文件性落差,disposition 也只要求改標,不要求改邏輯。核對計劃筆記的措辭一致。**已修好(標記與裁定狀態相符)。**
- **x1-5(動態匯入掃描漏別名、`fromlist`、字串串接)**:兩層防線都補了。①測試層:`tests/eval/test_evaluation.py:360-368` 的 `_eval_imports` 把判準從「只認第一個引數是字面常數」改成「任何 `__import__`/`import_module`(含別名)呼叫都算」,新增 `dunder_variable`、`import_module_variable` 探針。②結構層:`src/rtb/analyzer/ruff.toml`、`src/rtb/domain/ruff.toml`、`src/rtb/dsp/ruff.toml`、`src/rtb/executor/ruff.toml`、`src/rtb/ops/ruff.toml` 五份都新增 `"importlib".msg = "..."` banned-api,直接連 `import importlib` 這種還沒呼叫任何函式的匯入都擋,比原本只認特定呼叫形式更徹底。實測 `import importlib`、`from importlib import import_module`、`from importlib import import_module as im`、`import importlib.util` 四種寫法在五層的 `ruff.toml` 下都會噴 `TID251`;全專案掃描五層目錄的原始碼沒有任何 `importlib`/`__import__` 用法會被誤擋。在 `/tmp` 副本拿掉 `domain/ruff.toml` 的 `importlib` 一行,`test_nothing_outside_the_eval_package_imports_it` 新加的探針立刻翻紅(`F401` 沒有 `TID251`)。**已修好,且沒有擋到合法用途。**
- **arch-1(`is_plain_number` 判準被重刻,漏 `OverflowError`)**:`src/rtb/eval/adoption.py:46-48` 的 `_finite_nonnegative` 改成直接呼叫 `rtb.domain._checks.is_finite_or_none(value)`(`src/rtb/domain/_checks.py:37-44`,內含 `try/except OverflowError` 捕捉超大整數轉浮點數丟出的例外),自己只疊加 `value is not None and value >= 0`,不再是同一判準的第二份複本。在 `/tmp` 副本把 `_finite_nonnegative` 改回原本直接 `isinstance` + `math.isfinite` 的寫法,新測試 `test_huge_integers_are_rejected_without_crashing` 傳入 `10**400` 直接讓 `OverflowError` 洩漏出 `__post_init__`(不是翻紅成 `ValueError`,是整支測試直接因未捕捉例外而失敗),證實這條防線真的在守。**已修好。**
- **x1-1(公開工廠可把任意計分結果簽成正式報告)**:分兩半,雜湊格式那半修好,「經過候選」那半**沒有真的修好**,見下方發現。

## 二、發現

### 1. 正式報告「每格至少一筆經過候選」的判準形同虛設,現行規則為主的資料仍能冒充候選品質並被採用

severity: major
blocking: 是

引句:「untouched = [c.cell.value for c in cells if not set(c.paths) & _THROUGH_CANDIDATE]」

file: `src/rtb/eval/scoring.py:209-211`、`src/rtb/eval/scoring.py:50-54`(`ScoredCase` 定義)

觸發情境:呼叫公開的 `production_report(scored, provenance)`,`scored` 是手工組出的 `ScoredCase` 序列——每一格 73(或 `DELIVERY_WITH_VALUE` 16)筆裡,72(或 15)筆的 `path` 標成 `RoutePath.CODE_RULE`、`final` 全部等於標準答案,只有 1 筆的 `path` 標成 `RoutePath.CANDIDATE`。我在 `/tmp` 副本(未碰工作樹)用這個形狀重現:五格全部通過 `production_report` 的新檢查(因為每格 `set(c.paths) & _THROUGH_CANDIDATE` 非空),接著餵進 `decide_adoption` 配合達標的比較表列,結果是 `adopt=True`、`validated.cells` 五格全數驗證通過——跟修法前「全部走現行規則也能簽成正式報告」的原始缺陷後果完全一樣,只是把「0 筆」的門檻墊高成「1 筆」,實測仍然一擋就破。

會出什麼錯的行為:`_metrics()`/`_cell_report()` 算 Wilson 下界時用的是整格的 `n`(72+1 或 15+1),完全不分 `path`,所以只要那 72、15 筆現行規則剛好都答對(現行規則在容易的案例上本來就答得對,Phase 10 的評估紀錄本身也說「現行規則在某些格判錯」,不是全錯),下界照樣可以壓過 0.80/0.95 門檻。而且 `path` 只是 `ScoredCase`(`src/rtb/eval/scoring.py:50-54`)上一個沒有簽發者哨兵、任何呼叫端都能直接指定的欄位,跟 `final` 這個真正的計分結果毫無耦合——呼叫端甚至不用真的呼叫候選,只要把 1 筆的 `path` 貼上 `RoutePath.CANDIDATE` 標籤就能通過檢查,`production_report()` 的文件註解「每一格至少要有一筆經過候選的案例」因此是可以被自報、不可驗證的宣稱。這正是 x1-1 原始發現點名的問題本身(「呼叫者無意把錯的資料送進公開工廠」「合成案例手工建立」),第 2 輪的修法只是把觸發門檻從 0 筆調到 1 筆,沒有堵住同一個洞。

建議修法:「至少一筆」不足以證明整格反映候選品質,應該要求候選路徑覆蓋率(例如整格或每格 `_THROUGH_CANDIDATE` 的筆數比例達到某個高門檻,甚至要求逐格全部案例都經過候選,退回也算但 `CODE_RULE`——沒被路由到候選——的筆數要接近 0),或者根本不要讓 `production_report` 信任呼叫端自報的 `path`;更貼近合約的做法是像 `ValidatedCells`/`ProductionReport` 本身一樣,只信任「真的呼叫過 `score()`」產生的資料(例如把 `score()` 回傳一個帶簽發者哨兵的型別,`production_report` 只收這個型別而不收裸 `tuple[ScoredCase, ...]`),讓「有沒有經過候選」不是靠呼叫端自己填的欄位決定。

## 三、次要落差(minor,不影響行為)

### 2. `domain/ruff.toml` 的 `importlib.import_module` banned-api 現在是死規則

severity: minor
blocking: 否

引句:「"importlib.import_module".msg = "領域層不得動態匯入」」

file: `src/rtb/domain/ruff.toml:25`、`src/rtb/domain/ruff.toml:31`

觸發情境:同一份 `ruff.toml` 裡同時保留舊的 `"importlib.import_module"` 條目與新加的 `"importlib"` 條目;新條目已經涵蓋 `import importlib`、`from importlib import import_module`(含別名)等所有形式,舊條目變成永遠不會單獨命中的重複規則(實測 `from importlib import import_module` 會同時噴兩條 `TID251`)。

會出什麼錯的行為:不影響擋下能力,純粹是設定檔多一行維護負擔;之後如果有人誤以為「只擋 `importlib.import_module` 就夠、把通用的 `importlib` 條目移除」,反而會不小心開一個口子(因為兩條規則現在看起來像互相獨立的雙重保險,其實通用條目才是真正生效的那條)。

建議修法:可以留一句註解說明「`importlib` 條目已涵蓋這條,保留只是為了訊息更精準」,或乾脆刪掉重複的 `importlib.import_module` 條目,避免未來誤刪通用條目時沒人發現漏洞重開。

## 四、驗證註記

- 全套測試:`PYTHONPATH=src /Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest`(在 `/Users/enzo/rtb-p10i2`,未改工作樹)→ **1815 passed**。
- 受保護五層(analyzer/executor/dsp/domain/ops)禁 `importlib` 後,全專案原始碼掃描沒有任何合法用途被擋;`import importlib`、`from importlib import import_module`(含別名)、`import importlib.util` 四種寫法在五份 `ruff.toml` 下均正確噴 `TID251`。
- 揭露旗標與雜湊格式的拒絕都寫在 `HiddenSetProvenance.__post_init__`(`src/rtb/eval/scoring.py:116-124`),frozen dataclass 沒有其他管道改欄位(`dataclasses.replace` 一樣會重新觸發 `__post_init__`),兩項檢查確認是在建構當下生效,不是事後才驗。
- x1-2、x1-3、x1-5、arch-1 四條新防線,以及 x1-1 裡雜湊格式那一半,在 `/tmp/rtb-p10i2-audit2`(已刪除)逐一改掉關鍵行後全數翻紅,還原後對應子集重跑全綠。
- x1-1「至少一筆經過候選」那一半的可繞過性,用同一份 `/tmp` 副本手工組出的 `ScoredCase` 序列重現(五格各 72~73 筆 `CODE_RULE`、1 筆 `CANDIDATE`),`production_report` 接受、`decide_adoption` 判定 `adopt=True` 且五格全部驗證通過,即發現一的具體重現。
