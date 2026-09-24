severity: major

### 1. 資料來源失敗的實際原因在進入命令列前被丟掉
severity: major
blocking: 是
引句:「s.error or '資料來源讀不到(例:DSP 連不上或拒讀、稽核金鑰沒設)'」
file: `src/rtb/ops/side_effects.py:282`
file: `src/rtb/ops/side_effects.py:312`
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase9可觀測與SLO_計劃.md:351`

觸發情境：DSP 連線失敗、回 401／403／503、回應格式錯誤或提交時間無法解析時，底層會產生帶具體原因的 `DspUnreadable`；但 `unauthorized()` 與 `duplicates()` 接住後只回 `Tally(..., missing=True)`，沒有保留例外內容。

會出什麼錯的行為：命令列雖正確回 `EXIT_INCOMPLETE=8`，stderr 卻對所有情況只印同一串可能原因，無法說明這次究竟是連不上、金鑰未設／錯誤、DSP 拒讀還是資料畸形，沒有履行「印出哪幾條、為什麼」的既定合約。

建議修法：讓 `Tally` 保留經過遮罩的資料來源錯誤原因，並由 `period_tally` 傳到 `SloStatus.error`；或用其他不丟失部分統計的方式保存第一個／彙整後的 `DspUnreadable` 原因。stderr 應印出實際分類與狀態碼，且不得包含金鑰內容。

### 2. 被認定可用的長金鑰可能永遠無法經 HTTP 標頭送達
severity: major
blocking: 是
引句:「def encode_audit_key(key: bytes) -> str:」
file: `src/rtb/capabilitykit.py:50`
file: `src/rtb/dsp/server.py:343`
file: `/opt/homebrew/Cellar/python@3.14/3.14.6/Frameworks/Python.framework/Versions/3.14/lib/python3.14/http/client.py:226`
file: `/opt/homebrew/Cellar/python@3.14/3.14.6/Frameworks/Python.framework/Versions/3.14/lib/python3.14/http/server.py:408`

觸發情境：把雙方的 `RTB_DSP_AUDIT_KEY` 設為 49,138 個 ASCII 位元組。`read_key()` 只有 32 位元組下限，因此兩端都把它視為有效；不帶補位的 base64url 為 65,518 字元，加上標頭名稱與行尾後是 65,537 位元組，超過 CPython 每條 HTTP 標頭 65,536 位元組的限制。

會出什麼錯的行為：DSP 在 `_require_audit_key()` 執行前就以 431 拒絕請求；即使客戶端和 DSP 設定完全相同的合法金鑰，兩條稽核指標仍永久缺資料並回代碼 8。服務啟動時不會指出這份設定永遠無法使用。

建議修法：為稽核金鑰另訂明確且遠低於標頭上限的最大位元組數，讓 DSP 與評估器在啟動／執行入口一致地拒絕超長設定；解碼入口也應在配置上限內先限制字串長度。補測最小長度、最大長度與最大值加一，以及相同上限下的非 Latin-1 金鑰。

測試：指定的 pytest 命令因唯讀沙盒沒有可用暫存目錄，在測試收集前失敗；上述邊界以讀碼與不寫檔的直譯器計算查證。