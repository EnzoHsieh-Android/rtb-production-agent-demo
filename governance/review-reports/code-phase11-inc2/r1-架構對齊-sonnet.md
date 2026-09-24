severity: major

**發現 1:tests/dsp/test_store.py 新測試沒有沿用檔案既有的 `@pytest.mark.parametrize("action", ...)` 寫法,另開一條手動迴圈 + 新 fixture 達到同一目的**
severity: major
blocking: 是
引句:「for action, params in WRITE_ACTION_PARAMS.items():」
file: `tests/dsp/test_store.py:139`(既有 `test_a_future_expected_version_is_rejected_not_only_a_stale_one` 用 `@pytest.mark.parametrize("action", sorted(WRITE_ACTION_PARAMS))`)、`tests/dsp/test_store.py:157`(既有 `test_every_write_action_rejects_a_stale_expected_version` 同樣寫法)、`tests/dsp/test_store.py:34`(新增的 `store_factory` fixture)、`tests/dsp/test_store.py:67`(新測試位置)

重現:在複本 `/tmp/p11i2-arch` 裡把新測試改寫成既有的 `@pytest.mark.parametrize("action", sorted(WRITE_ACTION_PARAMS))` + 既有 `store` fixture(不新開 `store_factory`),放到 `WRITE_ACTION_PARAMS` 定義之後(跟另外兩支既有的參數化測試同一區),執行:
```
PYTHONPATH=src .../python -m pytest /tmp/test_store_experiment2.py -k test_every_write_action_with_the_same_key -q
```
結果 `2 passed`,行為與新增的手動迴圈版本完全等價,而且不必新開 `store_factory` fixture(既有的 `store` fixture本來就是 function-scope,每次 parametrize 呼叫都會拿到全新的資料庫檔)。另外驗證了把改寫後的測試留在原本位置(緊接在 `test_same_key_same_payload_...` 之後、`WRITE_ACTION_PARAMS` 定義之前)會在收集階段直接 `NameError: name 'WRITE_ACTION_PARAMS' is not defined`——這說明作者選擇手動迴圈很可能只是為了把新測試擺在語意相鄰的位置,而不是既有參數化寫法做不到。

建議:改用 `@pytest.mark.parametrize("action", sorted(WRITE_ACTION_PARAMS))` + 既有 `store` fixture,把測試移到 `WRITE_ACTION_PARAMS` 定義之後、跟另外兩支同樣以此表參數化的測試放在一起(或把 `WRITE_ACTION_PARAMS` 提到檔案更前面);移除新增的 `store_factory` fixture 與其計數器。這樣同一個檔案裡「逐一動作驗證」只有一種寫法,而且每個動作各自是獨立的測試節點,某個動作斷言失敗不會連帶讓迴圈裡後面的動作完全不被跑到看不出來。

**發現 2:tools/claim_hashes.py 的命令列慣例跟同目錄的 tools/verify_claims.py、tools/mypy_sarif.py 不一致(直接照抄後者的呼叫方式會壞掉)**
severity: minor
blocking: 否
引句:「python -m tools.claim_hashes claims/」
file: `.github/workflows/ci.yml:38`(`python tools/verify_claims.py claims/`,repo 裡唯一的既有 CLI 接線範例)

重現:在複本裡分別執行
```
python tools/claim_hashes.py claims/        # 依樣照抄 verify_claims.py 的呼叫方式
```
得到 `ModuleNotFoundError: No module named 'tools'`,結束代碼 1(不是文件承諾的「0 印完 / 2 讀不懂或參數錯」任一種);而
```
python -m tools.claim_hashes claims/
```
才會照文件說的印出「沒有雜湊跟現況不一樣的檔」並回 0。原因是 `claim_hashes.py` 用 `from tools import verify_claims` 重用驗證器的 `read_json`/`path_problem`/`EXIT_UNDECIDABLE`(這點做得對,沒有另外寫一套讀檔或雜湊邏輯,雜湊那行 `hashlib.sha256(...).hexdigest()` 跟 `verify_claims.py` 內部 `_sha256` 與 `closure_problems` 本來就重複的寫法一致,不算另立門戶),但這個 import 只有用 `-m` 從 repo 根執行才會成功;而 `verify_claims.py`、`mypy_sarif.py` 都是自足腳本,可以直接 `python tools/<檔名>.py` 執行。這個差異在檔案自己的 docstring 與 Phase 11 計劃(`S817`)裡都有明講,不是被藏起來的地雷,只是跟同目錄兩支既有工具的呼叫慣例不同,習慣打 `python tools/xxx.py` 的人會踩到一個沒被承諾過的結束代碼與未收斂的 traceback。

建議:可留白不改(已有文件講清楚),但若要跟家族慣例對齊,可以在 `tools/claim_hashes.py` 底部補一段對 `python tools/claim_hashes.py` 直接執行時的防呆(例如偵測到匯入失敗就印清楚訊息、回 `EXIT_UNDECIDABLE`),或者在檔案開頭以 `sys.path` 修正相容兩種呼叫方式,讓結束代碼落在文件承諾的 0/2 之內而不是未預期的 traceback + exit 1。

其餘檢查:驗證器第 5 步(`injection_problems` 及其輔助 `_test_function`/`_fixtures`/`_used_functions`/`_mentions`/`_asserts`)沿用既有的 `_parse_code`/`_sole_statement`/`_opaque_binding` 等 AST 基礎設施,沒有匯入產品程式、沒有另立一套讀檔或符號解析邏輯,寫法跟第②③④步一致,未發現架構分歧。`tools/claim_hashes.py` 的結束代碼語意(0/2)、`argparse` 用法、`run()`/`main()` 拆分皆完整複製 `verify_claims.py` 的既有慣例,唯一分歧就是發現 2 所述的呼叫方式。

2 條,blocking 1。
