severity: blocker

### F1 依賴閉包演算法沒說清楚「套件屬性匯入」怎麼解析,實測會漏掉真實證據檔

severity: blocker
blocking: 是(直接讓 S810「少一個就擋」在真實檔案上失靈,不是效能或風格問題)
引句:「沿靜態匯入(檔案裡任何位置的 import,含函式內與相對匯入)在 src/rtb 內遞迴,得到依賴閉包」

- file: `/Users/enzo/rtb-3b/tests/kit/test_migration_failure_closes_the_connection.py:12-14` 三行分別是 `from rtb.analyzer import task_store`、`from rtb.dsp import store as dsp_store`、`from rtb.executor import inbox_store`,整支檔沒有任何一行用完整路徑(如 `from rtb.dsp.store import ...`)重複匯入這三個模組。
- 我在 `/tmp/p11exp/naive_closure.py` 寫了一支照 spec 文字最直白的實作(`from X import Y` 只解析 X、不檢查 Y 是不是 `X/Y.py` 這個檔),對這支測試檔跑閉包,結果只收到 `src/rtb/sqlitekit.py` 一個檔,完全漏掉 `store.py`、`task_store.py`、`inbox_store.py`——這三個正是這支測試實際在測的模組。
- 原因:`src/rtb` 底下除了 `rtb/__init__.py`、`rtb/eval/__init__.py`,其他子套件(`dsp`、`executor`、`analyzer`…)都沒有 `__init__.py`(namespace package,`/Users/enzo/rtb-3b/pyproject.toml` 的 `namespace_packages = true` 也印證這點),所以 `from rtb.dsp import store` 裡的 `store` 一定是「套件屬性匯入」(要接到 `rtb/dsp/store.py` 這個檔),不是模組裡定義的一個名字。spec 只寫「沿靜態匯入…遞迴」,沒交代 `ImportFrom.names` 裡的每個名字要試著當子模組解析——這正是最省事、最容易漏掉的字面實作方式,不是我編造的極端案例:上面那支測試檔就是現成反例。
- 附帶量測:用正確處理這個歧義的版本(`/tmp/p11exp/full_closure.py`)算冪等宣稱 scope(`server.py`+`store.py`)的依賴閉包,結果是 7 個檔(`capabilitykit.py`、`dsp/capability.py`、`dsp/errors.py`、`dsp/server.py`、`dsp/store.py`、`httpkit.py`、`sqlitekit.py`),規模不大,但前提是解析器要處理對這個歧義。

### F2 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 擋不住 -p、conftest 的 pytest_plugins、addopts 的 -p;PYTEST_ADDOPTS 環境變數完全不在雜湊範圍內

severity: blocker
blocking: 是(整個「驗證器自己跑才算數」與雜湊防重貼的天花板都靠「跑起來的東西可控」,這條直接繞過)
引句:「環境變數 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 不自動載入已安裝的外掛」

- 在 `/tmp/p11exp/plugintest` 建了一支只印一行字的假外掛(`myplugin.py`),用 pytest 9.1.1 實測:設定 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 後,`-p myplugin`(命令列)、conftest 的 `pytest_plugins = ["myplugin"]`、pytest.ini 的 `addopts = -p myplugin` 三種方式都照樣把外掛載進來(三次都印出 `MYPLUGIN LOADED`),自動載入(entry_points)以外的載入路徑完全不受這個環境變數影響。
- 更關鍵的是 `PYTEST_ADDOPTS` 環境變數本身:`PYTEST_ADDOPTS="-p myplugin" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest ...` 一樣載入外掛,而這個變數不是檔案,不在 scope/harness 的任何一個 sha256 裡,第 3 步的雜湊機制原理上就看不到它。conftest 的 `pytest_plugins` 至少被第 3 步的「conftest 強制算進 harness 雜湊」擋住(spec 64 行有講);但 `PYTEST_ADDOPTS` 這條路完全在雜湊視野外——CI 執行器或開發者 shell profile 常態設定這個變數(裝 coverage、xdist 之類)並非罕見,一個能把失敗改判通過的外掛(跟 spec 自己在 68 行提到的「一支 conftest 的報告鉤子能把失敗改成通過」是同一類手法)透過這條路徑,驗證器完全偵測不到、也不會在下一次改動時因雜湊不符而被審查員看見。
- spec 第 6 步只列了要「加」的旗標(`-o xfail_strict=true`、`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`…),完全沒提到子行程要不要繼承目前環境、要不要清掉或至少記錄 `PYTEST_ADDOPTS`,這是一個字面實作最容易漏掉的地方。

### F3 「每一段大小寫要跟目錄實際列出的檔名完全一樣」——最自然的實作只查最後一段,漏掉中間目錄段,macOS 過、CI 才炸

