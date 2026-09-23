severity: clean

# 對答案審查:增量 3b(分析側任務租約)spec vs diff

範圍:spec 檔〈為什麼租約不照抄收件表的改欄位〉〈租約紀錄表〉〈推進函式怎麼用租約〉七步與不續租〈提交怎麼核對租約〉〈租約長度與外部呼叫逾時〉〈保證什麼、不保證什麼〉〈怎麼測〉〈合約〉S150–S160。〈事故 F3 轉正〉依裁定不算(3a 合併後才做),diff 也確實沒有動 Verification/Systems 檔,一致。

## 逐條裁定

### 〈為什麼租約不照抄收件表的改欄位〉— 已實作
- spec:「在同一個資料庫檔、同一個任務模組裡開一張只增不改的租約紀錄表」「任務模組仍不得出現任何改或刪的資料庫敘述」。
- diff:`src/rtb/analyzer/task_store.py:178-180` 新增 `CREATE TABLE IF NOT EXISTS task_leases (task_id TEXT NOT NULL, lease_seq INTEGER NOT NULL, owner TEXT, expires_at TEXT NOT NULL, PRIMARY KEY (task_id, lease_seq));`,通篇只有 INSERT,無 UPDATE/DELETE 敘述;`tests/analyzer/test_task_lease.py:681-682` 的 `test_opening_an_old_task_database_adds_the_lease_table_without_touching_history` 對整個模組原始碼做字串掃描斷言 `"UPDATE " not in source and "DELETE " not in source`,對應 [S157]。

### 〈租約紀錄表〉— 已實作
- 欄位/主鍵:`task_store.py:178-180` 與 spec「任務編號、租約序號、擁有者、到期時間;主鍵是(任務編號、租約序號)」一致。
- 取得列/放掉列:`task_store.py:304-316`(`acquire_lease`)owner=呼叫端識別、expires_at=now+LEASE_DURATION;`task_store.py:345-349`(`_append_release`)owner=NULL、expires_at=now,與 spec「取得列……放掉列:擁有者空白、到期時間等於現在」一致。
- 是否持有:`task_store.py:231-233` `_is_live`「有擁有者而且還沒到期」,與 spec「目前那一列有擁有者,而且到期時間晚於呼叫端的現在時間」一致。
- 收據:`LeaseReceipt(task_id, lease_seq, owner)`(`task_store.py:207-213`),與 spec「取得時回傳(任務編號、租約序號、擁有者)」一致。
- 放掉一律帶條件:`release_lease`(`task_store.py:318-326`)在 `immediate_transaction` 內先 `_holds` 核對才寫,與 spec「都在一個立即取得寫入鎖的交易裡先核對……是才寫放掉列;已被別人接手就什麼都不寫」一致。
- 擁有者用隨機識別非行程號:`flow.py:70` `uuid.uuid4().hex`,與 spec「每一次推進呼叫自己產生一個隨機識別,不是行程編號」一致。

### 〈推進函式怎麼用租約〉七步 — 已實作
逐步核對 `flow.py:63-110`(`advance` / `_advance_holding`):
1. 讀目前那一列,沒有丟 `TaskNotFound`;終點/已交接直接回傳、不取租約 — `flow.py:63-67` 與取得租約(第 70 行)之間即是此順序,完全吻合 spec「這兩種不會呼叫任何外部介面」。
2. 取得租約,拿不到直接回傳第 1 步讀到的狀態、不呼叫任何介面、不寫任何列 — `flow.py:70-72`。
3. 拿到後重讀目前那一列,序號不同就放掉租約、回傳重讀到的狀態、不呼叫外部介面 — `flow.py:90-95`。
4. 照舊做這一步 — `flow.py:96`。
5. 沒進展則放掉租約回原狀態 — `flow.py:97-99`。
6. 有結果帶收據提交,提交與放掉租約同一交易 — `flow.py:101-106`(`commit_step(..., lease=lease)`),放掉列寫在 `task_store.py:298-299` 的同一個 `with immediate_transaction` 區塊內。
7. 行程內例外往外傳前先放掉租約、放掉本身資料庫層錯誤吞掉不蓋原例外 — `flow.py:73-79` 的 `except BaseException` 與 `flow.py:112-120` 的 `_release_keeping_the_original_error`(只吞 `sqlite3.Error, DatabaseBusy`),與 spec「吞的範圍比照既有『記一筆對外呼叫』那支只吞資料庫層錯誤,程式錯誤不吞」一致。
- 不續租:全 diff 未新增任何續租/延展租約的函式或呼叫,與 spec「不需要續租」「一次一步」一致。

