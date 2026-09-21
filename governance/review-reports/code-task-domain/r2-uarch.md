severity: minor

# r2 架構對齊席(uarch)報告

整體:修復沒有引入第二種做法或跨層直呼,依賴方向仍是 domain 內部往內。以下三條都是可讀性與一致性的小事,不阻擋。

## Finding 1
severity: minor
blocking: 否 只是「同一類檢查有三個小變體」,沒有造成行為分歧,也沒有跨層。
- 位置:`src/rtb/domain/_checks.py:12`、`src/rtb/domain/metrics.py:65`、`src/rtb/domain/evidence.py`(`_is_positive_finite`)
- 問題:`_checks.is_plain_int` 是共用版;metrics.py 沒有用它,仍有自己的 `_is_number`(int|float 且非布林);evidence.py 又新增 `_is_positive_finite`,內嵌同樣的「非布林數字」判斷。「非布林數字」這個規則現在有 metrics 與 evidence 兩份寫法。
- 走法:比對三處,`_is_number` 與 `_is_positive_finite` 開頭的 `isinstance(x, int|float) and not isinstance(x, bool)` 是同一句。
- 引句:「+def _is_positive_finite(value: object) -> bool:」
- 建議:`_checks` 補一個 `is_plain_number`,metrics 與 evidence 都改用;或在 `_checks` 的 docstring 註明 metrics 為何不併入。`_checks.py` 命名與放置符合慣例(領域目錄內、底線表示套件內部;dsp 不依賴它並已在 docstring 說明,有意的分隔,不算第二種做法)。

## Finding 2
severity: minor
blocking: 否 屬可讀性,行為由測試覆蓋。
- 位置:`src/rtb/domain/proposal.py:191-198`(`_children`)與 `_size_error`
- 問題:迭代式走訪約 45 行,相對「擋超大與過深」的目的偏重,且 `_children` 用 `return [object()]` 當哨兵,讓走訪下一步才判為非 JSON,是繞路的寫法,讀者要跨兩個函式才懂。它保住了「絕不丟例外」的承諾(第 1 輪重要問題),複雜度有理由,但可讀性可再收。
- 引句:「+            return [object()]  # 讓走訪在下一步把它當成「不是 JSON 能表示的值」」
- 建議:非字串鍵直接由 `_children` 回報錯誤(例如回傳特定例外值或讓 `_size_error` 先檢查鍵),不用哨兵物件。
- 對照:metrics.py 與 dsp 沒有類似的走訪,這是專案內唯一一處,不算第二種做法。

## Finding 3
severity: minor
blocking: 否 風格差異,且有明確理由(重用同一份規則,避免兩份欄位驗證)。
- 位置:`src/rtb/domain/proposal.py:52-64`(`Proposal.__post_init__`)、`src/rtb/domain/task_state.py:46-47`
- 問題一:Proposal 的 `__post_init__` 先轉 primitives 再呼叫 `_field_errors` 與 `_expiry_errors`,與 `Evidence.__post_init__`(直接逐欄檢查,`evidence.py:57`)和 `MetricResult.__post_init__`(`metrics.py:37`)風格不同。好處是重用 parse 的規則、沒有第二份驗證;風險是規則靠 `to_primitives` 的轉換忠實,轉換若放寬(例如 tuple 轉 list)會讓自我驗證失去嚴格度,目前有 shape_ok 前置擋住。
- 問題二:task_state 在匯入時 `raise RuntimeError` 檢查表涵蓋所有狀態,而 `tests/domain/test_task_state.py:70` 已有同一條斷言(`assert set(TRANSITIONS) == set(TaskState)`),專案其他處(grep src 只有這一處 RuntimeError)沒有匯入時檢查。同一個不變量兩處守,第二種做法;匯入失敗的爆炸半徑比測試紅燈大。
- 引句:「+    raise RuntimeError("狀態轉換表沒有涵蓋所有狀態")」
- 建議:擇一;專案慣例是測試,可刪匯入時檢查。Proposal 的做法在註解說明「重用解析規則」的理由即可。

## 圖譜筆記查證
- `docs/rtb-production-agent-demo-knowledge/Systems/任務流程領域模型.md`:about_code 列 4 支(task_state、evidence、proposal、_checks),與程式一致(metrics.py 另屬其他筆記範圍,不在此筆記宣稱內)。
- PITFALL 引用的測試名稱共 8 個以上,逐一 grep `tests/`,每個都存在恰好 1 份定義;筆記內所有 `[test:...]` 也全數有對應定義(比對後無缺漏)。
- 大小描述(粗估位元組、每容器固定開銷)與 `CONTAINER_OVERHEAD`、`_leaf_bytes` 現況相符。
- 已讀,無 finding。

## 總結
最嚴重等級 minor,blocking 條數 0。
