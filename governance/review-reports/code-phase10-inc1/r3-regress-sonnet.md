severity: minor
Third-round review complete. Full report below (verbatim, ready to save as-is):

---

severity: minor

## 總覽
這輪(61947ef..fa4559c)驗兩塊:第 2 輪 3 條修復逐條試攻拿不進去;5 格評分表另寫一支獨立窮舉腳本比對,6912 組可建構輸入全部跟計劃逐字對上,[S700] 沒候選不看格也還在。新開的唯一發現是 1 條 minor(pickle 回歸),不擋這輪放行。全套測試 `PYTHONPATH=src /Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest`:1783 passed。

## 第 2 輪 3 條驗收(以下已修乾淨,不留 severity 字樣)

**regress-1(`dataclasses.replace` 繞過已驗證清單)已修乾淨。** `ValidatedCells` 不再是 dataclass,改成普通類別、哨兵是建構式必填參數。
引句:「建好之後不能改、複製拿到的是同一個物件,所以從已簽發的清單也拿不到新的非空清單」
我在自己的臨時目錄用 PYTHONPATH=src 直接對 `policy.ValidatedCells` 實測:`dataclasses.replace(issued, cells=frozenset(WorthCell))` 因為它已經不是 dataclass 直接丟 `TypeError: replace() should be called on dataclass instances`;`copy.copy`／`copy.deepcopy` 都回傳同一個物件(`is` 比對為真);`setattr(none, "_cells", ...)` 與 `setattr(none, "cells", ...)` 都被 `__setattr__` 擋下丟 `AttributeError`;pickle 往返會在 `loads` 階段被同一個 `__setattr__` 擋下整個失敗(見下面新開的 minor,失敗但同樣拿不到偽造的非空清單)。以上手法都攻不進去。唯一拿得到的是刻意繞過的 `object.__setattr__(none, "_cells", frozenset(WorthCell))`——照裁示只記觀察:這是刻意繞過型別系統,不是「無意間」會踩到的路徑。

**arch-2(比照 `attempt_store.py` 的簽發者先例)已修乾淨。** 新版是普通類別、`__slots__`、建構式必填哨兵、直接比對,私有工廠 `_issue_validated_cells` 整支拿掉。
引句:「def __init__(self, cells: frozenset[WorthCell], issuer: object) -> None:」
比對先例 `src/rtb/executor/attempt_store.py:192-195`(`__slots__ = ("_open", "conn")`、`def __init__(self, conn: sqlite3.Connection, issuer: object) -> None:`、直接 `if issuer is not _EXECUTOR_TRANSACTION_ISSUER`)寫法一致。測試檔也已改成直接呼叫建構式 `policy.ValidatedCells(frozenset(WorthCell), ISSUER)`,不再繞工廠。

**arch-1(三支共用檢查改 TypeGuard、參數收 `object`)已修乾淨。**
引句:「def is_count_or_none(value: object) -> TypeGuard[int | None]:」
另外兩支同一套改法:`def is_finite_or_none(value: object) -> TypeGuard[int | float | None]:`、`def is_int_between(low: int, value: object) -> TypeGuard[int]:`,參數型別都從 `Any` 改成 `object`。比對既有先例 `src/rtb/domain/evidence.py:121-122`(`def _is_version_or_none(value: object) -> TypeGuard[int | None]:`)寫法相同。delta 裡新增的 `test_the_shared_whitelist_checks_are_type_guards` 用 `typing.get_type_hints` 機檢這三支的參數與回傳型別,測試綠。

## 5 格改動驗收

**評分表逐字比對:完全一致。** 沒有沿用 delta 測試檔自己寫的 `_expected_cell`,而是照計劃 `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase10評估與Jev決策點_計劃.md` 82–88 行的五條文字,自己另外寫一支獨立評分函式,對「狀態 × 曝光/點擊/轉換(-1、0、1、3、5、缺值)× 花費/營收(-1.0、0、1.0、缺值)」窮舉出 6912 組可建構的 `WorthInput`,逐筆跟 `worth.cell_of` 比對——0 筆不一致,5 格全部到得了。特別釘住的邊界例:
- 花費缺值(其餘欄位正常)→ `anomaly`,跟計劃「花費缺值或負數…花費異常只由合成集與直連判斷點的單元測試驗」一致。
- 曝光正、點擊零、轉換零 → `no_delivery`;曝光正、點擊零、轉換正(轉換多於點擊)→ `anomaly`,跟計劃 88 行的邊界例逐字對上。
- 暫停且異常(全部欄位負數)→ `paused`,不看資料異常,跟計劃「暫停中 → 不值得加(不看其他欄位,資料異常也一樣)」一致。

