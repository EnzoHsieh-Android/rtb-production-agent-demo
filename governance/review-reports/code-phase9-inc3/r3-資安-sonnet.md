severity: clean
All 364 relevant tests pass. I've completed the review. Here is the full report.

---

severity: clean

# 資安審查報告(增量 3 第 2 輪修正覆核)

範圍:`governance/review-reports/code-phase9-inc3/r3-delta.patch`(對照 `r2-資安-sonnet.md` 提出的「結束代碼弱化、稽核靜默失效看不出來」那條 major 做的修正),站在攻擊者角度重查三件事:結束代碼 8 與 stderr 有沒有洩漏、稽核金鑰專用標頭 base64url 編解碼的邊界與時間側通道、分析端能不能藉新標頭成員夾帶不該送的標頭。三項都沒有找到可利用的洞。

## 結論摘要

r2 那條 major(稽核金鑰讀不到時,`slo.run()` 結束代碼仍是 0,監控腳本看不出稽核安靜失效)在這版被正確封住:新增 `EXIT_INCOMPLETE = 8`,`run()` 在任何一條 `missing` 或 `error` 非空時一律回 8,優先於 `EXIT_UNSTABLE`,且判斷式用的是 `any` 語意的逐條檢查(`incomplete = [s for s in statuses if s.missing or s.error is not None]`,`src/rtb/ops/slo.py:290`),不是只挑兩條稽核相關指標,涵蓋面比 r2 建議的還寬。稽核金鑰改走專用標頭 `X-Dsp-Audit-Key`、base64url(不帶填充)編碼後,伺服器端解碼與固定時間比對的順序、失敗路徑、跟能力憑證標頭的隔離都覆核過,沒有發現可利用的繞過或側通道。分析端(`rtb.analyzer.dsp_client`/`inbox_client`)沒有引用 `ClientHeader`、也沒有傳 `headers` 參數給 `request_json`,新增的 `AUDIT_KEY` 成員對它完全不可觸及。

---

## 驗證記錄(逐項附證據)

### 1. 結束代碼 8 是否真的讓「稽核安靜讀不到」對外可見;stderr 有沒有洩漏金鑰或內部細節

引句:「incomplete = [s for s in statuses if s.missing or s.error is not None]」

行為確認:`run()` 印完 JSON 之後,只要任一條 `SloStatus.missing` 為真或 `error` 非空,就印出每一條的原因並回 `EXIT_INCOMPLETE`(8),優先於 `EXIT_UNSTABLE`(見 `src/rtb/ops/slo.py:290-295`)。用假 DSP(`http://127.0.0.1:9`,连不上)与真实缺金鑰情境都会命中这条路径,`tests/ops/test_slo.py` 新增的 `test_a_programming_error_or_a_broken_database_is_not_swallowed` 與既有 `test_the_command_line_reports_fixed_exit_codes` 也已經把「沒帶稽核金鑰時結束代碼是 8、`safe_completion` 不出現在 stderr」斷言死。攻擊者角度(掌控/滲透 DSP 一方,故意在自己搞壞事的窗口讓兩支列操作端點失敗)確實再也騙不過只看結束代碼的監控腳本。

