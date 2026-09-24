severity: major

## 發現 1: 逾時後可能留下孫行程
severity: major
blocking: 是

引句:「+            popen.communicate(timeout=STOP_SECONDS)」

`_end_group` 送出 SIGTERM 後，只要直屬子行程退出、輸出管線關閉，就立刻 `return`，不再送 SIGKILL。若孫行程留在同一行程群組、忽略 SIGTERM 且不持有輸出管線，它會繼續執行；暫存專案卻已開始清除。這違反逾時要收掉整組行程的合約。

具體例子：證據測試啟動一個忽略 SIGTERM、輸出導向 `DEVNULL` 的 `sleep 60` → 逾時後 pytest 與 sleep 都應結束 → pytest 結束後 `_end_group` 提早返回，sleep 仍存活。

重現方式：在 `/tmp/外家-codex-p12i3` 複本中，將逾時測試的孫行程改為忽略 SIGTERM 並關閉標準輸出、錯誤輸出，維持原本的 3 秒逾時，再檢查記錄的 PID。測試入口可見 `file: `tests/tools/test_forgery_comparison.py:63``。

## 發現 2: 外部 pytest 選項讓比較表兩欄跑不同測試
severity: major
blocking: 是

引句:「+    env.pop("PYTHONPATH", None)」

「沒有驗證器」欄只清掉 `PYTHONPATH`，仍繼承 `PYTEST_ADDOPTS`；驗證器執行證據測試時則會清掉所有 `PYTEST_` 變數，見 `file: `tools/verify_claims.py:1147``。因此兩欄不一定使用設計要求的同一組證據測試。現有測試只檢查前欄結束代碼為 0，沒有核對兩支測試都執行，見 `file: `tests/tools/test_forgery_comparison.py:27``。

具體例子：環境帶 `PYTEST_ADDOPTS="-k test_update"` → 兩欄都應使用 `test_update` 和 `test_pause` → 前欄只跑一支並顯示另一支被 deselect，驗證器欄仍按自身的乾淨環境處理兩支。

重現方式：在 `/tmp/外家-codex-p12i3` 複本中，以 `PYTHONPATH=src PYTEST_ADDOPTS="-k test_update" python -m tools.forgery_comparison` 產表，查看前欄 pytest 摘要，再對照驗證器的證據測試環境。

搬家核對：抽出的 14 項共用定義之 AST 內容與舊版相同；公開別名仍指向原函式，保留底線函式名的折衷可接受。唯讀環境未重新執行 278 項測試收集清單。

2 條,blocking 2。