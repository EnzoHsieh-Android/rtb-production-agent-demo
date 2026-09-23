severity: major

### 1. 增量 3 把 `read_key` 改成「可指定讀哪一把」,漏改另一個既有呼叫端 `src/rtb/dsp/server.py`,會讓 Mock DSP 開不起來

severity: major
blocking: 是 這打壞現有合約(「金鑰環境變數名稱只在共用模組出現一次,讀取函式只給啟動程式呼叫;伺服器物件與簽發器都把金鑰當參數收」),而且照字面實作會讓既有系統(Mock DSP 伺服器,F7 端到端與幾乎所有整合測試都依賴它)開不起來,屬於「卡死」等級的錯誤行為。
引句:「讀取函式改成可指定讀哪一把、只給啟動程式呼叫」

說明:增量 3 打算把 `capabilitykit.read_key` 從現在的單一參數簽章(只讀固定的 `RTB_CAPABILITY_KEY`)改成「可指定讀哪一把」,好讓執行迴圈的啟動程式能分別讀簽發金鑰與核可金鑰兩個名稱。但 `read_key` 目前不是只有執行迴圈的啟動程式(`runner.py`)一個呼叫端——Mock DSP 的啟動程式也用同一支函式讀同一把金鑰,來初始化 DSP 端驗證憑證用的 `capability_key`:

file: `src/rtb/dsp/server.py:271`
```
capability_key=read_key(os.environ))  # 只有啟動程式讀環境變數
```

這一行呼叫 `read_key(os.environ)`,只帶一個參數。如果照設計文字把 `read_key` 改成「可指定讀哪一把」而沒有保留舊的單參數相容路徑(例如加一個有預設值的第二參數),這個既有呼叫端會因為缺少必要參數而在 Mock DSP 啟動時直接丟例外,整個 Mock DSP 伺服器開不起來——包括增量 1 的 F7 端到端測試([S340])、增量 3 自己的核可流程測試等所有依賴 Mock DSP 的測試與部署場景都會連帶卡死。

增量 3 設計節裡「被改寫的既有合約」小節(第 1 輪相容席折入的清單)只列了增量 1 的 [S331]/[S340]、增量 2 的 [S400]/[S403]、增量 1 的 [S342]、以及「分析行程邊界測試(金鑰名稱掃描)擴充成兩把金鑰」,完全沒有提到 `dsp/server.py` 這第三個 `read_key` 呼叫端。也就是說,即使是第 1 輪已經跑過的「架構對齊席」(sarch-4 項)也只核對了 `src/rtb/capabilitykit.py:22、:47` 與 `tests/analyzer/test_boundaries.py:127`,沒有核對 `read_key` 在生產程式碼裡的另一個呼叫點,這條路徑目前完全沒有被設計或審查涵蓋到。

file: `src/rtb/capabilitykit.py:47`(現有簽章,單一參數:`def read_key(environ: Mapping[str, str]) -> bytes | None:`)
file: `src/rtb/executor/runner.py:135`(既有第二個呼叫端:`read_key(os.environ if environ is None else environ)`)
file: `docs/rtb-production-agent-demo-knowledge/Systems/寫入能力憑證.md`(RULE:「金鑰環境變數名稱只在共用模組出現一次,讀取函式只給啟動程式呼叫;伺服器物件與簽發器都把金鑰當參數收」)

例:實作者依設計文字把 `read_key` 改成 `read_key(environ, name)` 且 `name` 為必填參數 → `src/rtb/dsp/server.py:271` 的 `read_key(os.environ)` 缺少第二個參數 → Mock DSP 的 `main()` 在啟動時丟 `TypeError`,伺服器完全起不來,連帶讓所有跑 Mock DSP 的測試(含事故 F7 端到端 [S340])全部失敗,而不是設計預期的「多一把核可金鑰」而已。