洩漏面查了兩層:
- `missing=True` 且 `error is None` 時,印出的是固定文案「資料來源讀不到(例:DSP 連不上或拒讀、稽核金鑰沒設)」(`src/rtb/ops/slo.py:293`),不含任何動態內容,不可能洩漏金鑰。
- `error` 非空時,來源被這版收斂成只有 `DspUnreadable`(`except DspUnreadable as exc:`,`src/rtb/ops/slo.py:239`),取代了舊版「任何 `Exception` 都吞下來塞進 `error` 欄位」的寫法——這其實是收斂了洩漏面,不是擴大:程式錯誤、資料庫毀損現在會直接往外丟(`tests/ops/test_slo.py` 的 `test_a_programming_error_or_a_broken_database_is_not_swallowed` 蓋住 `AssertionError`/`TypeError`/`AttributeError`/`KeyError`/`sqlite3.DatabaseError` 五種,斷言 `slo.evaluate`/`slo.run` 都往外丟,不會被塞進 JSON 或 stderr)。追過 `DspUnreadable` 在 `src/rtb/ops/side_effects.py` 裡所有的建構點(`get_json` 包 `OSError`/`ValueError` 的 `str(exc)`、`commit_moment`、`_write`/`_operation` 包 `KeyError`/`TypeError`/`ValueError` 的 `repr(exc)`),沒有一處把 `audit_key`/`candidate`/標頭原始值放進例外字串——這些例外只描述連線失敗訊息、HTTP 狀態碼、或 DSP 回應裡本來就不可信、非金鑰的欄位(如 `committed_at`、`campaign_id`)解析失敗。也就是說,就算把這條印到 stderr 的內容視為攻擊面(例如日誌轉送到較不信任的系統),裡面也沒有金鑰或簽章之類的機密。

判定:clean——這條修正是有效的,且沒有帶出新的資訊洩漏面。

### 2. 稽核金鑰專用標頭 `X-Dsp-Audit-Key`、base64url:解碼失敗/補位/大小寫/超長/空值,以及固定時間比對與側通道

引句:「解回位元組後固定時間比對,解不開當帶錯(代碼審第 2 輪)」

引句:「return _b64decode(text)」

比對確認在解碼後的位元組上:`_require_audit_key()` 先 `presented = self.single_header(AUDIT_HEADER)`,再 `candidate = decode_audit_key(presented)`(丟 `TokenRejected` 就直接回 403,不比對),最後 `hmac.compare_digest(candidate, expected)`(`src/rtb/dsp/server.py:185-193`)——`candidate` 與 `expected` 都是 `bytes`,不是比較 base64 字串本身,符合預期契約。

用實際 Python 解釋器逐一驗證了 `capabilitykit._b64decode`(`src/rtb/capabilitykit.py:84-90`)在下列輸入的行為,全部落在「拒收、不崩潰、回 403」的路徑,沒有一種能繞過或造成例外穿出:
- 空字串:`_SEGMENT` 要求至少一個字元(`+`),空字串 `fullmatch` 失敗 → `TokenMalformed` → 403,對應「空值」的處理。
- 帶填充(`=`)、非法字元(空白、`!`)、非 Latin-1 的原文(例如中文金鑰直接送、"密"):`_SEGMENT.fullmatch` 都失敗 → 403,不會被誤判成合法輸入(測試 `test_the_operation_list_endpoints_require_the_audit_key` 裡的 `"not base64!"`、"沒編碼的原文不收" 兩個案例覆蓋到)。
- 通過字元檢查但補位後長度不合法(例如去掉填充後 `len % 4 == 1`,像 61 個字元的全大寫串):`base64.urlsafe_b64decode` 丟 `binascii.Error`,被 `except (binascii.Error, ValueError)` 接住轉成 `TokenMalformed` → 403,不會是 500。
- 超長標頭(實測 20480 字元的合法 base64url 也能正常解碼、無報錯,只是解出一串跟金鑰對不上的位元組,比對後 403):`decode_audit_key` 本身沒有像 `capabilitykit.decode()` 那樣的 `MAX_TOKEN_CHARS` 上限,但實際請求的標頭大小受 `http.client` 內建的單行 64KB 上限(`_MAXLINE = 65536`)與 100 行標頭數上限限制(這是 Python 標準函式庫既有邊界,不是這版新引入也不是這版動到的程式碼),64KB 內的 base64 解碼是微秒級運算,量不到能構成資源耗盡攻擊,也沒有因為輸入更長就多做任何「跟金鑰有關」的運算。
- 大小寫:HTTP 標頭名稱本來就不分大小寫(`self.headers.get_all(name)`),`X-Dsp-Audit-Key`/`x-dsp-audit-key` 等寫法都會被讀到;base64url 字母大小寫是有意義的資料位元,不是可以互換的等價寫法,這是正常編碼行為不是漏洞。

