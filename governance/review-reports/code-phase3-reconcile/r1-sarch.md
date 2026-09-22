severity: major

### 1. 作廢端點沒有走既有寫入端點共用的故障注入外殼,自己另開一條路
severity: major
blocking: 是 兩個既有寫入端點都經同一支 `_write` 完成故障注入與提交;新端點另開一條不經過它的路徑,屬於對同一件事(DSP 寫入端點的提交流程)引入第二種做法
引句:「result = store.void(key)」
說明:`_update_budget`、`_pause_campaign` 最後都經 `_authorized_write` 呼叫 `self._write(store, op, fault)`,由這支共用方法在提交前套用故障(`_apply_fault_before_commit`,涵蓋 `timeout_before_commit`/`transient_5xx`/`permanent_validation_error`),提交後再視 `timeout_after_commit` 延遲回應。新的 `_void_operation` 沒有走這條路:它直接呼叫 `store.void(key)` 後自組回應,方法簽章上的故障參數照讀取端點的慣例命名成 `_fault`(收下不用),整段故障注入邏輯被繞過。實際效果是:開著 `--fault-injection`、對 `/campaigns/{id}/void` 送 `X-Fault: timeout_before_commit` 之類的標頭,`read_fault` 照樣放行(不報 400),但伺服器會正常提交、不會像其他寫入端點一樣掛起或延遲回應——同一族「DSP 寫入端點」出現兩種故障行為,而且沒有任何測試涵蓋這個落差。設計筆記明講作廢端點要「跟既有寫入端點同一種寫法」,這裡沒有做到。
file: `src/rtb/dsp/server.py:184`(既有寫入端點怎麼接 `_write`)
file: `src/rtb/dsp/server.py:218-225`(`_write` 套用故障注入的位置)
file: `src/rtb/dsp/server.py:186-204`(`_void_operation` 全程沒有呼叫 `_write`)

### 2. DSP 用戶端新增的查詢方法跟既有方法打同一支端點,各自解析一份
severity: minor
blocking: 否 設計文件(增量 4 段)已明講刻意保留舊介面「原樣保留」,是為了不動增量 3 已凍結的合約,經設計審過,不是漏改
引句:「def operation_record(self, key: str) -> OperationRecord | None:」
說明:`operation_version` 與新增的 `operation_record` 都打 `GET /operations/{key}`、都做一樣的 404/非 200 判斷,只是回傳形狀不同;`operation_record` 其實是 `operation_version` 的超集(`OperationRecord.version_after` 就是 `operation_version` 回的那個值)。對帳與執行後驗證改呼叫 `operation_record` 之後,`operation_version` 在正式業務邏輯裡已經沒有呼叫點,只剩測試直接呼叫用戶端本身。測試替身(`tests/executor/fakes.py`)把兩支方法疊在共用的 `_lookup` 上;正式的 `DspClient` 沒有這樣收斂,兩支各自獨立解析一次回應。列出來備查,不建議動——已在設計審記錄裡說明是刻意選擇。
file: `src/rtb/executor/dsp_client.py:73-89`

### 3. 兩張回應對照表各自複製了一份逐字相同的狀態碼比對輔助函式
severity: minor
blocking: 否 純重複程式碼,行為完全一致,不影響任何判斷結果,也不是「第二種做法」(對照表本身仿照既有架構風格,規則內容不同)
引句:「def _void_status(low: int, high: int, error: str | None = None) -> Callable[[VoidAnswer], bool]:」
說明:既有的 `_status`(給 `RESPONSE_TABLE` 用)與新增的 `_void_status`(給 `VOID_TABLE` 用)函式本體逐字相同,只有型別註記(`WriteAnswer` vs `VoidAnswer`)不一樣。兩張表本身是刻意仿照既有「唯一一份對照表」的架構風格另開一張(這點沒問題,兩者規則內容真的不同),但這段比對邏輯可以共用,不必照抄一次。
file: `src/rtb/executor/execution.py:134-138`(既有 `_status`)
file: `src/rtb/executor/execution.py:211-215`(新增 `_void_status`)

---
補充說明審查範圍:本次 sarch 座位材料照 dispatch 只給 `r1-snapshot-src.patch`,上面三點都以它為主;為了判斷「跟既有寫法一不一樣」,額外讀了 `src/rtb/dsp/server.py`、`store.py`、`capability.py`、`capability_signer.py`、`capabilitykit.py`、`execution.py`、`dsp_client.py`、`attempt_store.py`、`runner.py` 的現況(HEAD),以及設計計劃筆記「RTB_Phase3外部寫入安全_計劃」增量 4 段與 Systems 筆記(Mock-DSP、執行迴圈)做比對基準。三層分工(`capability.py`/`capability_signer.py`/`capabilitykit.py`)、作廢表與遷移(`store.py`)、對帳流程(`execution.py`)這幾塊查下來寫法跟既有慣例一致,沒有發現跨層直呼或第二種做法,故沒有列成發現。