severity: major
blocking: 是(這條規則存在的理由就是防「本機過、CI 擋」,若只查到檔名這一段,規則等於沒生效在最常見的路徑寫錯情境上)
引句:「每一段的大小寫要跟目錄實際列出的檔名完全一樣」

- 在 APFS(這台機器)上先確認基礎行為:`os.path.exists()` 對大小寫不敏感(`myfile.py`/`MYFILE.PY` 對一個真實檔 `MyFile.py`都回 True),但 `os.listdir(parent)` 回傳的是實際大小寫,拿宣告字串去跟 `os.listdir(parent)` 的結果做精確字串比對可以正確抓到大小寫不符(`/tmp/p11exp/casetest`,`'myfile.py' in os.listdir('.')` 回 False,`'MyFile.py' in os.listdir('.')` 回 True)——這條路本身是能做對的,不是不可行。
- 但這只驗證了「最後一段檔名」。我在 `/tmp/p11exp/casetest` 建了目錄 `Real_Dir2/child.py`,宣告路徑寫成 `real_dir2/child.py`(目錄段大小寫故意寫錯):`os.path.exists()` 直接放行,而如果實作只對 `os.path.dirname(full)` 解出來的真實目錄跑一次 `os.listdir` 去核對 basename(`child.py`),不會回頭核對 `real_dir2` 這一段本身的大小寫,兩個檢查都回真——也就是說,若實作沒有「從 repo 根逐段下降、每一段都用 os.listdir(上一段) 核對」,而是用 `os.path.exists` 或 `os.path.dirname` 走捷徑,中間目錄段的大小寫錯誤在 macOS 上完全測不出來,恰好留到 CI(Linux,大小寫敏感)才炸——這正是規則想擋的那個場景本身。
- spec 沒有寫「逐段下降比對」的演算法字樣,只寫了結果要求,容易被實作成「先用 `Path.exists()` 判斷存在,再對 basename 補一次 listdir」這種捷徑。

### F4 tools/ruff.toml 的 banned-api 管不到 tests/tools/,「互不匯入」的其中一半在驗證器自己的測試裡沒人擋

severity: major
blocking: 是(S808 明講驗證器不應匯入 rtb,而驗證器的測試被規定放在 tests/tools/,這一半的機械守衛剛好照不到那裡)
引句:「新開 tools/ruff.toml 用 banned-api 禁 tools 匯入 rtb」

- 在 `/tmp/p11exp/rufftest_tools` 複刻專案的目錄型 ruff.toml 慣例(仿 `/Users/enzo/rtb-3b/src/rtb/analyzer/ruff.toml` 的寫法),`tools/ruff.toml` 裡 `banned-api` 禁 `rtb`,用真的 ruff 0.16.8 實測:對 `tools/verify_claims.py` 裡 `import rtb.dsp.store` 或 `from rtb.dsp import store`,TID251 都正確擋下;但對放在 `tests/tools/test_verify_claims2.py` 裡一模一樣的 `import rtb.dsp.store`,`ruff check --select TID251` 回「All checks passed!」——完全沒擋。
- 原因是 ruff 的設定解析是「從檔案所在目錄往上找最近的 ruff.toml/pyproject.toml」,`tests/tools/` 不在 `tools/` 目錄底下、也不是它的子孫目錄,兩邊是平行目錄,所以 `tests/tools/` 底下的檔案只吃得到根目錄 `pyproject.toml`,吃不到 `tools/ruff.toml`;這一點在既有專案的分層 ruff.toml(`src/rtb/analyzer`、`domain`…)裡本來就成立,因為那些禁令只需要管自己目錄底下的正式碼,沒有「測試放在另一棵目錄樹」這種情況。
- spec 69 行講的「rtb 那一側…另用一支語法樹掃描測試擋 rtb 匯入 tools」只提到反方向(rtb 匯入 tools),沒有提到「tools 那一側如果測試放在 tests/tools/,ruff 管不到,要另外補」;結果是「tools→rtb 禁令」這一向,只保護了 `tools/verify_claims.py` 本體,沒保護到規定要放測試的 `tests/tools/`。

### F5 policy 範圍詞是固定五個字面詞的子字串比對,換個近義詞就繞過列舉強制

severity: major
blocking: 是(S803 的「policy 用了範圍詞卻沒有列舉」這道閘,靠近義詞換句話說就整個不觸發,而且是最自然的中文寫法、不需要刻意鑽營)
引句:「至少要有一個 enumerations(沒有登錄表可列舉,就要改寫宣稱、不准用這些詞)」

