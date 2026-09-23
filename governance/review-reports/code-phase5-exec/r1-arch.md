severity: clean

# 架構對齊審查:rtb-3b code-phase5-exec r1-snapshot.patch

## 一、分層與依賴方向

擋下原因的對應函式 `block_code_for_failure` 放在收件表模組(`inbox_store.py:84`),和它要用的 `BlockCode` 列舉(`inbox_store.py:73`)同一支檔,執行端(`execution.py`)照舊只是匯入使用(`execution.py:37-43` 的 import 區塊多了 `block_code_for_failure` 一項),方向跟改動前一樣是「執行端依賴收件表模組」,沒有反過來。

三個呼叫點都落在既有的「執行端 → 收件表模組」方向上:
- 開始一筆撞到既有失敗鍵:`execution.py:419`
- 終點確認:`execution.py:434`(`_ack_terminal`,定義於 `execution.py:426`)
- 取件時撞到既有失敗鍵:`inbox_store.py:500`(`_settle_existing`,定義於 `inbox_store.py:492`,呼叫時多傳的 `code` 參數是同一支模組內部的 `attempt_store.AttemptRow.code`,不是跨模組回頭呼叫)

`attempt_store.py` 新增的 `version_conflict_count`(`attempt_store.py:251`)沒有匯入或呼叫 `inbox_store` 的任何東西,查了 `inbox_store.py:31` 的 import(`from rtb.executor import attempt_store`)也確認方向仍是「收件表模組 → 執行嘗試表模組」單向,`attempt_store` 沒有反向依賴收件表。三處都跟改動前的分層方向一致,沒有跨層直呼。

## 二、命名與錯誤處理

- `block_code_for_failure(code: OutcomeCode | None) -> BlockCode`:命名比照本檔既有的 `X_for_Y` 風格(如 `tests/executor/fakes.py:118` 的 `record_for`),輸入輸出型別跟三個呼叫點的實際值(`AttemptRow.code`)一致;`code is None` 時落回原本唯一的行為 `OPERATION_PREVIOUSLY_FAILED`,沒有改變舊行為的預設路徑。
- `Accepted` 新欄位 `block_code: str | None = None`(`inbox_store.py:229`):放在 frozen dataclass 最後一個位置且帶預設值,兩個既有的建構呼叫點分別是 `inbox_store.py:390`(帶入 `existing[2]`)與 `inbox_store.py:408`(新提案、未擋下,省略此參數吃預設值 `None`)——沒有其他建構呼叫點需要改,型別跟同檔 `state: str` 一樣是原始字串而非列舉,一致。
- `version_conflict_count` 沒有像 `unresolved_count`(`attempt_store.py:243`)那樣做 `count < 0` 就丟 `CorruptedAttemptRow` 的毀損檢查——但 `unresolved_count` 的檢查是因為它是「第 1 列數 − 終點列數」的相減,前提不成立才會出現負數;`version_conflict_count` 是單純 `COUNT(*)`,數學上不會是負,沒有對應的不變量可查,不是漏寫檢查。
- SQL 用 `# noqa: S608 - 只拼接固定條件` 的註解字樣跟 `unresolved_count_query`(`attempt_store.py:236`)、`campaigns_with_unresolved`(`attempt_store.py:263`)、`unresolved_keys`(`attempt_store.py:271`)一致,錯誤處理與既有寫法對齊,沒有另立說法。

## 三、第二種做法

**新查詢沒有拆成「組查詢」與「執行」兩支**——但查過同檔其餘三支「查未結案/衝突」函式後,`unresolved_count_query`(`attempt_store.py:232`)其實是全檔唯一拆開的一支,`campaigns_with_unresolved`(`attempt_store.py:260`)與 `unresolved_keys`(`attempt_store.py:269`)都是查詢字串跟 `_conn(tx).execute` 寫在同一支函式裡,跟新的 `version_conflict_count` 同一種寫法(3 支 vs 1 支,inline 才是多數寫法)。查了 `unresolved_count_query` 拆開的理由:`tests/executor/test_attempt_store.py:851` 那支測試要單獨對查詢字串跑 `EXPLAIN QUERY PLAN` 驗證有沒有吃到部分索引(`unresolved_count` 在收件取件的熱路徑上,見 `attempt_store.py` 開頭模組說明)。`version_conflict_count` 的欄位 `code` 在 `SCHEMA`(`attempt_store.py:38-48`)裡沒有任何索引,函式自己的 docstring(`attempt_store.py:252-253`)也寫明是「事後查帳用」、目前 `src/` 裡沒有任何呼叫點(只有測試在用)。沒有索引可驗、也不在熱路徑,不需要那組「查詢/執行」分離來支援索引計畫測試——不拆是照多數寫法走,不是另立第二種做法。

**新測試的固定件**:`test_version_conflict.py` 前面幾個案例(`test_a_dsp_version_conflict_is_acknowledged_as_version_changed` 等)用的是既有 `Harness`/`proposal`/`h.submit`/`h.process`(`tests/executor/fakes.py:151` 起的 `Harness`),跟 `test_queue.py` 的用法一致,沒有另造。真正新增的是 `real_dsp` 這個夾具(`test_version_conflict.py:119`)與 `_RacingClient`(`test_version_conflict.py:136`)、`_one_writer`(`test_version_conflict.py:146`)——查過之後這不是重造 `test_execution_e2e.py` 的 `World`(`test_execution_e2e.py:43`):`World` 只管單一收件表/單一執行器 + 故障排程,這支新測試要的是「兩套各自獨立的收件表與執行器、共用同一個真 DSP、在網路呼叫那一刻對齊」,`World` 的形狀做不到。啟動真 DSP 伺服器那段樣板(`CampaignStore(...).seed_campaign`、`DspServer(fault_injection=False, hang_seconds=0.2, delay_seconds=0.0, ...)`、`threading.Thread(target=server.serve_forever, daemon=True)`)在全專案本來就沒有共用夾具,`tests/analyzer/test_dsp_client.py:32`、`tests/analyzer/test_boundaries.py:37`、`tests/dsp/test_capability.py:44` 等每支檔都各自寫一份幾乎一樣的樣板——這是本專案既有的重複方式,不是這支新檔獨創的第二種做法。柵欄放的位置(`_RacingClient.write` 裡、打真 DSP 之前)跟 `test_multi_worker.py` 文件開頭講的既有並行測試法(柵欄放在「進資料庫交易之前」、monkeypatch Executor 方法入口,見 `test_multi_worker.py:1-9`)不是同一招,但那支檔測的是「同一份假 DSP、同一份收件表被兩個工作者搶」的情境,新測試測的是「兩套獨立系統打同一個真 DSP」,既有柵欄招式打不到這個情境(沒有共用的一個交易入口可掛),換一種對齊方式是情境不同所需,不是重造既有做法的第二種寫法。

不對齊共 0 條,其中 major 0 條。

⚠ 交編排者:「新查詢是否該拆成組查詢/執行」與「real_dsp 夾具算不算重造」兩點我查完既有程式碼後判定為對齊,但都是讀了周邊多支檔案、比對出「多數寫法/既有慣例本身就重複」才得出的判斷,不是一眼能定的;如果編排者手上有這兩點更早的既有裁決(例如本來就打算把 `real_dsp` 之類的樣板抽成共用夾具、只是這個 phase 還沒排到),請以那份裁決為準覆蓋我這裡的結論。
