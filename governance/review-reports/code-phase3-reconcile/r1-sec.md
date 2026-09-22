severity: major

### 1. 新增的作廢用戶端(`DspClient.void`)例外處理完全沒有回歸測試覆蓋——拿掉防護後測試依舊全綠(假綠)
severity: major
blocking: 是 符合判準明文的「測試在被守的程式拿掉後仍然綠(假綠)才算 major」,且此路徑一旦真的在生產環境失效,會讓對帳迴圈在網路異常時整個當掉,而不是照設計退回「結果不明」。
引句:「except (OSError, ValueError):」

說明(白話):對帳新增的「作廢」呼叫(`DspClient.void`)理論上要跟既有的 `write()`/`_get()` 一樣——遇到逾時、斷線就吞掉例外、回一個「沒拿到結果」的答案,交給 `VOID_TABLE` 的兜底規則判成「留在結果不明」,而不是讓例外原樣往外炸。程式碼確實寫了這段 `try/except (OSError, ValueError): return VoidAnswer(None)`(`src/rtb/executor/dsp_client.py:96-98`),但這批凍結 patch 附的整套測試(`test_reconcile.py`、`test_void.py`、`test_execution_e2e.py`、`test_runner.py`)裡,沒有任何一個測試會讓 `void()` 真的丟出 `OSError`/`ValueError`。

我實際做了拿掉防護的實驗:把 `void()` 裡的 `try/except` 整段刪掉、讓 `request_json` 的例外原樣往外拋,在一份暫存複本上用 `PYTHONPATH` 指向這份複本重跑相關測試:

輸入:對 `src/rtb/executor/dsp_client.py` 的 `void()` 拿掉 `except (OSError, ValueError): return VoidAnswer(None)` 這一段防護。
預期:至少一個測試(尤其是 S79–S81 那三支端到端事故測試,或某支專門測作廢逾時的測試)應該變紅,因為它們本該經過真實逾時/斷線的路徑。
實際:`pytest tests/executor/test_execution_e2e.py tests/executor/test_reconcile.py tests/dsp/test_void.py tests/executor/test_runner.py` → **70 passed**,完全沒有紅燈。

進一步確認這不是巧合而是「這條路徑本來就沒人走到」:
- `tests/executor/fakes.py` 的 `FakeDsp.void()` 是純記憶體模擬,從不會丟例外,單元測試(`test_reconcile.py`)天生碰不到這段。
- 真正會啟動真實 DSP 行程、用真實 HTTP 呼叫 `void()` 的只有 `test_execution_e2e.py` 裡兩支測試(`test_a_voided_key_answer_is_not_mistaken_for_a_version_conflict`、`test_the_real_client_voids_a_key_through_the_dsp`),兩支都只走成功路徑,沒有搭配故障注入。
- 更關鍵的是,這批 patch 同時新增的 `PlannedHandler.read_fault` **刻意把 `/void` 排除在故障注入之外**:`self.path.endswith("/void")` (`tests/executor/test_execution_e2e.py:25`),意思是就算某個測試想排一個 `timeout_before_commit`/`delayed_response` 給作廢請求,這支測試工具也不會讓它生效。這代表「作廢在真實逾時/斷線下的行為」這條路,從設計上就被測試基礎設施擋在外面,不是偶然漏測。

會不會是誤報:目前 repo 裡的程式碼本身是對的(`except (OSError, ValueError)` 確實有寫),我不是說現在就有錯誤行為;我是說**這段防護沒有任何測試守著**,之後任何人改壞它(例如漏掉某個例外型別、改成只接 `OSError`、或手滑刪掉整段)都不會被 CI 抓到,而後果是對帳迴圈在網路抖動時直接被未捕例外打斷(`runner.py` 的 `_loop` 只接 `(ExecutorHalted, InboxBusy)`,不接一般的 `OSError`/`TimeoutError`),不是設計要的「留在結果不明,下一輪再試」。
file: `src/rtb/executor/dsp_client.py:91-105`
file: `tests/executor/test_execution_e2e.py:20-26`

### 2. `voided_keys` 表沒有任何筆數或保留期上限,只增不減
severity: minor
blocking: 否 作者在設計文件裡已經明確承認並接受這個取捨(「作廢紀錄永久保留」),不是沒看到的錯誤,程式行為也符合該決定;只是程式碼本身目前沒有任何機械防護或告警,值得記一筆。
引句:「CREATE TABLE IF NOT EXISTS voided_keys (key TEXT PRIMARY KEY, voided_at TEXT NOT NULL);」

說明:每次對帳判定「作廢再判失敗」都會在 `voided_keys` 永久多寫一列(`src/rtb/dsp/store.py:45,336,351`),且明確設計成回退也不拿掉。相較於嘗試紀錄表有「每把鍵最多 50 列」「全表未結案上限 20」這類機械上限,`voided_keys` 完全沒有對應的筆數/期限上限或索引以外的清理機制。以本次的攻擊面提問「作廢表無上限成長」來看:實際上能觸發一次真正的作廢,前提是先要有一次真實的 DSP 逾時/斷線把嘗試打進「結果不明」,而不是靠外部輸入(例如被提示注入的提案)就能直接觸發,所以不構成可被外部觸發的拒絕服務放大器;但長期在真實環境跑,這張表確實只會單調變大,目前程式碼裡沒有任何機制承接計劃文件裡「REVISIT:2026-11-30…保留期限與清理方式」這條待辦。

---
其餘沙盒鏡頭要求逐一查過、沒有發現可被利用的洞,附查驗依據:
- **誰能作廢誰的鍵/一般寫入憑證能不能拿來作廐**:`check_scope` 的六項比對裡 `claims.action == request.action`,而作廐端點把 `action` 寫死成 `"void_operation"`(`src/rtb/dsp/server.py:191`),所以 `update_budget`/`pause_campaign` 憑證拿來打 `/void` 一定因動作不符被拒(403),已有 `S88`/`test_voiding_needs_a_void_capability_scoped_to_the_key` 覆蓋,我另外重跑過一次也確認一致。
- **對帳會不會被 DSP 回應內容誤導、把「成功」判成「沒發生」**:`VOID_TABLE`/`RESPONSE_TABLE` 裡「已作廐」與「已提交(查到)」是用 `state` 欄位互斥判斷,且「操作已作廐」那條規則刻意排在「版本衝突」之前(有註解說明理由),DSP 端 `store.void()` 與既有寫入都在同一把 `BEGIN IMMEDIATE` 鎖下依序查詢,經 `S89` 的多執行緒/多行程真重疊實驗驗證過(我讀了那支測試的鎖定手法,邏輯站得住)。沒有找到讓「已提交」被誤判成「未發生」的路徑。
- **憑證與金鑰外洩到日誌或資料庫**:嘗試紀錄只存憑證到期時間(`capability_expires_at`),不存token本體;DSP 端各種拒收例外訊息都是固定代碼字串,不回顯聲明或憑證內容;本次新增程式碼(`execution.py`、`dsp_client.py`、`capability_signer.py`)裡沒有任何地方把 `token`/金鑰寫進資料庫欄位或印出。
- **被提示注入的提案能不能經對帳鎖死廣告或讓執行迴圈停機**:提案裡可被分析端操控的欄位都在存入嘗試紀錄前經過嚴格白名單與型別驗證(`domain/proposal.py`),`sign`/`sign_void` 只接受設定檔裡明列的租戶與廣告,鎖住的範圍就事論事只到「同一個廣告」,全表未結案上限(20,增量1既有)把最壞情況封頂,這是作者刻意的「出事就停」而非本次 patch 新引入的漏洞。
