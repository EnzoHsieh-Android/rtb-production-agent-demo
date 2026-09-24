severity: major

**1. repo 根的 conftest.py 和 src/、tests/ 以外的證據測試檔都不進依賴閉包,可以靜默改掉測試結果**
severity: major
blocking: 是
引句:「if rel in seen or rel.split("/")[0] not in CODE_ROOTS or not (root / rel).is_file():」
file: `tools/verify_claims.py:371`、`tools/verify_claims.py:385`
- 原因:`_conftests()` 會算出 repo 根的 `conftest.py`,`dependency_closure()` 卻因為第一段不是 src 或 tests,直接略過它。結果 `need_harness` 永遠不含根 conftest,它自己的匯入也不會被追。
- S810 寫的是「路上的 conftest.py」。根 conftest 在路上,pytest 以 `--rootdir .` 執行時也會載入它。
- 同一個原因:證據節點若放在 src/、tests/ 以外(例如 `scripts/test_x.py::t`),證據測試檔本身也不會被要求列進 harness。
- 重現:用小 repo(`/tmp/p11rev-corr/exp/e1_root_conftest.py`)。
  1. 先讓 `test_pause` 失敗,驗證器正確擋下:`(1, … test_pause 沒通過 …)`。
  2. 在 repo 根放一支 `conftest.py`,內容是測試檔裡的 EVIL_PLUGIN(報告鉤子把失敗改成通過),清單完全不動。
  3. 結果是 `(0, '通過:5 條宣稱,跑了 2 支證據測試全部通過')`。
- 建議:
  - 閉包在 root 層也收 `conftest.py`,並追它的匯入。
  - 證據測試檔一律強制進 need_harness,不看它在哪個根。
  - 或者直接規定證據節點必須在 tests/ 底下,不在就擋。
  - 補一支「根 conftest 沒列就擋」的測試。

