severity: minor

**發現一:驗證器把「跑證據測試」的呼叫方式從既有的「python -m <工具>」慣例換成「python -c 內嵌驅動碼 + pytest.main(plugins=...)」——查證後判斷是受環境隔離旗標逼出的必要手段,非隨意另立新做法,但引數陣列裡兩個語意不同的 `-c` 疊在一起,可讀性有退步**
severity: minor
blocking: 否
引句:「command = [sys.executable, "-E", "-s", "-c", RUNNER, str(recorded),」
引句:「code = pytest.main(sys.argv[2:], plugins=[recorder])\n」
file: `tools/verify_claims.py:644`(command 組裝)、`tools/verify_claims.py:59-73`(RUNNER 定義)、對照 `tools/mypy_sarif.py:155-160`(既有慣例:`[sys.executable, "-m", "mypy", ...]` 直接呼叫、只解析文字輸出,不內嵌驅動碼)

重現:
- 逐一 grep 全專案(tools/、tests/、src/)裡 `subprocess.run/Popen`、`"-c"`、`sys.executable` 的用法,確認生產工具碼(tools/ 底下,不含測試)只有這一處用 `python -c <內嵌腳本>` 去呼叫另一支工具的 API;既有的對照組 `tools/mypy_sarif.py` 走的是「`-m` 直接呼叫、解析純文字輸出」這條路,r1 版 verify_claims.py 本身也是 `[sys.executable, "-m", "pytest", "-c", PYTEST_CONFIG, ...]`(同一份指令,`-c` 只出現一次、且是 pytest 自己的設定檔旗標)。
- 逐一排除「更貼近既有寫法」的替代方案,確認換掉的理由站得住:
  1. 想用既有的 `-p <plugin 模組>` 機制(驗證器自己在 `plugin_modules`/`addopts_plugins` 也懂這個概念)取代 `-c` 內嵌碼——但子行程開了 `-E`(不讀任何 PYTHON 開頭的環境變數),`-p` 要能匯入到暫存目錄的 plugin 檔,幾乎只能靠 `PYTHONPATH`,而 `PYTHONPATH` 正是 `-E` 刻意要擋掉、也是同一份設計文件裡點名的外部注入管道(EVIL_PLUGIN/PYTEST_ADDOPTS 那條 pitfall),等於為了省一支 `-c` 又要開一個已知的洞。
  2. 想直接在驗證器自己的行程裡呼叫 `pytest.main(argv, plugins=[recorder])`,省掉子行程——但 `-E`/`-s` 是直譯器啟動時的旗標,沒辦法對「已經在跑的行程」補開,一定要另開一個乾淨的子行程,而 `plugins=` 這個帶狀態物件的參數本來就只能透過 Python API 呼叫,CLI 的 `-p` 只吃可匯入的模組名稱、吃不到活的 Recorder 實例——這正是 pytest 官方文件自己推薦的「用小驅動程式呼召 pytest.main(..., plugins=[...])」寫法,不是另立門派。
  綜合以上,換掉 `-m pytest` 直呼是被 `-E`/`-s` 這兩個安全旗標與「要讀 wasxfail 活物件」逼出來的,不算「本來可以沿用既有做法卻沒沿用」。
- 唯一站得住的殘留問題是可讀性:同一份 `command` 清單裡先出現 Python 直譯器自己的 `-c`(接內嵌腳本),接著又出現 pytest 的 `-c`(接 `pyproject.toml`),兩個 `-c` 語意完全不同,只能靠位置(sys.argv[1] 是紀錄檔路徑、sys.argv[2:] 轉交 pytest)分辨,日後有人照抄這段挪動參數順序容易挪錯。

建議:維持現有機制(已排除更貼近既有寫法的替代方案),但在 `command = [...]` 那幾行補一句註解明講「兩個 -c 不同義:前者是直譯器內嵌碼、後者原樣轉交給 pytest 當設定檔旗標,順序不能動」,降低未來誤改的風險。不需要動邏輯。

**發現二:測試裡新增「用 python -m venv 造一個 user-site 開著的直譯器」的手法,專案裡沒有先例——查證後判斷是必要手段**
severity: minor
blocking: 否
引句:「subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", "--without-pip",」
file: `tests/tools/test_verify_claims.py:767-780`(`_python_with_user_site`),對照全專案其餘測試裡的子行程用法(`tests/executor/test_crash_recovery.py`、`tests/analyzer/test_task_lease.py`、`tests/dsp/test_store.py`、`tests/analyzer/test_boundaries.py` 都只用 `[sys.executable, "-c", CHILD, ...]` 開一次性子行程做故障注入,沒有任何一處用 `python -m venv` 另造直譯器)

重現:
- grep 全專案(含 tests/)找 `venv`、`EnvBuilder`、`m", "venv` 等字樣,確認 `python -m venv` 造直譯器這個動作在本輪之前的測試套件裡完全沒出現過,是這份 delta 第一次引入。
- 實際跑了一段對照實驗確認「為什麼非造 venv 不可」:
  - 目前跑測試用的直譯器(`.venv/bin/python`,一般 venv、無 `--system-site-packages`)`site.ENABLE_USER_SITE` 是 `False`。
  - 讀 CPython `site.py` 原始碼(`site.venv()`)確認:只要 venv 的 `pyvenv.cfg` 寫 `include-system-site-packages = false`,`ENABLE_USER_SITE` 會被直接強制設成 `False`,跟 `PYTHONNOUSERSITE`、`sys.flags.no_user_site` 等環境設定完全無關,外部設不動。
  - 也就是說,要測「user site 裡的 usercustomize 能不能繞過清空的環境」這個情境,唯一乾淨、可攜的做法就是另外造一個 `--system-site-packages` 的 venv(讓它的 `pyvenv.cfg` 寫 `include-system-site-packages = true`,`ENABLE_USER_SITE` 才會維持 `True`),不是隨手就有更貼近既有測試風格的替代品。

建議:不需要改動;可以考慮在 `_python_with_user_site` 的 docstring 補一句「這是本專案測試套件第一次造 venv,原因見上述查證」,方便下一位維護者不必重新推導這段 CPython 行為。

2 條,blocking 0。
