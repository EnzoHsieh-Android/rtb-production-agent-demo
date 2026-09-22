severity: clean

已對照下列既有做法逐條核對「增量 2 設計:寫入能力憑證」(第 2 版)的每一個架構決定,沒有發現引入原本沒有的第二種做法或跨層直呼:

- 共用層邊界:比對 `src/rtb/httpkit.py`、`src/rtb/sqlitekit.py`、`src/rtb/httpclient.py` 與 `docs/rtb-production-agent-demo-knowledge/Systems/共用行程基礎.md` 的 `responsibility`(明寫「不負責任何業務規則」);設計把格式機制放共用層、聲明組裝放執行行程、範圍驗證放 DSP,新節點落在 `docs/.../Systems/寫入能力憑證.md`(已存在的空節點),不跟共用行程基礎的既有職責衝突。
- 分析行程匯入禁令:核對 `src/rtb/analyzer/ruff.toml` 與 `tests/analyzer/test_boundaries.py`,確認確實有 TID251 擋 `rtb.executor`,設計「不另外發明掃描測試」的說法屬實。
- 錯誤對照表:核對 `src/rtb/dsp/errors.py`、`src/rtb/dsp/server.py` 的 `ERROR_TABLE`/`error_entry`/`map_exception`,設計沿用同一套「型別化例外 + (狀態碼, 代碼, retryable) 查表」機制,沒有另開一條例外分派路徑。
- 啟動參數:核對 `src/rtb/dsp/server.py`、`src/rtb/executor/inbox_server.py` 的 `main()`,確認專案目前確實沒有讀環境變數或設定檔的先例,且 executor 端維持既有 argparse 傳遞設定檔路徑、只有 DSP 端新增環境變數且已附理由(命令列參數會出現在行程清單);executor 遇缺金鑰拒絕啟動的作法沿用 `inbox_server.py` 既有的 `except (ValueError, InboxRejected) → SystemExit(2)` 拒絕啟動樣式,DSP 端維持既有「無拒絕啟動機制、請求時查表」的樣式,兩邊各自對齊自己既有的慣例而非互相矛盾。
- 補欄位遷移:核對 `src/rtb/analyzer/task_store.py` 的 `_migrate_evidence_payload_column`,設計描述的「每次連線檢查欄位在不在、缺就在交易內 ALTER TABLE、舊列補預設值」與既有實作一致。
- DSP 另有一份定義的偏離:核對 `src/rtb/domain/_checks.py`(明寫「DSP 刻意不依賴這裡」)與 `src/rtb/dsp/store.py` 的 `_is_plain_int`/`_validate`,設計讓 DSP 自己定義聲明欄位與範圍檢查、動作名稱沿用路由表既有的 `update_budget`/`pause_campaign`,是既有偏離的延伸,不是新增第三份定義。
- 第 1 輪架構對齊席提過的五點(業務聲明不放共用層、動作與編號格式定義歸屬、參數傳遞方式、錯誤對照表沿用、補欄位遷移)在第 2 版文字裡逐條都有對應段落回應,且回應內容與程式碼現況核對一致。

沒有發現新的模組邊界跨越或第二套做法,故列 clean。
