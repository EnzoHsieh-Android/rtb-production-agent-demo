severity: major
Below is the full report as required by the format rules.

---

severity: major

### 1. `_checks.py` 新函式改用 `value: Any` 與純 `bool` 回傳,跟檔內既有函式的型別標注方式不一致
severity: major
blocking: 是
引句:「def is_finite_or_none(value: Any) -> bool:」
file: `src/rtb/domain/_checks.py:17`
file: `src/rtb/domain/_checks.py:22`
file: `src/rtb/domain/_checks.py:47`
file: `src/rtb/domain/_checks.py:51`
file: `src/rtb/domain/evidence.py:123`

觸發情境:任何人要在 `_checks.py` 裡新增或修改一支「缺值就放行、有值就驗型別」的邊界檢查函式,照這次增量新加的三支函式(`is_int_between`、`is_count_or_none`、`is_finite_or_none`)抄樣板。

會出什麼錯的行為:`_checks.py` 檔頭明講這支檔案的判斷函式「用 TypeGuard 標明『通過之後是什麼型別』,讓型別檢查(mypy)能驗證邊界判斷」,檔內既有的四支函式 `is_plain_int`、`is_plain_number`、`is_id`、`is_aware` 全部把參數標成最寬的 `object`、回傳標成對應的 `TypeGuard[...]`。這次新增的三支函式裡,`is_int_between` 還維持 `TypeGuard[int]`,但把參數改標成 `value: Any`;`is_count_or_none`、`is_finite_or_none` 更進一步連回傳型別都改成純 `bool`,不再用 `TypeGuard`。這不是把舊寫法原地照搬——這三支函式是從 `dsp_client.py` 裡本來已經是 `Any`/`bool` 標注的私有函式（`_is_int_between`、`_is_count_or_none`、`_is_finite_or_none`)整支搬過來,搬遷時沒有跟著落地檔案的既有慣例調整型別標注,等於在同一支檔案裡新開一種「用 `Any` 繞過 mypy 邊界檢查、用 `bool` 放棄型別窄化」的第二種寫法。而且這不是沒有先例可比:網域層唯一同構的「缺值或整數」判斷 `evidence.py:123` 的 `_is_version_or_none(value: object) -> TypeGuard[int | None]`,做法跟 `is_count_or_none` 要驗的東西幾乎一樣(缺值或整數、有上下限),卻是用 `object` + `TypeGuard[int | None]`,證明「`_or_none` 判斷也該用 TypeGuard」在網域層本來就有先例,新函式沒有照著做。

建議修法:三支新函式的參數改回 `value: object`(跟檔內其餘函式與 `evidence.py:123` 的 `_is_version_or_none` 一致);`is_count_or_none` 改回傳 `TypeGuard[int | None]`,`is_finite_or_none` 改回傳 `TypeGuard[int | float | None]`,讓這支共用檔案內的判斷函式全部維持同一套「用 TypeGuard 讓 mypy 驗證邊界」的寫法,而不是搬進來的地方各自保留原本的標注習慣。

### 2. `ValidatedCells` 的私有哨兵多包一層私有工廠與預設欄位,跟既有的私有哨兵先例是不同構造
severity: major
blocking: 是
引句:「def _issue_validated_cells(cells: frozenset[WorthCell]) -> ValidatedCells:」
file: `src/rtb/executor/attempt_store.py:186`
file: `src/rtb/executor/attempt_store.py:194`
file: `src/rtb/executor/attempt_store.py:195`
file: `src/rtb/executor/attempt_store.py:208`

觸發情境:後面階段要再加一種「只給信任呼叫端建構,一般呼叫端不能直接建」的型別(這個增量的計劃筆記已經預告有評估套件的採用函式要用),照這次 `ValidatedCells` 的寫法抄樣板。

會出什麼錯的行為:專案裡已經有「私有哨兵擋建構」的先例——`attempt_store.py:186`/`208` 的 `_EXECUTOR_TRANSACTION_ISSUER`/`_READ_TRANSACTION_ISSUER`,做法是把哨兵當成建構式(`__init__`,`attempt_store.py:194`/`216`)的**必填**參數,直接在建構式裡用 `is not` 比對(`attempt_store.py:195`/`217`),信任呼叫端匯入哨兵常數後直接傳進建構式,沒有另外包一層工廠函式,哨兵值也不留存成物件欄位。這次 `ValidatedCells`(`src/rtb/analyzer/policy.py:75-95`)是不同構造:`_issuer` 是**有預設值 `None`** 的 dataclass 欄位(`field(default=None, repr=False, compare=False)`),擋建構的判斷放進 `__post_init__` 且只在 `cells` 非空時才生效,另外又包了一支模組函式 `_issue_validated_cells` 讓信任呼叫端呼叫,而不是像先例一樣直接把哨兵常數傳進建構式。三個地方都跟既有先例不同(欄位是否必填、檢查放在哪裡、有沒有工廠函式包一層),是同一個「限制建構」問題的第二種做法,不是照抄既有先例做出來的最小改法——先例的做法(哨兵當必填參數、直接比對、無工廠)同樣能撐住「正式路徑要有一個安全的空清單、非空清單只能由信任呼叫端建」這個需求,不需要多出「預設值 + 條件式檢查 + 額外工廠函式」這三樣先例沒有的機制。

建議修法:比照 `attempt_store.py` 的既有先例,拿掉 `_issue_validated_cells` 這層工廠,把 `_issuer` 改成建構式必填參數、`__post_init__` 一律比對(不分 `cells` 是否為空),`ValidatedCells.NONE` 這個唯一的空清單定義處直接傳入 `_ISSUED`;信任呼叫端(評估套件的採用函式、測試)一樣匯入 `_ISSUED` 常數直接建構,不再需要一支額外的模組函式。
