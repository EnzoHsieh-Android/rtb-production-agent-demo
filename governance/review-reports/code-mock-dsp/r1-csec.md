severity: major

# r1 資安席(csec)報告

實驗腳本與重現都在 `/private/tmp/csec/`(exp.py、t1~t4.py),用真的 DSP 子行程加原始 socket 打。

## 已驗證安全(無 finding)
- 故障注入旗標繞過:旗標關閉時,大小寫變體(`x-fault`)、空值、重複標頭(`bogus` 加 `transient_5xx`)全部回 400 `fault_injection_disabled`,廣告狀態不變(version 維持 1)。`_read_fault` 在路由與讀本文之前執行,PUT/OPTIONS 等其他方法只會得到 501,沒有第二條路徑。
- SQL 注入:store.py 所有查詢皆為 `?` 參數化,無字串拼接。
- 錯誤回應洩漏:只回錯誤代碼與 retryable,不含例外訊息或路徑(`StoreBusy(str(exc))` 未外送)。
- 監聽位址:`super().__init__((LOOPBACK, 0), DspHandler)` 實際只綁 127.0.0.1。
- 跨站 CSRF:寫入需要自訂標頭 Idempotency-Key,瀏覽器會先發 OPTIONS 預檢,伺服器回 501 且無 CORS 標頭,預檢失敗。
- `--db` 為 operator 自己給的 CLI 參數,不經網路,不算攻擊面。
- 檔案 errors.py、pyproject.toml、requirements-dev.txt、.gitignore、.lumos/*、Mock-DSP.md、tests/__init__.py:已讀,無 finding。

## Findings

### 1. 非 DspError 例外讓連線被靜默切斷,永久性錯誤看起來像逾時
severity: major
blocking: 是 設計的核心是「型別化錯誤讓呼叫端分辨可重試與永久拒絕」,這條路徑上永久拒絕變成無回應,與 timeout_before_commit 無法分辨,agent 會誤判。

- 檔案:src/rtb/dsp/server.py 的 `_dispatch` 與 `_read_json`、`handle_error`。
- `_dispatch` 只接 `RequestRejected`、`DspError`、`_NoResponse`,其餘例外冒出去,被 `handle_error` 整個吞掉,連 stderr 都沒有,連線直接關閉,客戶端拿到空回應。
- 引句:「        length = int(self.headers.get("Content-Length") or 0)」(`Content-Length: abc` 走到這行 ValueError)
- 引句:「        pass  # 客戶端中途離開的例外不需要印到終端」(吞掉所有例外,無法事後追查)
- 走查:`_validate` 只檢查 `isinstance(int)` 與 `> 0`,`new_budget = 2**70` 合法通過,進入 `_apply` 時 sqlite3 拋 OverflowError(超過 64 位元),不是 DspError。`execute` 會 ROLLBACK 後再拋出,`_dispatch` 未接。
- 最小重現(`/private/tmp/csec/t4.py`):
  - POST /campaigns/c1/budget,本文 `{"new_budget":1180591620717411303424,"expected_version":1}` 得到空回應(`b''`);對照 `new_budget:-1` 正常回 422 validation_rejected。
  - 本文 `{"new_budget":` 後接 5000 個 9:`b''`(Python 對超長整數字串拋 ValueError,不是 JSONDecodeError)。
  - `Content-Length: abc`:`b''`。
- 後果:狀態沒有被改(已驗證 version 仍為 1,GET /operations/big1 回 404),但呼叫端無法得知「沒提交」,依設計會當成 ambiguous timeout,用同一把冪等鍵無限重試;且伺服端零日誌,除錯無從下手。旗標關閉時任何人(或 agent 自己帶的大數)即可觸發。
- 建議:`_dispatch` 加 `except Exception` 回 500 `internal_error`(retryable 視情況),`_read_json` 把 ValueError 與 OverflowError 對應成 400/422,`_validate` 加預算上限(例如 2**63-1)。

### 2. 負的 Content-Length 使處理執行緒永久阻塞,且整個伺服器沒有 socket 逾時
severity: minor
blocking: 否 只影響本機回送的 mock,不改狀態,不鎖資料庫,屬資源耗盡而非資料錯誤。

- 檔案:src/rtb/dsp/server.py 的 `_read_json`。
- 引句:「        if length > MAX_BODY_BYTES:」(只擋上限,不擋負值)
- 走查:`Content-Length: -1` 通過檢查,`self.rfile.read(-1)` 讀到 EOF 為止,客戶端不關就永遠卡住(重現 `t3.py`,4 秒後仍 `TIMEOUT no close`)。另外類別沒有設 `timeout`,`Content-Length: 10` 只送標頭不送本文的連線也無限期占用執行緒。
- 重現(`/private/tmp/csec/t2.py`):開 300 條只送標頭的連線,`ps -M` 顯示 302 條執行緒,等 8 秒後仍全部保留;同時讀取請求仍正常(讀取不受影響,因為讀本文發生在 `store.execute` 之前,不占寫鎖)。
- 建議:負值一律 400,`DspHandler.timeout = 10` 之類。

### 3. 不檢查 Host 標頭,可被 DNS rebinding 利用
severity: minor
blocking: 否 埠是隨機的,且需要受害者本機有瀏覽器並先被誘導,對 demo 情境風險低。

- 檔案:src/rtb/dsp/server.py 的 `_dispatch`。
- 走查:`Host: evil.example` 的 POST /campaigns/c1/budget 成功提交(`t1.py` 輸出 version_after 為 2)。經 DNS rebinding 的網頁可視為同源,繞過預檢,直接讀寫廣告狀態。
- 建議:只接受 `127.0.0.1:<port>` 與 `localhost:<port>` 的 Host。

### 4. expected_version 沒有型別檢查,布林 true 等同版本 1
severity: minor
blocking: 否 只在版本樂觀鎖被繞過的邊角成立,呼叫端是自家 agent。

- 檔案:src/rtb/dsp/server.py 的 `_operation`,store.py 的 `_execute_in_transaction`。
- 引句:「        if current.version != op.expected_version:」
- 重現(`t2.py`):`{"expected_version":true}` 對 version 1 的廣告 pause 成功(Python 中 `1 == True`),`1.0` 同理。`_validate` 對 new_budget 明確排除 bool,對 expected_version 卻沒有,寬嚴不一。
- 建議:要求 `type(v) is int`,否則 422。

### 5. 含 `/` 或 `?` 的冪等鍵可寫入但無法用 GET /operations/<key> 查回
severity: minor
blocking: 否 只影響查詢便利性,不影響冪等本身。

- 檔案:src/rtb/dsp/server.py 的 `ROUTES` 與 `_route`。
- 走查:冪等鍵是任意字串,`a/b` 的 POST 被接受並寫入(回 version_conflict 是因為當時版本已變,鍵本身沒被拒絕);GET /operations/a/b 回 404 not_found,`a?b` 被 `split("?")` 切掉,回 operation_not_found。路徑段也沒有做百分比解碼,所以客戶端無法編碼繞過。agent 若用含斜線的鍵,事後無法查證是否已提交,而這正是 GET /operations 存在的用途。
- 建議:寫入時限制鍵字元集(例如 `[A-Za-z0-9._:-]{1,128}`)。

### 6. 測試 fixture 在埠行斷言失敗時洩漏子行程
severity: minor
blocking: 否 只影響測試環境整潔,未能穩定重現。

- 檔案:tests/dsp/conftest.py 的 `DspProcess.__init__` 與 `start_dsp`。
- 引句:「        assert line.startswith("PORT="), f"DSP 沒有印出埠:{line!r}"」
- 走查:斷言在建構子內失敗時,物件尚未加入 `started`,fixture 收尾不會 kill 該行程。環境傳遞部分正確:`{**os.environ, "PYTHONPATH": SRC}` 覆蓋既有 PYTHONPATH,子行程用 `sys.executable`,未見注入問題。此條未能重現(需要 DSP 啟動失敗),降權為 minor。

最嚴重等級為 major,blocking 共 1 條。
