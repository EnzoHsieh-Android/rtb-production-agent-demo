severity: clean

本輪只看架構對齊,鎖定三處:tests/dsp/test_store.py 的參數化是否回到既有寫法、雜湊輔助的防呆與結束代碼、故障注入走訪是否沿用 `_this_level`/`_OWN_SCOPE` 而非另立一套。三處都查了,沒有發現。

查了什麼:

1. **tests/dsp/test_store.py 參數化**:第 1 輪我報告過 major(手動迴圈 + 新開 `store_factory` fixture,另立一套跟既有 `@pytest.mark.parametrize("action", sorted(WRITE_ACTION_PARAMS))` 不同的寫法)。這輪比對 `governance/review-reports/code-phase11-inc2/r2-delta-hint.patch`(第 64–130 行)與 `tests/dsp/test_store.py` 現況:`store_factory` fixture 整個移除,新測試改成 `@pytest.mark.parametrize("action", sorted(WRITE_ACTION_PARAMS))` + 既有 `store` fixture,搬到 `WRITE_ACTION_PARAMS`(第 80 行)定義之後,緊接著另外兩支同表參數化的測試(`test_a_future_expected_version_is_rejected_not_only_a_stale_one` 第 127 行、`test_every_write_action_rejects_a_stale_expected_version` 第 145 行)之前(第 83–95 行)。跑了 `PYTHONPATH=src .../python -m pytest tests/dsp/test_store.py tests/tools/test_verify_claims.py -q`,301 passed,無殘留分歧寫法。

2. **雜湊輔助的防呆與結束代碼**:`tools/claim_hashes.py` 新增 `try: from tools import verify_claims / except ModuleNotFoundError: print(...); raise SystemExit(2) from None`,並在檔案最上方加 `sys.dont_write_bytecode = True`。手動驗證三種呼叫路徑:
   - `python -B -m tools.claim_hashes claims/`(正式用法)→ exit 0,無 `__pycache__`。
   - `python tools/claim_hashes.py claims/`(直接執行、舊呼叫慣例)→ 印「請在 repo 根用 python -B -m tools.claim_hashes claims/ 執行」,exit 2,無 `__pycache__`(第 1 輪 minor 建議的防呆已落地,結束代碼落在文件承諾的 2 而非未預期的 traceback)。
   - `python -m tools.claim_hashes claims/`(-m 但沒 `-B`)→ exit 0,`tools/__pycache__` 只留下 `claim_hashes.cpython-314.pyc`(python -m 編譯輔助本身,程式裡攔不到,協調者已接受),沒有 `verify_claims.cpython-314.pyc`(`sys.dont_write_bytecode` 確實擋下匯入驗證器產生的快取)。三種行為都跟檔案 docstring 承諾的一致,`SystemExit(2)` 對應 `tools/verify_claims.py:33` 的 `EXIT_UNDECIDABLE = 2`(此處匯入已失敗、拿不到 `verify_claims` 物件,只能寫死 `2`,不算另立一套結束代碼語意)。

3. **故障注入走訪**:比對 `r2-delta-hint.patch` 第 783–920 行與 `tools/verify_claims.py` 現況(第 774–974 行)。第 1 輪送審時 `_mentions`/`_asserts` 用的是原生 `ast.walk(function)`(不分作用域,會鑽進沒被呼叫的巢狀 def/lambda,也會被子字串命中),這輪改成新寫的 `_executed()` 產生器,但它直接複用既有的 `_OWN_SCOPE`(`tools/verify_claims.py:365`)常數判斷該不該往下鑽,跟 `_this_level` 同一套「碰到 `_OWN_SCOPE` 就不展開」邊界規則,不是另立一套作用域概念;`_test_function`/`_class_fixture_clash` 沿用既有的 `_sole_statement`;`_fixtures` 改回傳 `(dict, problems)` 元組,跟檔案裡 `dependency_closure`→`(seen, problems)`、`plugin_modules`→`(names, problems)` 的既有慣例一致,唯一呼叫點 `injection_problems`(第 913 行)也正確解構。`_executed` 之所以沒有直接呼叫 `_this_level`,是因為兩者目的不同(`_this_level` 認的是「這一層的名字綁定」,含推導式特例;`_executed` 認的是「這支函式本體實際會執行到的節點」,含 assert 訊息不算、常數假值分支不算),用途分岔、規則不同,寫成獨立產生器但共用 `_OWN_SCOPE` 邊界,屬於既有「多支小型專用 AST 走訪函式共用同一套邊界常數」的既有風格,未發現另立门户或跨層直呼產品程式碼。

0 條,blocking 0。
