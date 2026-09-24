severity: major

### 1. 逐條隔離把程式缺陷當成穩定的資料缺失，命令列仍回成功
severity: major
blocking: 是
引句:「except Exception as exc:  # 一條讀不到不拖垮另外五條;例外記在這一條的狀態裡」
file: `src/rtb/ops/slo.py:233`
file: `src/rtb/ops/slo.py:289`

觸發情境：任一計數器發生 `AssertionError`、`TypeError` 等程式缺陷，或共用資料庫毀損而拋出 `sqlite3.DatabaseError`。實測讓計數器拋出 `AssertionError("programming bug")`，六條狀態全部變成 `missing=True`、`stable=True` 並記下錯誤。

會出什麼錯的行為：例外雖寫進 JSON，`run()` 只檢查 `stable`，因此所有指標都計算失敗仍回 `EXIT_OK`。排程或監控若依退出碼判斷，會把整支告警器失效當成成功；這超出「單一資料來源暫時讀不到」應隔離的範圍。

建議修法：只隔離可預期的資料來源例外；程式不變量或共享資料庫錯誤應往外拋。若仍要捕捉所有一般例外以完成其他指標，至少新增非零的部分失敗退出碼，並在任一 `SloStatus.error` 不為空時回該代碼。

### 2. 合法的非 Latin-1 稽核金鑰無法放進 HTTP 標頭
severity: major
blocking: 是
引句:「headers = None if audit_key is None else {ClientHeader.CAPABILITY: audit_key}」
file: `src/rtb/capabilitykit.py:52`
file: `src/rtb/httpclient.py:65`
file: `src/rtb/ops/side_effects.py:197`

觸發情境：將 `RTB_DSP_AUDIT_KEY` 設為至少 32 位元組、但含中文等 Latin-1 以外字元的字串。共用 `read_key()` 以 UTF-8 編碼後會認定這把金鑰可用，DSP 與維運入口也會得到相同位元組；但標頭傳送時 Python HTTP 用戶端以 Latin-1 編碼，會在送出前拋出 `UnicodeEncodeError`。用指定直譯器以 `"密" * 32` 已重現。

會出什麼錯的行為：例外被 `get_json()` 當成 `ValueError` 轉為 `DspUnreadable`，請求根本沒到 DSP；兩條零目標指標永久顯示資料來源缺。也就是 DSP 與維運端明明設定了相同且依共用讀法有效的金鑰，仍無法完成稽核。

建議修法：為稽核金鑰定義可傳輸格式，例如只接受 ASCII／base64url，並在啟動或命令列入口立即驗證、不合法時明確報設定錯誤；或先將金鑰位元組做 base64url 編碼再放入標頭，DSP 解碼後再固定時間比較。

測試說明：指定 pytest 命令已執行，但唯讀沙盒使 pytest 無法在 `/tmp` 等位置建立捕捉用暫存檔，測試尚未開始即退出；上述兩項改以不寫檔的指定 Python 直譯器重現。