時間側通道查證:解碼函式 `_b64decode` 只吃「攻擊者送來的候選字串」,完全不觸碰 `expected`(伺服器端的真金鑰)——正規表達式比對、`base64.urlsafe_b64decode` 的執行時間只跟候選字串自身的長度/字元組成有關,不會因為「跟真金鑰像不像」而有差異,所以解碼步驟本身沒有能拿來猜金鑰內容的側通道。真正touch到金鑰內容的只有解碼「之後」的 `hmac.compare_digest(candidate, expected)`,這一步比對的是官方文件保證的固定時間比較,且不管候選解碼是否合法、長度是否相符,伺服器對外回應的狀態碼與錯誤碼都是同一組(`RequestRejected(403, "audit_key_invalid")`),格式錯誤跟金鑰錯誤在回應內容上無法區分——`tests/ops/test_side_effects.py` 新增/沿用的斷言(`"not base64!"` 跟 `AUDIT + b"x"`、`b"x" * len(AUDIT)` 三種都回同樣的 403)印證了這點。在題目「威脅模型內合理程度」下沒有找到可操作的側通道。

判定:clean。

### 3. 分析端能不能藉新增的標頭成員送出不該送的標頭(例如故障注入)

引句:「AUDIT_KEY = "X-Dsp-Audit-Key"」

驗證了兩層防線都還在,新成員沒有打開缺口:

- `httpclient.request_json` 收 headers 時逐一檢查 `isinstance(key, ClientHeader)`(`src/rtb/httpclient.py:63-64`,這段本次沒改動,不在 patch 材料內,故列 file 引用),用的是型別檢查不是字串相等——就算 `StrEnum` 成員在值層面等於同名字串,傳入的普通字串(如 `"X-Fault"`)一樣不是 `ClientHeader` 的實例,一律 `TypeError`,不因為列舉裡多了 `AUDIT_KEY` 這個成員而放寬。
- 新增的 `AUDIT_KEY` 值固定是 `"X-Dsp-Audit-Key"`,跟既有的 `"X-Fault"`(故障注入,由 `httpkit.read_fault` 專門處理)、`"X-Capability"` 都是不同字串,沒有任何路徑能讓 `ClientHeader.AUDIT_KEY` 被解讀成別的標頭名稱。
- 實際查了會呼叫 `request_json` 的三個分析端檔案(`src/rtb/analyzer/dsp_client.py:118,194`、`src/rtb/analyzer/inbox_client.py:61`),它們呼叫時完全沒有傳 `headers` 參數(用預設值 `None`),既不 import `ClientHeader` 也不 import `capabilitykit`,新標頭對分析端行程而言不可觸及,也沒有違反 `httpclient.py` 模組說明裡「不匯入 capabilitykit,免得分析行程間接載入憑證模組」這條設計限制——本次真正新增 `from rtb.capabilitykit import encode_audit_key` 的地方是 `src/rtb/ops/side_effects.py:31`,而 `ops/sli.py`/`ops/slo.py` 是單向依賴分析端的 `TaskReader`(`src/rtb/ops/sli.py:24`),分析端沒有任何檔案反向 import `rtb.ops.*`,所以稽核金鑰模組不會被分析行程間接載入。

判定:clean。

## 測試覆核

`PYTHONPATH=src /Users/enzo/rtb-p9i1/.venv/bin/python -m pytest tests/ops/test_side_effects.py tests/ops/test_slo.py tests/httpclient/test_httpclient.py -q` → 54 passed;`pytest tests/ -k "capabilitykit or dsp" -q` → 364 passed, 1284 deselected。均通過,未發現與本次修正相關的回歸。
