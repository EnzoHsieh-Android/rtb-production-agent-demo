severity: major
<!-- 來源:codex exec --sandbox read-only,取 stdout 最後一次完整輸出;原文未改 -->

## F1 靜態閉包刻意漏掉會自動執行的套件初始化檔
severity: major
blocking: 是 — [S408] 的守衛會假綠，分析行程可新增未經允許的 POST 呼叫而不被發現。

引句:「if path.stem != "__init__"]」

`analyzer_import_closure` 明確排除 `rtb/analyzer/__init__.py`，也沒有把匯入路徑上的父套件初始化檔納入閉包。但 Python 匯入任何 `rtb.analyzer.*` 模組時，都會執行存在的 `rtb/analyzer/__init__.py`。因此只要日後新增該檔並在裡面匯入 `request_json`、向 DSP 發 POST，[S408] 仍會通過，直接違反「請求函式只出現在兩支允許檔案」的合約。

file: `tests/analyzer/test_boundaries.py:204`  
file: `tests/analyzer/test_boundaries.py:207`  
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md:213`

唯讀記憶體檔案系統重現：加入含 `request_json(..., "POST", ...)` 的 `rtb/analyzer/__init__.py` 後呼叫 patch 內的掃描器。

```text
package_init_offenders = []
```

應把分析套件本身以及每個被匯入模組沿途會執行的 `__init__.py` 納入閉包，並將此反例加入殺傷力測試。

## F2 傳遞依賴可繞過「只能經共用 HTTP 用戶端」限制
severity: major
blocking: 是 — 分析行程可經領域層 helper 直接送任意 HTTP POST，兩道靜態守衛卻同時假綠。

引句:「found, methods = _check_request_uses(module, tree)」

新增的閉包掃描在傳遞依賴中只檢查 `rtb.httpclient` 與 `request_json` 的出現；既有網路模組禁令則只掃 `src/rtb/analyzer/` 目錄。若 `flow.py` 匯入 `rtb.domain.write_helper`，而 helper 直接用 `urllib.request.urlopen(Request(..., method="POST"))`，新掃描看不到 `request_json`，舊掃描也看不到 analyzer 目錄外的 `urllib`。這是一般的「共用領域 helper 順手發請求」寫法，落在明定的防忘記威脅模型內，不需要動態匯入或反射。

file: `tests/analyzer/test_boundaries.py:111`  
file: `tests/analyzer/test_boundaries.py:113`  
file: `tests/analyzer/test_boundaries.py:272`  
file: `tests/analyzer/test_boundaries.py:277`  
file: `tests/analyzer/test_boundaries.py:326`

唯讀記憶體檔案系統重現：讓 analyzer 匯入一支以 `urllib` 直接 POST 的領域 helper，再呼叫 patch 內的掃描器。

```text
transitive_urllib_offenders = []
```

網路模組與動態匯入禁令必須套用到整個靜態匯入閉包，僅豁免受檢查的共用 HTTP 用戶端本身；殺傷力測試也應加入傳遞 helper 直接使用 `urllib` 的案例。

比例公式、護欄順序與 `not_permitted` 合併未見行為錯誤。兩支並行測試把第二份預算由 160 改成 140，仍保留原本的確定性交錯、單次 DSP 寫入及版本衝突斷言；封閉列舉與 `BLOCK_TRIGGERS` 的新增項也未削弱原測試意圖。指定 pytest 子集在此唯讀環境因沒有可用暫存目錄而無法啟動；上述兩個反例改以不落盤的記憶體路徑替身執行。
