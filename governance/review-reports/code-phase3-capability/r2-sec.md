severity: minor

已用凍結 patch 逐條核對四個問題:政策版本能否污染稽核紀錄(長度/字元)、讀取端點是否洩漏政策版本、新驗證順序是否開探測路徑、簽發器不等待開檔在競態下讀不讀到錯內容。前三者實測結果如下,第四者(競態)實際跑過確認安全,不構成發現。

### 1. DSP 對政策版本沒有自己的格式上限,理論上可塞任意長度/字元,但目前呼叫鏈到不了

severity: minor
blocking: 否 理由:要利用這條,前提是能自己組出一份「policy_version 內容不受 POLICY_PATTERN 限制」且簽章正確的憑證——而簽章需要共用金鑰,這正是題目排除、不重報的「同一作業系統使用者與對稱金鑰」已知限制;在目前真實呼叫鏈(`Proposal.__post_init__` → `CapabilitySigner.sign`)上,policy_version 一律先過 `POLICY_PATTERN = [A-Za-z0-9._:-]{1,64}` 才會被簽進憑證,DSP 端收到的值永遠已經是安全字元集、64 字以內。
引句:「policy_version=claims.policy_version)  # 只記不驗,供稽核」

說明:`src/rtb/dsp/capability.py:59` 的 `_nonempty_str` 只檢查「是字串且非空」,對長度、字元集合都沒有限制(DSP 刻意不依賴領域層的自我宣告)。我直接繞過 `CapabilitySigner`,用 `capabilitykit.encode` 自己組一份 claims(需要金鑰,等同已知限制範圍)送進活的 DSP 測試過:任意長度、任意 JSON 安全字元的 `policy_version` 都會被原樣寫進 `operations.policy_version`(TEXT 欄位無長度上限)。在現有程式碼路徑下,唯一能組出憑證的地方(`src/rtb/executor/capability_signer.py:124`)一定會先經過 `Proposal` 的自我驗證,所以這條路目前打不穿——列為 minor 是因為 DSP 這層本該是最後一道防線("刻意不依賴領域層"的設計初衷),但實際上完全仰賴上游沒被繞過,屬於縱深防禦缺口而非可獨立利用的洞。

### 2. 冪等鍵重用時,政策版本不進指紋比對,重放會悄悄留著舊政策版本、不會反映最新一次授權

severity: minor
blocking: 否 理由:不會造成未授權寫入(真正落地的那次狀態變更仍是第一次、且是在原始簽章授權下發生),也不需要偽造簽章;只是稽核紀錄的政策版本欄位可能落後於「這次呼叫實際使用的憑證」,屬於既有冪等語意的自然延伸(任何沒進指紋的欄位都會有同樣行為),不是本次改動新引入的授權漏洞。
引句:「policy_version: str | None = None  # 憑證上的政策版本:只記不驗,供稽核;不進指紋」

說明:`fingerprint()`(`src/rtb/dsp/store.py:76`)只取 `campaign_id, action, params, expected_version`,不含 policy_version。我啟動一個真的 DSP 行程實測:

1. 送一份合法簽章的憑證 `idempotency_key=k1, new_budget=150, expected_version=1, policy_version="policy-A"` → 200,`operations` 表寫入 `policy_version="policy-A"`。
2. 用同一個 idempotency_key、同樣的 body,但另一份合法簽章、`policy_version="policy-B-newer"` 的憑證再送一次 → 仍是 200(`"replayed": true`),但 `SELECT policy_version FROM operations` 還是只有 `[('policy-A',)]`——第二次呼叫方拿到成功回應,卻完全不知道稽核紀錄留的是舊政策版本,伺服器也沒有比對兩者是否一致或回報任何差異。若治理稽核依此欄位判斷「這筆寫入是在哪個政策版本下被核准的」,遇到政策版本更新後的重放呼叫就可能被誤導。

### 已查但未發現可利用問題的部分(附在此,不佔獨立發現編號)

- **新驗證順序(先查金鑰再讀標頭)**:比對前後兩版邏輯,差異只在「標頭重複」這個邊界情況下的錯誤碼優先序(`file: /Users/enzo/rtb-production-agent-demo/src/rtb/httpkit.py:185-189` 的 `single_header` 在重複標頭時丟 `duplicate_header`)。舊版是「先讀標頭」的參數求值順序把它蓋過「沒有可用金鑰」;新版讓「沒有可用金鑰」優先。攻擊者原本用正常方式(不帶重複標頭)在有無金鑰時得到的 503/401 區分度不變,這條修正沒有開出新的可觀測狀態,是單純修 bug。
- **簽發器不等待開檔的競態**:實際寫了一個 monkeypatch 實驗,在 `os.open()` 拿到 fd 之後、`os.read()` 之前用 `os.replace()` 把設定檔整份換掉,結果讀到的內容仍是「換檔前」那份——因為 `_read_config_securely` 一路都是對已開好的 fd 操作(dir_fd 相對開檔 + `O_NOFOLLOW`),檔名被置換不影響已開 fd 指向的 inode 內容。`O_NONBLOCK` 只是為了避免對具名管道卡住,不影響一般檔案的讀取語意。未發現「讀到換檔後錯誤內容」的問題。
- **讀取端點洩漏政策版本**:`_get_history`(用 `HistoryEntry`)、`_get_operation`/`_recorded_result`(顯式 `SELECT operation_id, campaign_id, action, version_after, committed_at ...`)、`OperationResult` dataclass 都沒有 `policy_version` 欄位,SQL 也是明確列欄位而非 `SELECT *`。核對原始碼確認政策版本目前不會經任何 HTTP 讀取端點外流。
