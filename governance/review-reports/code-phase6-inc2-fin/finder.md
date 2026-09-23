severity: major
<!-- 來源:codex exec --sandbox read-only,取 stdout 最後一次完整輸出;原文未改 -->

## F1 S406 的空白舊列抓不到既有擋下原因在重建時遺失
severity: major
blocking: 是 — 測試宣稱守住「舊列不變」，但唯一舊列只是尚未處置的 pending 列，`block_code` 與 `disposition` 都是空值；重建若抹掉既有 `aggregate_limit_reached`，測試仍會綠，升級後會遺失舊列的擋下原因與重送回應語意。
引句:「assert store._conn.execute("SELECT * FROM proposals").fetchall() == before  # 舊列不變」

舊列只經 `accept` 建立，沒有寫入增量 1 才有代表性的 `aggregate_limit_reached`：file: `tests/executor/test_guardrails.py:204`。逐欄搬資料的風險點在重建表的 `INSERT ... SELECT`：file: `src/rtb/executor/inbox_store.py:356`、file: `src/rtb/executor/inbox_store.py:365`。

靜態 mutation 重現：把 `_rebuild_proposals()` 的 `block_code` 來源固定成 `NULL`，目前 S406 仍會通過，因為 old 列本來就是 NULL，而遷移後的新列仍可寫入新代碼。測試應在降版前先把 old 列確認成 `("blocked", "aggregate_limit_reached")`，重開及寫入新代碼後再確認該列完整不變。

## F2 歷史相容分類仍被舊測試要求具有真實觸發路徑
severity: minor
blocking: 否 — 目前歷史清單為空，不影響本次啟用；但依文件化回退移除比例規則後，測試會錯誤阻擋回退。
引句:「HISTORICAL_CODES = ()  # 規則已拿掉、只為讀舊資料而留在列舉裡的代碼(回退時比例代碼搬來這裡)」

舊測試仍以整個列舉參數化：file: `tests/executor/test_execution.py:176`，並無條件執行 `BLOCK_TRIGGERS[code]`：file: `tests/executor/test_execution.py:181`。依回退設計把比例代碼搬入歷史清單並移除 trigger 後，S404 會成立，但此測試會以 `KeyError` 翻紅；若保留 trigger，又會因生產規則已移除而在擋下斷言翻紅。應只要求「單筆＋非單筆」活動代碼有 trigger，排除歷史相容代碼。

S402 已讀，無 finding：綠路徑會跑完全部 1005 個預算；紅路徑回報第一個違反值，沒有縮小通過判定的覆蓋。

移除 `BLOCK_TRIGGERS` 集合相等斷言本身無漏網：新增 `BlockCode` 若忘記補 trigger，現有 `parametrize(list(BlockCode))` 加字典索引仍會失敗。

S406 的表結構降版方式正確；對照增量 1，允許值清單的差異確實是缺少 `budget_increase_too_large`。缺口在舊資料內容，不在 CHECK 清單。

動態 pytest 因唯讀環境沒有可用暫存目錄而未能啟動；F1 已附可在臨時副本套用的最小 mutation。
