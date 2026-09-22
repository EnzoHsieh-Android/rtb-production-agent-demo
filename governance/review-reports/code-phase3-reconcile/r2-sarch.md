severity: minor

### 1. `operation_version` 轉呼叫 `operation_record` 後驗證條件變嚴,「行為不變」的說法不成立
severity: minor
blocking: 否 `Executor` 正式流程只呼叫 `operation_record`,`operation_version` 在生產路徑上目前沒有呼叫點(只剩對外介面與測試在用),不會造成錯誤判斷或資料損壞,純屬合約精度落差
引句:「增量 3 的介面,行為不變;解析只有 operation_record 一份,不各自再解析一次。」
說明:合併前 `operation_version` 只驗 `version_after` 是正整數,其餘欄位不管,是最小需求;合併後改成呼叫 `operation_record`,而 `operation_record` 用的 `_record()` 額外要求 `campaign_id`、`action` 是字串、`params` 是 dict 都成立才算「讀得懂」,否則整包當成 `None` 再讓外層丟 `DspUnavailable`。也就是說,DSP 回應只要 `version_after` 有效、但 `campaign_id`/`action`/`params` 缺漏或型別不對,舊版會照常回版本號,新版會改丟例外——這是實質的行為變更,跟 docstring 寫的「行為不變」對不上。實測驗證:把 `request_json` 打樁成只回 `{"version_after": 5}`,舊邏輯應回 `5`,新版 `client.operation_version("k1")` 直接丟出 `DspUnavailable: 查詢回 200`。這正是 sarch-2 折法要處理的「查詢方法改成轉呼叫」那一格,轉呼叫這件事本身沒問題(不是第二種做法、也沒有跨層直呼),但把兩支原本邊界不同的檢查合併成一份時,少了「取交集」的判斷,把 `operation_record` 較嚴的合約整個套到 `operation_version` 頭上。目前的測試(`tests/executor/test_execution_e2e.py`)沒有覆蓋這個落差,新增的邊界測試（2**63 那組)也只驗了 `client.write`,沒有再叫一次 `operation_version`。
file: `src/rtb/executor/dsp_client.py:73-76`(`operation_version` 轉呼叫)
file: `src/rtb/executor/dsp_client.py:104-116`(`_record` 額外要求的欄位)

---
其餘三個重點格查過沒有發現第二種做法或跨層直呼:
- **共用提交外殼 `_commit`**:`_write`(改廣告)與 `_void_operation`(作廢)現在都只經 `self._commit(...)` 一條路提交,故障注入(`_apply_fault_before_commit`)套用順序一致,沒有找到繞過 `_commit` 直接呼叫 `store.execute`/`store.void` 的第二條路。
- **`_status` 合併後用 `Any`**:確認 `WriteAnswer` 與 `VoidAnswer` 都有 `.status`/`.error` 兩個同型欄位,結構完全相容;`Any` 犧牲的是「未來如果拿去配第三種沒有 `.error` 的型別,mypy 抓不到」這種靜態保障,目前兩個實際用法都正確,不是跨層或第二做法問題。
- **共用格式檢查加上界**:`validate_key_and_version` 的新上界 `SQLITE_INTEGER_MAX` 寫入端點(經 `validate`)與作廢端點(直接呼叫)共用同一支函式、同一個模組常數,沒有另外找到誰在別處手抄一份 `2**63-1` 的上界判斷來繞過它(`domain/proposal.py`、`dsp_client.py` 各自的同名常數是既有分層下的既有重複,不是這輪改動新增的)。

補充審查範圍:本輪只給 `r2-delta.patch`,對照 `r2-snapshot.patch` 與 HEAD 現況讀了 `src/rtb/dsp/server.py`、`store.py`、`executor/dsp_client.py`、`executor/execution.py`,並在臨時目錄用 `.venv` 的 Python 跑了一段打樁實驗驗證上述行為差異(未修改 repo)。`capability.py`、`capability_signer.py`、`attempt_store.py`、`runner.py` 這輪 delta 沒有觸及,故未列入發現。