引句:「ANOMALY = "anomaly"」——`WorthCell` 加了這一格,值不跟既有四格衝突。
引句:「return i.clicks > i.impressions or i.conversions > i.clicks」——`is_anomalous` 的兩種「多於」跟計劃「點擊多於曝光、轉換多於點擊」一致,且只在先排除負數/缺值之後才比較大小(前面的 `assert` 只是幫型別窄化,不影響邏輯)。

**[S700] 決策函式對外行為不變:仍然成立。** `route()` 本身沒被這次 delta 動到,它的短路邏輯就是「沒有候選就不看格」的保證來源。
file: `src/rtb/analyzer/policy.py:184` —— `if candidate is None or cell_of(worth_input) not in allowed.cells:`。`candidate is None` 在 `or` 左邊,`decide()` 一路傳 `candidate=None`,Python 短路求值保證右邊的 `cell_of(worth_input)` 根本不會被呼叫,加了 `ANOMALY` 格、改了 `cell_of` 的判斷順序都摸不到這條路徑。`code_rule()`(現行規則)這次也完全沒被動到,一樣只看曝光、點擊是不是正數。全套測試裡 `test_extracting_the_worth_increase_check_keeps_every_decision_identical`(625 筆存下的決策結果、跟 main e8b26f6 雜湊比對)也還是綠的,佐證正式路徑行為真的沒變。

## 新發現(這輪唯一一條)

### 1. ValidatedCells 的 pickle 反序列化必炸,r2 版本沒有這個限制
severity: minor
blocking: 否
引句:「raise AttributeError("已驗證清單建好之後不能改")」
觸發情境:對任何 `ValidatedCells` 實例(含 `ValidatedCells.NONE`)做 `pickle.loads(pickle.dumps(x))`。
會出什麼錯的行為:`pickle.dumps` 能成功序列化,但 `pickle.loads` 重建物件時,Python 對有 `__slots__` 又沒有自訂 `__reduce__`/`__setstate__` 的類別預設用 `setattr()` 把每個 slot 寫回去,會打到這支類別無條件擋下所有寫入的 `__setattr__`,整個反序列化丟 `AttributeError: 已驗證清單建好之後不能改`。我在自己的臨時目錄用等價的 r2 版寫法(`@dataclass(frozen=True)`、沒有 `__slots__`)重現過:同一個物件可以正常 pickle 往返,因為沒有 `__slots__` 的 frozen dataclass 預設反序列化直接寫 `__dict__`、不經過 `__setattr__`,所以 r2 版不會炸。也就是說這是這輪改法(改用 `__slots__` + 一律擋寫)才引入的行為倒退,不是 r2 就有的既有限制。現在全庫 `src/`、`tests/` 沒有任何地方呼叫 `pickle`(`grep -rn "pickle"` 0 筆),不影響這輪要驗收的合約與現有測試,判 minor、不擋放行。
建議修法:若確定不會有呼叫端需要序列化 `ValidatedCells`,在類別註解補一句「不支援 pickle」定調成已知限制即可;若之後有需要,補一組 `__reduce__`(例如 `return (ValidatedCells, (self._cells, _VALIDATED_CELLS_ISSUER))`),讓反序列化直接呼叫建構式帶哨兵過檢查,不必經過會被擋的 `__setattr__`。

## 測試
`PYTHONPATH=src /Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest`(在 /Users/enzo/rtb-p10i1):1783 passed,無紅燈、無新跳過。

## 結論
第 2 輪 3 條全部修好,5 格改動跟計劃評分表逐字一致、[S700] 承諾不變。這輪唯一新發現是 1 條 minor(pickle 回歸,未使用、不擋),沒有 major 或 blocker,可以放行。
