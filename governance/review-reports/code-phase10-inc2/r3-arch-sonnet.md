severity: major
已完成稽核。判了 r3-delta.patch 四個修正項目(正式報告經過候選的檢查、雜湊格式、揭露旗標 bool、五層 ruff.toml 加 importlib 禁令與邊界掃描),逐一跟既有寫法比對:

- **正式報告經過候選的檢查**(`production_report` 新增的 `untouched` 檢查、`decide_adoption` 的同格比對修正):只用既有的 `RoutePath`/`WorthCell` 型別與既有的 dataclass 驗值風格,沒有新寫法。
- **揭露旗標 bool 檢查**(`type(self.candidate_predates_disclosure) is not bool`):跟 `src/rtb/executor/attempt_store.py:247` 既有的 `type(self.source) is not Source` 是同一種「凍結 dataclass 裡用 `type(x) is not Y` 做嚴格型別檢查」寫法,一致。
- **邊界掃描**:`_eval_imports` 這次擴充「認得任何動態匯入呼叫、不只認字面常數」與新增 `dunder_variable`/`import_module_variable` 探針,是延續同一套「ruff TID251 逐層擋 + AST 掃描補動態匯入」防線的漸進強化(跟 `tests/ops/test_ops_boundaries.py` 歷來用「代碼審第 1、2 輪」逐步補動態取用漏洞的方式相同),仍是同一套掃描,沒有新架構。
- **雜湊格式檢查**:發現落差,見下方第 1 條(major)。
- **五層 ruff.toml 的 importlib 禁令**:發現落差,見下方第 2 條(major)。

以下是完整報告全文:

---

severity: major

### 1. `HiddenSetProvenance` 的雜湊格式檢查沒有沿用 `domain/evidence.py` 既有的 `HASH_PATTERN`/`_is_hash`,自己另刻一份逐字相同的正則

severity: major
blocking: 是

引句:「_SHA256 = re.compile(r"[0-9a-f]{64}")」

file: `src/rtb/eval/scoring.py:30`、`src/rtb/eval/scoring.py:121`、`src/rtb/domain/evidence.py:21`、`src/rtb/domain/evidence.py:84`

觸發情境:之後有人要調整「雜湊要驗成什麼格式」這條規則(例如證據雜湊改用別的長度、或決定要不要接受大寫十六進位)時,只會想到改 `domain/evidence.py` 的 `HASH_PATTERN`/`_is_hash`。

會出什麼錯的行為:`eval/scoring.py` 新增的 `_SHA256 = re.compile(r"[0-9a-f]{64}")` 與 `HiddenSetProvenance.__post_init__` 用它做的 `isinstance(..., str) and _SHA256.fullmatch(...)` 判準,跟 `domain/evidence.py` 既有的 `HASH_PATTERN = re.compile(r"[0-9a-f]{64}")` 與 `_is_hash` 逐字相同。`eval` 套件的 `src/rtb/eval/ruff.toml` 並沒有禁止匯入 `rtb.domain`(只擋 `sqlite3`、`rtb.sqlitekit`、`rtb.executor`、`rtb.dsp`、`rtb.ops`),而且 `eval/scoring.py` 本來就已經匯入 `rtb.domain.worth`,不是不能重用。`domain/_checks.py` 檔頭明講這支模組存在的理由就是「同一個規則只有一份定義,免得各模組改了一處漏另一處」——這正是同一輪代碼審(r2 架構席)在 `_finite_nonnegative` 重寫 `is_plain_number` 一案已經指出並要求改成呼叫既有判準的問題,`_finite_nonnegative` 這處這輪確實已修好(改呼叫 `is_finite_or_none`),但同一份 patch 在雜湊格式檢查上又重犯一次同一種「另刻一份共用判準」的問題。

建議修法:重用 `domain/evidence.py` 的 `HASH_PATTERN`(或把它,連同一支 `is_hash`/`is_sha256_hex` 判準,抽到 `rtb.domain._checks` 這個共用層),`eval/scoring.py` 直接匯入,不要自己另刻 `_SHA256`。

### 2. 領域層的 importlib 新禁令跟既有 `importlib.import_module` 禁令重疊,同一個匯入會被兩條措辭不同的規則同時攔下

severity: major
blocking: 是

引句:「"importlib".msg = "不准動態匯入:會繞過匯入禁令與邊界掃描(Phase 10 增量 2 代碼審第 2 輪)"」

file: `src/rtb/domain/ruff.toml:27`、`src/rtb/domain/ruff.toml:32`

觸發情境:任何人在領域層寫 `from importlib import import_module` 或 `import importlib.util` 這類匯入。

會出什麼錯的行為:實測 `ruff check --config src/rtb/domain/ruff.toml`,`from importlib import import_module` 同時觸發兩條 TID251——新加的「不准動態匯入:會繞過匯入禁令與邊界掃描(Phase 10 增量 2 代碼審第 2 輪)」跟舊有的「領域層不得動態匯入,會繞過這裡的檢查」;`import importlib`、`import importlib.util` 也都被新的 `"importlib"` 條目單獨攔下。也就是說新加的 `"importlib"` 禁令範圍已經完全涵蓋舊的 `"importlib.import_module"`,舊條目現在是死重複,而且兩條訊息各自用不同措辭描述同一件「領域層不得動態匯入」的事(一個援引 Phase 10 增量 2 代碼審第 2 輪、一個是舊有的簡短說法)。其他四層(analyzer、executor、dsp、ops)加這條新禁令時都只新增一筆,沒有跟既有條目重疊的情形;只有領域層因為修法時沒有順手整併或移除舊條目,同一件事留了兩筆範圍重疊、措辭不同的 banned-api 條目,跟這份 patch 自己在其他四層立下的「一件事一條」寫法不一致。

建議修法:領域層的 `"importlib.import_module"` 舊條目直接移除(新的 `"importlib"` 禁令已完全涵蓋它),或者把兩邊訊息文字改成一致;不要讓同一條規則留兩筆重疊、措辭不同的 banned-api 條目。

---

除上述兩項外,r3-delta.patch 對 r2 架構席指定的四個標的(正式報告經過候選的檢查、雜湊格式、揭露旗標 bool、五層 ruff.toml 加 importlib 禁令與邊界掃描)裡,`Measure`/`OperationalLimits` 的數字檢查改用 `rtb.domain._checks.is_finite_or_none` 已確實收斂到既有共用判斷、揭露旗標 bool 檢查與邊界掃描的寫法也都跟專案既有做法一致,沒有帶出新落差。