### 〈提交怎麼核對租約〉— 已實作
- `commit_step` 新增可選 `lease` 參數(`task_store.py:257`),在 `immediate_transaction` 內、核對序號之前先呼叫 `_lease_allows`(`task_store.py:274-276`),對應 spec「在既有的寫入交易裡、核對序號之前,先核對租約」。
- 帶收據:`_lease_allows` 呼叫 `_holds` 只核對 `(lease_seq, owner)` 不核對到期時間(`task_store.py:335-343`),與 spec「核對的是號碼,不是時間」一致;S158 測試 `test_an_expired_holder_that_nobody_took_over_still_commits` 驗證過期但沒接手仍寫得進去。
- 不帶收據:`_lease_allows` 落到 `current is None or not _is_live(current, now)`(`task_store.py:342-343`),與 spec「別人持有有效租約時回沒有寫入」一致。
- 序號核對保留在租約核對之後、順序不變(`task_store.py:277-282`)。
- 租約不符跟序號不符一樣回 False、不停機不丟例外,`flow.py` 端只用 `if not committed:` 分支處理。

### 〈租約長度與外部呼叫逾時〉— 已實作
- `LEASE_DURATION = timedelta(seconds=60)`(`task_store.py:185`),註解寫明「暫用,沒有實測校準……這條不等式目前沒有機械守衛」,與 spec 措辭一致。

### 〈保證什麼、不保證什麼〉— 已實作(用測試固定)
- 「同一時間只有一個持有有效租約的呼叫端會呼叫外部介面」由 [S150][S151] 固定。
- 「過期被接手後,舊持有者醒來寫不進任何一列」由 [S152][S159] 固定。
- 「不保證當機後不重做」由 [S153] 固定(斷言 `after.call_count == 1`,共兩次)。

### 〈怎麼測〉/合約 [S150]–[S160] — 全部已實作,測試命名與行為逐一核對
| 合約 | spec 測法要點 | diff 位置 | 裁定 |
|---|---|---|---|
| [S150] | 真並行、同步屏障、決策內等事件、數呼叫次數不數回傳值 | `test_task_lease.py:446-480` | 已實作 |
| [S151] | 蒐證/分析/已提案三態、別人持有時不呼叫任何介面 | `test_task_lease.py:484-500`(parametrize 三態) | 已實作 |
| [S152] | A 先持有卡住、B 過期後接手也卡住,先放 A 被擋、再放 B 寫入 | `test_task_lease.py:504-533` | 已實作 |
| [S153] | 子行程 `os._exit(9)` 猝死,到期前不重呼、到期後重呼一次 | `test_task_lease.py:537-578` | 已實作 |
| [S154] | 包一層 `acquire_lease`,先讓另一呼叫端做完一步(含推到終點) | `test_task_lease.py:581-607`(parametrize `NeedsFreshEvidence`/`NoAction`,`NoAction`為終點) | 已實作 |
| [S155] | 沒進展、行程內例外都要放掉且不必等到期 | `test_task_lease.py:611-628` | 已實作 |
| [S156] | 不帶收據時別人持有擋、沒人持有照舊 | `test_task_lease.py:632-640` | 已實作 |
| [S157] | 舊庫開啟自動多表、既有資料不動、模組內無 UPDATE/DELETE | `test_task_lease.py:644-682` | 已實作 |
| [S158] | 過期沒人接手仍寫得進去(核對號碼不核對時間) | `test_task_lease.py:686-692` | 已實作 |
| [S159] | 遲來放掉不寫任何列,接手者仍有效、第三方拿不到 | `test_task_lease.py:696-725` | 已實作 |
| [S160] | 放掉失敗(DatabaseBusy)不蓋原例外 | `test_task_lease.py:729-744` | 已實作 |

### 〈不做的事(增量 3b 範圍)〉— 邊界有守住
- 不做續租:確認無相關程式碼。
- 不做分析結果快取、不要冪等鍵:確認無相關程式碼。
- 不擋當機後重做:[S153] 明確容許並斷言重做一次。
- 不做分析行程啟動程式/派工:diff 只動 `flow.py`、`task_store.py`、測試檔,未新增任何啟動或派工程式。
- 不做投遞次數與死信:確認無相關程式碼。
- 不動執行側任何程式:diff 三個檔案全在 `src/rtb/analyzer/` 或 `tests/analyzer/`,未觸及 `src/rtb/executor/`。

## 多做(diff 有 spec 沒有的行為變更)
無。`_lease_allows` 內多一句 `lease.task_id == task_id` 的防呆(`task_store.py:340-341`)屬於同一份收據本來就只會配對同一個 task_id 的內部自證,不改變任一條合約描述的可觀察行為,不算「多做」。

## ⚠ 判不準
無。

## 縮水(做了但比 spec 少)
無。

## 未實作
無。

## 多做
無(見上方說明)。

縮水+未實作共 0 條
