severity: major

### 1. 收件口回應的 block_code 未經過內容檢查就原樣寫入永久稽核記錄,可用來偽造稽核文字或注入控制字元
severity: major
blocking: 是 破壞本專案既有的「不可信文字不得未經過濾進入系統紀錄」合約(見 `attempt.py` 的 `is_clean_detail` 與提交 377103e「不可信廣告文字不能擴權的合約轉正」),且會污染 tasks 表這張只增不改的永久稽核歷史
引句:「block_code=_text(body, "block_code"))」

說明:`inbox_client.py` 的 `_accepted()` 只用 `_text()` 檢查 `block_code` 是不是字串(`isinstance(value, str)`),完全不限字元集合或內容,就放進 `Accepted`。`flow.py` 的 `_belongs_to()` 對 `block_code` 也只驗證「`state=="blocked"` 時必須是字串」這個型別一致性,不驗證內容;只有 `state=="blocked" and block_code=="version_changed"` 這條路徑會把 block_code 鎖死成常數字面值,其餘「blocked」但代碼不是 `version_changed` 的情況會落到:

```
return _closed(f"{answer.state}={answer.block_code}")
```

（`src/rtb/analyzer/flow.py:334`）這一整串未過濾的 `block_code` 會被塞進 `error_detail`,經 `commit_step` 寫進 `tasks` 表——這張表「只增不改」,是 Phase 5 自己宣稱要解決「衝突可查:軌跡、指標、稽核」的那個永久紀錄。

對照組:同一個程式庫在執行端(`src/rtb/executor/attempt_store.py:378`、`:417`)對「一樣來源不可信、一樣要寫進永久紀錄」的 `detail`/`reason` 文字,強制套用 `is_clean_detail`(只收 ASCII 可列印字元,doc 明講「限定 ASCII 擋掉全形冒號、同形字這類能偽裝成系統欄位的文字」),不合格直接丟 `InvalidOutcome` 拒收。分析端這次新增的 `block_code` 卻完全沒有套用同一道檢查,是同一個檔案曾經修過的同一類洞,這次在姊妹模組又開了一次。

而且收件口既有的「拒收」路徑(`_handle_rejection`)本身也是先比對 `_STALE_CODES`/`_PERMANENT_CODES` 這種固定代碼白名單、只有白名單內的字才會被放進例外訊息;唯獨這次新加的「已收下、bloked、且代碼不在已知分支」的成功路徑,漏掉了同等級的白名單檢查,前後不一致。

實驗驗證(唯讀,跑在 `/Users/enzo/rtb-production-agent-demo/.venv/bin/python`,`PYTHONPATH=src`,任務推進到 `HANDED_OFF` 後,餵一個帶 ANSI escape 與假稽核行的 `block_code`):

```
evil_block_code = "over_budget_cap\x1b[31mFAKE SYSTEM ALERT\x1b[0m\ninjected-fake-audit-line:approved-by-ops"
answer = Accepted(..., state="blocked", block_code=evil_block_code)
result = flow.advance(store, "t1", ..., submit=Counting(returns=answer), ..., operation_lookup=Counting())
```

輸出:
```
result state: blocked
error_detail repr: 'blocked=over_budget_cap\x1b[31mFAKE SYSTEM ALERT\x1b[0m\ninjected-fake-audit-line:approved-by-ops'
contains ESC byte: True
contains injected fake line: True
```

輸入 → 預期 → 實際:收件口(或中間被竄改/偽冒的收件口回應)回一個帶 ANSI escape、換行、偽造稽核字樣的 `block_code` → 預期跟 `attempt_store.py` 一樣被内容檢查擋下、或至少限縮成可列印 ASCII → 實際原樣寫進 `t1` 這一列永久不可刪改的 `error_detail`,之後任何讀這張表做稽核(CLI、終端機、報表)的人看到的是被攻擊者操控的內容,可以偽裝成系統自產的訊息、插入偽造的稽核行、或對終端機做逃逸序列注入。

補充說明目前的真實收件口伺服器端有 `block_code TEXT CHECK (block_code IN (...))`（`src/rtb/executor/inbox_store.py:90`）限制在一個固定枚舉內,所以「正常、未被竄改的收件口」目前不會真的送出任意字串;但分析端這支用戶端程式碼自己把 HTTP 回應當成不可信邊界處理(`_text()` 的存在本身就是為了防「型別不對的欄位」),卻唯獨對這個欄位的**內容**沒有比照既有慣例做白名單或字元集限制——只要中間層被接上不同的收件口實作、遭 MITM、或收件口本身出現這個新欄位的輸入驗證漏洞,分析端這一層完全沒有第二道防線,直接把資料寫進不可回收的稽核表。這正是本專案在 `is_clean_detail`、`_STALE_CODES`/`_PERMANENT_CODES` 等處反覆採用的「深度防禦」原則在這裡的缺口。
