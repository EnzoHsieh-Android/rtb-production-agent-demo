severity: major
<!-- 來源:codex exec --sandbox read-only,取 stdout 最後一次完整輸出;原文未改 -->

## F1 [S408] 可經允許模組的重新匯出替請求函式改名，掃描會假綠
severity: major
blocking: 是 — 違反 [S408]「請求函式只出現在兩支允許檔案」的合約，且守衛無法偵測實際的 POST 呼叫。

引句:「mentioned = (isinstance(node, ast.Name) and node.id == _REQUEST) or (」

掃描只辨認名稱仍為 `request_json` 的 `ast.Name`／`ast.Attribute`，匯入檢查也只管來源直接是 `rtb.httpclient`。但 `dsp_client` 已把該函式暴露成模組全域；閉包內任一模組可從它重新匯入並改名：

```python
from rtb.analyzer.dsp_client import request_json as send
send("http://dsp/campaigns/c1", "POST", {}, 1)
```

此處 `send` 仍是同一個共用請求函式，但三道檢查全部回報無違規。這不是動態匯入或反射，而是普通的靜態匯入別名，落在「防忘記」的威脅模型內。`_FORGETFUL["import_as"]` 只測直接從 `rtb.httpclient` 改名，沒有涵蓋重新匯出的路徑。

file: `tests/analyzer/test_boundaries.py:243`  
file: `tests/analyzer/test_boundaries.py:260`  
file: `tests/analyzer/test_boundaries.py:285`  
file: `src/rtb/analyzer/dsp_client.py:31`

重現：

```text
$ printf ... | PYTHONPATH=src:. python -c \
  '解析上述兩行並依序呼叫 _check_http_imports、_check_request_uses、_network_offenders'
[]
([], [])
[]
```

應讓掃描追蹤匯入綁定的來源，或至少禁止閉包內從兩支允許模組重新匯出／匯入 `request_json`，並加入這個變異案例。

第 2 輪的兩項修正本身已到位：「不在投放 + 版本已變」交叉列會在兩項順序互換時翻紅；`import_module` 的名稱、屬性與匯入別名也都依收貨決定採取從嚴判斷。兩支並行測試把第二份提案由 160 改成 140，仍保留原本的競態與版本衝突意圖；封閉列舉及 `BLOCK_TRIGGERS` 增列亦未削弱既有斷言。