- 這道檢查只掃描 policy 句子裡有沒有出現「每一種」「所有」「全部」「任何」「一律」這五個固定字串(子字串比對,spec 62 行)。中文表達「涵蓋全部種類」的說法很多:「各種」「逐一」「全數」「通通」「逐項」「每個/每支/每筆」都是自然、常見的寫法,沒有一個在這五詞的清單裡。
- 具體例子:圖譜裡原本 Mock-DSP 那條合約的措辭是「每一種寫入動作版本不符都拒收」(spec 33 行前掃引用),如果作者改寫成語意完全相同的「各種寫入動作版本不符都拒收」,scope 詞偵測不到「各種」,S803 的「policy 有範圍詞卻沒 enumerations」這條閘完全不會觸發——不需要惡意規避,單純換一個更順口的量詞就沒事,而作者本來就有動機用不同措辭(避免同一份文件裡用詞太重複)。
- 這跟威脅模型「防粗心」直接相關:作者很可能不是故意繞,只是隨手換了個字,列舉強制就悄悄失效,而且沒有任何機械訊號告訴任何人這件事發生過。

### F6 harness 只固定盯 pyproject.toml 當 pytest 設定來源,pytest.ini/tox.ini/setup.cfg 一旦出現會整個蓋過去且不在雜湊範圍

severity: major
blocking: 是(pytest 設定來源改變不是靠雜湊守住的,是直接被硬編的假設排除在外)
引句:「harness 一定要含每支證據測試檔本身、從 repo 根到它所在目錄路上的每個 conftest.py、pyproject.toml(pytest 設定在這裡)」

- 用 pytest 9.1.1 實測設定優先序(`/tmp/p11exp/precedence`):只有 `pyproject.toml` 時,`[tool.pytest.ini_options]` 的 `addopts = "-m 'not slow'"` 生效(2 支測試只收集到 1 支);在同一個目錄加一份 `pytest.ini`(內容 `addopts =` 空字串)後,同樣的 `pyproject.toml` 完全被略過,原本被 deselect 的測試又跑回來(2 支全收)——確認 pytest.ini 一旦存在,`pyproject.toml` 裡的 `[tool.pytest.ini_options]` 整段失效,不是合併,是互斥的來源選擇。
- 目前 `/Users/enzo/rtb-3b` 確實只有 `pyproject.toml` 一份設定來源(`find . -maxdepth 3 -iname "pytest.ini" -o -iname "tox.ini" -o -iname "setup.cfg"` 沒有結果),所以現在還不會擋下什麼;但 spec 把「pytest 設定在這裡」寫死成只認 `pyproject.toml`,依賴閉包演算法(第 3 步)沒有把「檢查有沒有更高優先序的 pytest 設定檔出現」當成一個要主動偵測的項目。之後任何人(甚至某個裝進來的開發工具)在 repo 根丟一份 `pytest.ini`,pytest 的實際行為來源會整個切換,而驗證器的 harness 雜湊完全不會發現多了一個檔、也不會發現 `pyproject.toml` 的設定已經不算數了——這正是「程式看不到的限制」被沉默繞過的路徑,而不是文字比對可以擋下的錯字。

### 已讀,無 finding

- **讀檔規則(重複鍵、NaN/Infinity、超長整數、布林非整數)**:實測 `object_pairs_hook` 對巢狀物件(陣列裡的物件)一樣逐層觸發,重複鍵在最外層與巢狀層都能抓到;`parse_constant` 能擋 NaN/Infinity;Python 3.11+ 的整數字串轉換位數上限(預設 4300 位)本身就會對超長整數丟 `ValueError`,能被同一個 try/except 收斂成「清單讀不懂」;`isinstance(True, int)` 為真但 `type(True) is bool`,spec 文字本身已經正確點出這個陷阱(「JSON 的 true 在 Python 裡等於 1,不算整數」),沒有需要另外挑戰的地方。這部分「照字面實作」是做得到的,前提是整數型別檢查用 `type(x) is int` 而不是裸的 `isinstance`,但 spec 已經講清楚要排除布林,不算未查證宣稱。
- **conftest 鏈(本專案現況)**:`/Users/enzo/rtb-3b` 目前沒有 repo 根或 `tests/` 層級的 conftest.py,只有四個子目錄各自的 conftest.py(`tests/{analyzer,dsp,executor,ops}/conftest.py`);用 `pytest --debug` 對 `tests/dsp/test_store.py` 的一支測試實測,pytest 實際載入的 conftest 集合就是它路徑上的 `tests/dsp/conftest.py` 一個,跟 spec 描述的「路上的每個 conftest.py」一致,沒有同層其他目錄的 conftest 被誤載入的情形(pytest 的 conftest 收集本來就是逐路徑,不會拉平行目錄)。這部分在目前這個 repo 的結構下沒有落差,風險在 F6 已經另外指出(設定來源，不是 conftest 收集邏輯本身)。

最後一行總結:最嚴重等級 blocker,blocking 條數 6(F1、F2、F3、F4、F5、F6 全部 blocking:是)。
