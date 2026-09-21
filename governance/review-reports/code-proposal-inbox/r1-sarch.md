severity: major

# 架構對齊審查 r1(提案收件口)

總評:依賴方向正確(executor→domain、共用基礎;沒有 import dsp;domain 沒有反向依賴),共用基礎與 BEGIN IMMEDIATE 都有沿用,事件表(有界、盡力而為、只存封閉代碼)與 DSP 的操作歷史(不可丟的稽核記錄)用途不同,不算重造輪子。主要問題是錯誤對應走了第二條路。

### 1. 拒收錯誤不走共用的 map_exception,另起一套「型別自帶 code 加伺服器內對照表加回傳值」
severity: major
blocking: 是 專案既有做法是例外型別加 ERROR_TABLE,由 JsonHandler.map_exception 統一轉成錯誤回應(且對應失敗也保證落到 500 JSON);收件口改成在 handle_request 內自行 catch、以一般回傳值送出錯誤,等於第二種做法;需要 highest_revision 這個額外欄位是理由,但 map_exception 的回傳形狀沒擴充、節點也沒記錄為何偏離。
引句:「return REJECTION_STATUS[type(rejection)], body」
- file: `src/rtb/httpkit.py:92`(map_exception 定義與 `_reply_unexpected` 的兜底)
- file: `src/rtb/dsp/server.py:49`(ERROR_TABLE、沿繼承鏈查表;`type(rejection)` 精確比對不會涵蓋日後的子類別)
- 建議:擴充共用基礎的對應結果可帶額外欄位,或在提案收件口節點寫明偏離理由與撤除條件。

### 2. 錯誤型別放在 store 檔,且 code/retryable 與狀態碼分處兩地
severity: minor
blocking: 否 DSP 是 errors.py 放型別、server.py 一張表管 (狀態碼, 代碼, 可否重試);收件口把 code/retryable 放型別屬性、狀態碼另放 REJECTION_STATUS,同一事實分兩處維護,但範圍小、有測試,屬一致性問題。
引句:「    code = "content_conflict"」
- file: `src/rtb/dsp/errors.py:1`

### 3. 交易的 COMMIT/ROLLBACK 樣板在收件口重複兩次(加 DSP 共三份)
severity: minor
blocking: 否 sqlitekit 只提供 begin_immediate,沒有交易情境管理;新增程式沿用同一段寫法而非抽共用,屬重複而非第二種做法,可列 REVISIT。
引句:「                self._conn.execute("ROLLBACK")」
- file: `src/rtb/dsp/store.py:242`
- file: `src/rtb/sqlitekit.py:36`

### 4. 「executor 不得 import dsp」只是慣例,沒有機檢
severity: minor
blocking: 否 domain 層有 ruff.toml 的禁用清單守著,但新開的 executor 層沒有對應設定,domain 的清單也未禁 rtb.executor / rtb.dsp;目前程式碼方向正確,只是缺守門。
引句:「不執行提案、不呼叫 DSP、不做政策與租戶檢查。」
- file: `src/rtb/domain/ruff.toml:1`

### 5. 圖譜節點分工的敘述沒同步:共用行程基礎仍說收件口是「之後」、NoResponse「只有 DSP 在用」
severity: minor
blocking: 否 節點分工本身清楚(收件口不負責共用基礎、共用基礎只列自己的兩支檔),但共用行程基礎節點裡的舊句與收件口現況矛盾,會誤導下一位讀者。
引句:「含故障注入標頭的開關 `read_fault`:啟動時沒開旗標就一律拒絕 X-Fault」
- file: `docs/rtb-production-agent-demo-knowledge/Systems/共用行程基礎.md:28`(「之後的提案收件口也用」)
- file: `docs/rtb-production-agent-demo-knowledge/Systems/共用行程基礎.md:33`(NoResponse 目前只有 DSP 用)