**2. conftest 用 `pytest_plugins` 字串帶進的外掛模組不進閉包,改外掛內容不會擋**
severity: major
blocking: 是
引句:「"""檔案裡任何位置的匯入(含函式內、TYPE_CHECKING 區塊、相對匯入)。」
file: `tools/verify_claims.py:340`
- 原因:`imported_modules()` 只看 `ast.Import` 和 `ast.ImportFrom`。`pytest_plugins = ["tests.plugins.x"]` 是字串,裡面的模組不會列進 harness。
- 計劃只寫了「conftest 的 pytest_plugins 在 harness 雜湊裡」,這只保證 conftest 那一行改了會被看到,它指向的外掛檔之後再改就看不到了。pyproject addopts 裡 `-p tests.x` 也是同一個問題。
- 重現:`/tmp/p11rev-corr/exp/e2_plugins.py`。
  1. `tests/dsp/conftest.py` 加上 `pytest_plugins = ["tests.plugins.helpers"]`,helpers.py 先放無害內容,重算清單雜湊。這時失敗的 test_pause 正確擋下。
  2. 只把 `tests/plugins/helpers.py` 改成改結果的外掛,清單不動。
  3. 結果是 `(0, '通過:5 條宣稱…')`。
- 建議:
  - 閉包額外掃 conftest 與測試檔最外層的 `pytest_plugins` 字面字串(字串或字串 list/tuple),解析成模組後加入。
  - 讀不出字面值就擋。
  - pyproject addopts 的 `-p 模組` 也用同一套規則。

**3. 子行程環境沒清 PYTHONUSERBASE(user site),沒有 venv 的 Python(例如 CI 的 setup-python)可以從外面載入改結果的外掛**
severity: major
blocking: 是
引句:「CLEARED_VARIABLES = ("PYTHONPATH", "PYTHONSTARTUP")」
file: `tools/verify_claims.py:46`、`tools/verify_claims.py:454`
- 原因:計劃關掉的是「PYTHONPATH 指到外面的 sitecustomize」這條路。PYTHONUSERBASE 走的是一模一樣的路:user site 底下的 `usercustomize.py` 或 `.pth` 在啟動時執行,可以在清理環境之後再自己設 PYTEST_ADDOPTS。
- 用 venv 跑時 user site 是關的,所以本機測不到。CI 用 setup-python,沒有 venv,user site 預設開著。
- 重現:`/tmp/p11rev-corr/exp/e8_userbase.py`。
  1. 把 rtb 的 venv 複本改成 include-system-site-packages = true,讓 user site 打開,模擬 CI。
  2. 讓 test_pause 失敗,基線結果是 1。
  3. 設 `PYTHONUSERBASE=<外部目錄>`,在它的 site-packages 放 `evil.py` 和 `usercustomize.py`(設 `PYTEST_ADDOPTS=-p evil`)。
  4. 結果是 `(0, '通過:5 條宣稱,跑了 2 支證據測試全部通過')`。
- 建議:
  - 子行程改用 `sys.executable -E -s -m pytest …`:`-E` 忽略所有 PYTHON* 變數,`-s` 關掉 user site。PYTEST_ 開頭變數的清理照舊。
  - S815 的測試補一條 PYTHONUSERBASE 路線。這條測試要能在 user site 開著的直譯器上跑,否則測不到。

**4. `@pytest.mark.xfail(strict=False)` 的測試通過時,驗證器照樣印通過(S804「預期失敗卻通過」的字面沒擋住)**
severity: major
blocking: 是
引句:「"-p", "no:cacheprovider", "-o", "xfail_strict=true", "-q",」
file: `tools/verify_claims.py:490`
- 原因:`-o xfail_strict=true` 只改 ini 的預設值,標記上明寫的 `strict=False` 會蓋過它。非嚴格的 XPASS 在 JUnit 裡長得跟一般通過一樣,計劃自己也寫了這一點,所以 `judge_junit` 看不出來。
- 模組層寫 `pytestmark = pytest.mark.xfail(strict=False)` 也一樣。
- 重現:`/tmp/p11rev-corr/exp/e4_xpass.py`。
  - test_pause 加 `@pytest.mark.xfail(strict=False, reason='flaky')`,本體會通過,結果是 `(0, '通過…')`。
  - 對照組只寫 `@pytest.mark.xfail`,結果是 1。
- 建議:JUnit 分不出來,改從 pytest 自己拿結果。
  - 做法一:加 `-rX`,從輸出的 `XPASS` 行比對節點。
  - 做法二:由驗證器注入一支只讀的小外掛,在 `pytest_runtest_logreport` 把 `wasxfail` 記下來。
  - 做法三:用語法樹掃證據測試有沒有 `xfail(` 就擋。
  - 補一條 strict=False 的測試案例。

**5. 登錄表和 symbols 的「最後一次綁定」只看最外層的直接敘述;在 if、try、for 裡重綁,或對 list 登錄表 append,都會讀成舊值而通過**
severity: major
blocking: 是
引句:「"""每個名字在這一層最後一次被綁定的敘述;def、class、賦值、匯入都算綁定。"""」
file: `tools/verify_claims.py:288`、`tools/verify_claims.py:297`、`tools/verify_claims.py:422`
- 原因:`_last_bindings` 只走 `body` 的直接子敘述,區塊裡的賦值和匯入、`del`、for 目標、`.append/.extend/.add/.update` 都看不到。
- 計劃允許 list 和 set 當登錄表,但它們是可變的。第 4 步寫了「動態組出來的也擋」;第 2 步寫了「最外層定義之後又被重新指派也擋」。
- 重現(`/tmp/p11rev-corr/exp/e5_registry.py`、`e6_sym.py`):
  - 列舉:先寫 `WRITE_ACTIONS = ("update","pause")`,再用下面三種寫法讓執行期多出 "void"。三種全部得到結束代碼 0,"void" 沒有任何 covers 也照樣通過。
    - 後面接 `if True:\n    WRITE_ACTIONS = ("update","pause","void")`
    - 改成 list 後接 `WRITE_ACTIONS.append("void")`
    - `try: from rtb.kit import EXTRA as WRITE_ACTIONS`
  - symbols:在 `def act` 後面分別加 `try: from rtb.kit import ok as act`、`if True: act = None`、`del act`。`check_manifests` 三種都回 `[]`。
  - 常見的「try 匯入加速版覆蓋純 Python 版」就是這個寫法。
- 建議:
  - 對目標名稱,在模組最外層做完整的 `ast.walk`(不進 def、class 本體)。只要在唯一一次最外層賦值或定義之外還有任何綁定、`del`、或對它的方法呼叫(append/extend/add/update/insert/remove 等),一律擋。
  - 或者登錄表只收 tuple 和 frozenset。
  - 各補一條測試。

**6. Systems 家寫「寫成『每一種』會被驗證器當成宣稱範圍大於證據擋下」,實際上不會擋**
severity: major
blocking: 是
引句:「寫成「每一種」會被驗證器當成宣稱範圍大於證據擋下」
file: `docs/rtb-production-agent-demo-knowledge/Systems/宣稱驗證器.md:56`
- 原因:第 4 步是整份清單層級的聯集檢查:policy 有範圍詞、有列舉、所有證據的 covers 聯集涵蓋列舉,就過。它不知道範圍詞掛在哪一個子句。冪等那份已經靠版本測試 covers 了 update_budget 和 pause_campaign,再加一個「每一種」子句也照樣過。
- 家這樣寫,會讓之後改寬宣稱的人以為有機械防線。這正是本階段要防的「宣稱範圍大於證據卻亮綠燈」。
- 重現:在正式清單複本,把 idempotency policy 的「DSP 同一把冪等鍵最多套用一次」改成「DSP 每一種寫入動作同一把冪等鍵最多套用一次」,再跑 `check_manifests(ROOT, claims)`,結果是 `problems: []`。
- 建議:
  - 改家的措辭,寫明驗證器只查整份清單的聯集。「每一種」加在哪個子句,只能靠審查員分辨。
  - 或者在增量 2 把 covers 綁到 policy 子句(例如 evidence 帶子句編號)。

**7. CI 接線檢查擋不住 `shell: bash {0}` 加上後續指令的寫法**
severity: minor
blocking: 否
引句:「def test_every_way_of_making_the_claims_job_toothless_is_flagged(mangle):」
file: `tests/tools/test_verify_claims.py:830`
- 現況:目前的 ci.yml 會因為驗證器非零結束而紅,這一點確認過。
- 缺口:`ci_problems` 和 `claims_job_problems` 不看 `shell:`。改成下面這樣,驗證器失敗時步驟仍然綠。
  ```yaml
  - shell: bash {0}
    run: |
      python tools/verify_claims.py claims/
      echo done
  ```
- 這不在 S813 字面列的三項之內,但測試名稱宣稱「every way」。
- 重現:把上面的寫法丟進 `claims_job_problems`,回 `[]`。
- 建議:驗證器所在的工作不准出現 `shell:` 和 `defaults:`,而且驗證器指令必須是單行 `run:`。

**8. 清單型別怪值或範圍內的程式有語法錯時直接丟例外,沒有「擋下」或「無法判定」的訊息**
severity: minor
blocking: 否
引句:「if not isinstance(node, str) or not _is_strings(covers) or kind not in KINDS:」
file: `tools/verify_claims.py:144`
- 現象:兩種情況都不是靜默放行,CI 照樣會紅,但訊息只有 traceback,沒有 S811 規定的分類。
  - `"kind": ["unit"]` 會因為 list 不可雜湊丟 TypeError。
  - 閉包或登錄表裡的檔有語法錯,`ast.parse` 會丟 SyntaxError。
  - 兩者都結束代碼 1,只印 traceback。
- 重現:`/tmp/p11rev-corr/exp/e9_crash.py`,兩種情況分別印出 `(1, "TypeError: …unhashable type: 'list'")` 和 `(1, 'SyntaxError: invalid syntax')`。
- 建議:
  - kind 先確認是 str 再查集合。
  - 閉包和 read_registry 的 `ast.parse` 抓 SyntaxError 和 UnicodeDecodeError,轉成一條擋下原因。

**9. 合約綁定的測試沒蓋到合約字面的全部子句**
severity: minor
blocking: 否
引句:「def test_a_missing_file_or_symbol_is_blocked(repo, path, expected):」
file: `tests/tools/test_verify_claims.py:256`、`tests/tools/test_verify_claims.py:517`
- 這些都不是假陽性:每支測試斷言的內容確實有測到。只是合約綁定的那支沒蓋全。
  - S801 綁的測試只測檔案路徑,symbols 找不到定義放在另一支沒綁合約的測試。
  - S803 綁的測試沒測「登錄表讀不出字面常數」。
  - S810 綁的測試沒測「symbols 所在的檔」。
  - `test_the_files_of_symbols_enumerations_and_conftest_imports_are_in_the_closure` 的名稱有 enumerations,內容卻沒測列舉來源檔。
- 建議:把相關案例併進合約綁定的那支測試(或改綁),名稱和內容對齊。

**10. 範圍詞清單沒收「每一X」的寫法**
severity: minor
blocking: 否
引句:「SCOPE_WORDS = ("每一種", "每種", "每個", "每支", "每筆", "每項", "各種", "所有", "全部", "全數",」
file: `tools/verify_claims.py:36`
- 現象:清單收了「每一種」,但「每一個、每一支、每一筆、每一項、每條、每一條、每次」都不是現有詞的子字串,掃不到。例如「每一個寫入動作」不包含「每個」。
- 這些是最常見的同義寫法,不是刻意繞過。實作照抄了計劃的清單,所以這是計劃層級的缺口。
- 建議:加上這幾個「每一X」和「每條、每次」。

**查過、沒有問題的項目**
- JUnit 對應:
  - 同名測試在不同檔(classname 不同)。
  - 類別與巢狀類別的方法。
  - 參數化(`name[`)和空參數化(NOTSET 記成 skipped)。
  - setup 裡 skip(記成 skipped)和 teardown error。
  - 收集錯誤(結束代碼 4 或 2,都擋)。
  - pytest 9 內建 subtests(失敗會擋)。
  - 取消選取和結束代碼非零的補抓。
- 子行程:
  - 逾時時 killpg 會殺掉整組孫行程;另起新 session 的孫行程也不會卡住,因為 pytest 的 fd 擷取讓它拿不到管線。
  - PYTEST_ADDOPTS、PYTHONPATH→sitecustomize、自動載入外掛、tests/pytest.ini 這幾條都確實擋住。
  - PYTHONOPTIMIZE 不會讓改寫過的斷言失效。
- 五份正式清單:
  - 冪等清單的列舉是 `CAMPAIGN_WRITE_ACTIONS`,covers 對上計劃。
  - 其他四份沒有範圍詞,也沒有列舉。
  - 總曝險「擋下的都有紀錄」有 F7 斷言 write_stops 撐著。
  - S809 在目前版本通過;複本全套 103 支測試綠。
- CI:claims 工作確實會因為驗證器非零而紅,也沒有 needs、if、continue-on-error。

**附註**
- 我曾在複本 `/tmp/p11rev-corr` 跑過一次 `git -C /tmp/p11rev-corr checkout -- claims`(以及 `status`、`diff`),用來還原我在複本裡改過的清單。
- 複本的 `.git` 是指向原 worktree gitdir 的檔,所以這個指令可能動到共用 index 的 stat 快取。檔案內容和 HEAD 都沒變。
- repo 工作樹 `/Users/enzo/rtb-p11i1` 本身沒有任何改動。

10 條,blocking 6。
