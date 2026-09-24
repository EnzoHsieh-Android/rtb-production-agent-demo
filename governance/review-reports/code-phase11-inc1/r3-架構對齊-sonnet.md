severity: clean

我以「架構對齊」為鏡頭審了 governance/review-reports/code-phase11-inc1/r3-delta.patch,對照 tools/mypy_sarif.py 與 tests/test_static_wiring.py 既有寫法,逐項核對下列幾類寫法是否跟專案既有慣例一致,結果沒有發現符合末輪紀律(引入第二種做法、跨層直呼)的 major。

查了什麼:

1. 內建外掛名單寫成常數加一致性測試(`PYTEST_BUILTIN_PLUGINS` + `test_the_builtin_plugin_list_matches_the_installed_pytest`,`tools/verify_claims.py:59-65`、`tests/tools/test_verify_claims.py:1229-1232`)。既有先例確認存在:`tests/test_static_wiring.py:16` 的 `ANALYZERS`、`tests/test_static_wiring.py:23-27` 的 `EXPECTED_STEPS`、`tests/test_static_wiring.py:18` 的 `LINT_TOOL_PREFIX`,都配了「守衛的守衛」測試回頭核對常數跟現實一不一致(如 `test_the_expected_analyzers_are_actually_configured`、`test_every_configured_analyzer_has_a_known_lumos_wiring_rule`)。r3 的寫法是同一套架構的延伸,不是新引入的做法。唯一不同點是資料來源改成向第三方套件的私有模組 `_pytest.config.builtin_plugins` 取值(`tests/tools/test_verify_claims.py:1230`),這是全庫第一次這樣做(既有先例都是讀 `pyproject.toml`/`.lumos/lint.json` 這類專案自己的設定檔)。我認為這屬於資料來源上的差異,而非「常數+一致性測試」這個架構模式本身的分裂,加上 pytest 沒有公開 API 可以拿到內建外掛全集,判斷為 minor,不算 major。

2. 錯誤訊息與擋下原因的寫法:新增訊息(如 `symbols {reference} 所在的模組有{opaque},驗證器不展開、一律擋`、`{PYTEST_CONFIG} 的 pythonpath 只准 {PYTHONPATH_ALLOWED}`、`{importer} 用到的 {rel} 在 src、tests 以外,驗證器不算它的閉包`)延續既有的「要是/不准/只准」句式與「原因+說明」結構(對照 `tools/verify_claims.py:269`、`286-303` 等既有寫法),用字一致,沒有另創一套訊息語彙。

3. `closure_problems`(`tools/verify_claims.py:616-635`)裡對 `declared`(manifest 手動列的 scope/harness)另外用一段 list comprehension 產生「在 src、tests 以外」訊息,沒有直接呼叫既有的 `_outside_code()`(它是給 `dependency_closure`/`addopts_plugins` 解析出來的檔案用的,語意不同:「宣告的」vs「用到的」)。這是同一訊息模式下的兩個獨立呼叫點,不是新架構,只能算 minor 的小重複。

4. 測試面的新增輔助函式(`_plugin_in_tools`、`_plugin_at_root`、`_test_imports_tools`、`_declared_outside`、`_declared_dynamic_entry`,`tests/tools/test_verify_claims.py`)與既有 `_omit`、`_symbol_file`、`_root_conftest`、`_plugins_in_conftest` 等命名、簽名(接 `repo` fixture、回傳 `write_manifests` 的 kwargs dict)完全同款,`load_verifier()` 的用法(`tests/tools/test_verify_claims.py:1232`)也是沿用既有既有的模組內省慣例(對照既有 `tests/tools/test_verify_claims.py:667`)。

5. 驗證器本身仍守著檔首註解宣告的「不匯入產品程式,讀程式一律用語法樹」原則(`tools/verify_claims.py:11`),新增的 `_opaque_binding`、`_accessor_chain`、`_touches_attribute` 全部走 AST,沒有出現直接 import `src/` 底下程式碼的跨層呼叫。

6 條,blocking 0。
