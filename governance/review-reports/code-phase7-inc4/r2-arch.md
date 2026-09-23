severity: clean

### 驗收:F2(啟動執行緒移進各自 try/finally)修到位

`tests/analyzer/test_f5_end_to_end.py:53-64` 的結構,已與 `tests/analyzer/test_boundaries.py:37-49`(`test_the_agent_cannot_reach_fault_injection_on_production_style_servers`)逐行對齊:
- dsp 構造留在外層 try 之前(兩邊都是),dsp 的啟動執行緒移進外層 `try:` 內(改動前是掛在 try 外)。
- inbox 構造與其啟動執行緒都落在外層 try 內、inbox 自己的內層 try 之前/之內,兩檔對比行序一致:先 `inbox = InboxServer(...)`,再開內層 `try:` 才 `threading.Thread(...).start()`。
- `finally` 收尾順序相同:內層先 `inbox.shutdown()` + `inbox.server_close()`,外層再 `dsp.shutdown()` + `dsp.server_close()`。
- 新增的中文註解(`test_f5_end_to_end.py:49-50`)明確點名比照 `test_boundaries.py` 代碼審第 1 輪教訓,語意與 `test_boundaries.py:35-36` 的既有註解一致,屬於補說明,不是新規則。

未發現殘留:改動前「dsp 執行緒在 try 外、inbox 執行緒在內層 try 外」的兩處都已收進各自保護傘,無漏改的第三個伺服器或其他啟動點。

### 驗收:修正本身有沒有引入新的不一致

沒有。差異只動了 `run_once` 函式內的縮排與敘述順序,沒有新增伺服器、沒有改動 `_walk`、`finally` 收尾方法名稱(`shutdown()` + `server_close()`)與既有寫法相同,也沒有引入 `test_boundaries.py` 未出現過的資源管理手法(例如 `contextlib.ExitStack`、`atexit` 等)。改動範圍精準對應上一輪指出的問題,未夾帶其他重構。

不對齊共 0 條,其中 major 0 條。

⚠ 交編排者:圖譜三格皆空(無受影響測試、共改夥伴、呼叫者命中),本輪判斷純靠與 `test_boundaries.py` 的逐行文字比對,屬人工可重覆驗證但未經圖譜背書;若後續要建立此類「雙伺服器巢狀 try/finally」的機檢規則,建議另立 Systems/PITFALL 節點記錄,目前僅靠測試檔案間的手動比對維持一致。
