severity: major
以下是完整報告全文(依格式規則產出,供原樣存檔):

severity: major

### 1. ValidatedCells 拿掉 dataclass 後手刻的 __setattr__/__eq__/__hash__/__repr__/__copy__/__deepcopy__,在專案唯一同類先例裡都沒有,也沒有任何呼叫端真正需要

severity: major
blocking: 是

引句:「def __copy__(self) -> ValidatedCells:」

file: `src/rtb/executor/attempt_store.py:189`
file: `src/rtb/executor/attempt_store.py:192`
file: `src/rtb/executor/attempt_store.py:194`
file: `src/rtb/analyzer/policy.py:184`
file: `tests/analyzer/test_worth_check.py:272`
file: `tests/analyzer/test_worth_check.py:273`

觸發情境:後面階段(計劃已預告的評估套件採用函式)要再做一個「只給信任呼叫端建構」的型別時,照這次 `ValidatedCells` 的寫法抄樣板,而不是照專案裡唯一同類的既有先例 `ExecutorTransaction`/`ReadTransaction`(attempt_store.py:189-227)。

會出什麼錯的行為:r3 把 `ValidatedCells` 拿掉 `@dataclass(frozen=True)`、改成 `issuer` 必填參數、無條件檢查(policy.py:86-89)——這部分確實比照了 attempt_store.py 的先例,也修掉了 r2 指出的「多包一層工廠函式、`_issuer` 欄位有預設值」問題,是對的方向。但拿掉 dataclass 之後,patch 又手刻了 `__setattr__`(擋修改)、`__eq__`、`__hash__`、`__repr__`、`__copy__`、`__deepcopy__` 共六個 dunder(policy.py:95-111)。這整組在專案裡唯一的同類先例 `ExecutorTransaction`/`ReadTransaction` 裡完全沒有——那兩個類別同樣是「私有哨兵擋建構」,卻只有 `__slots__` 加兩個一般屬性(attempt_store.py:192、194),沒有覆寫相等、雜湊、複製或屬性設定,因為它們是可變的資源代理(`close()` 直接 `self._open = False`),用身分比較就夠,誰都不會去複製或雜湊一筆交易物件。`ValidatedCells` 現在卻被寫成一個要有完整值語意的不可變物件,但生產程式碼裡 `route()`/`explain()` 只讀 `allowed.cells`(一個 `frozenset`,本身就能比較、能雜湊,見 policy.py:184),從沒有地方對 `ValidatedCells` 物件本身做 `==`、`hash()`、`copy.copy()` 或印出它的 `repr()`。唯一用到這六個新 dunder 的地方,是這次 patch 自己新加的測試(tests/analyzer/test_worth_check.py:272-273 的 `issued == ...`、`hash(issued) == hash(...)`,以及同一段的 `copy.copy(issued) is issued`)——也就是這組機制是 patch 自己新加的測試在驗證 patch 自己新加的機制,不是既有測試或既有生產需求逼出來的。這等於在「限制建構只給信任呼叫端」這一類問題上,於專案唯一的既有先例之外另開了一套更重的第二種做法(手刻多個 dunder 去模擬 frozen dataclass 曾經免費附送的行為),而不是照抄先例的最小形狀。

建議修法:比照 `ExecutorTransaction`/`ReadTransaction` 的最小形狀——`issuer` 必填、無條件檢查這部分留著;拿掉 `__setattr__`、`__eq__`、`__hash__`、`__repr__`、`__copy__`、`__deepcopy__`,只留 `__slots__` 加一般屬性賦值(`self._cells = frozenset(cells)`);測試裡改用 `.cells`(一個 `frozenset`)本身做比較,不需要對 `ValidatedCells` 物件比相等或雜湊。之後如果真的有生產程式碼要比較兩個 `ValidatedCells`,再依那個實際需求加,不要現在就為了補齊 dataclass 曾經免費給的功能而預先加上。

### 2. Systems 筆記還留著「4 個評分格」,跟這次 patch 把 WorthCell 改成 5 格自相矛盾

severity: minor
blocking: 否

引句:「ANOMALY = "anomaly"」

file: `docs/rtb-production-agent-demo-knowledge/Systems/任務流程領域模型.md:76`
file: `docs/rtb-production-agent-demo-knowledge/Systems/任務流程領域模型.md:80`
file: `src/rtb/domain/worth.py:33`

觸發情境:下一個 session 的 AI 或人要改 `worth.py` 或接上真正的候選規則之前,先讀 Systems/任務流程領域模型.md「「值不值得加」判斷點的型別」一節,想知道評分格有幾格、各是什麼。

會出什麼錯的行為:這次 patch 把 `WorthCell` 加了 `ANOMALY = "anomaly"`(引句;src/rtb/domain/worth.py:33-38),評分格從 4 格擴成 5 格,程式與測試都已照 5 格改好(`test_the_rubric_gives_exactly_one_class_for_every_input` 斷言 `len(WorthCell) == 5`)。但同一篇 Systems 筆記裡,第 76 行(該節開頭的總覽句)仍寫「4 個評分格與歸格函式」,跟第 80 行(同一節、同一天寫的「歸格」細節句)講的「使用者 2026-09-24 裁定評分表加資料自洽後改成 5 格」自相矛盾。這正是 CLAUDE.md 自己點名的「同一篇筆記內部新舊打架、doctor 驗不出來」的情況:只讀到第 76 行總覽句的人會以為只有 4 格、漏掉「資料異常格」。

建議修法:把第 76 行的「4 個評分格」改成「5 個評分格」,跟第 80 行的敘述對齊。
