severity: minor

## 查驗總結(給人看的白話版本)

這輪是修第 1 輪 8 條發現的「補丁的補丁」,我站在攻擊者角度重新走了一遍四個重點,並且做了變異測試(把修好的地方改回舊寫法,看新測試會不會抓到):

1. **作廢端點現在也吃故障注入**:查了 `httpkit.py` 的 `read_fault`——`X-Fault` 標頭只有伺服器啟動時明確帶 `--fault-injection` 旗標才會被接受,旗標沒開一律先擋 400、不碰任何狀態,而且這道閘在 `handle_request` 裡對所有路由(含 GET)統一套用,不是作廢端點獨有的新開口。所以把作廢併進共用的 `_commit` 故障注入外殼,不會讓外部攻擊者多一個可操弄的入口,反而是把第 1 輪抓到的「作廢端點測不出提交後逾時」補齊。把它改回舊寫法(繞過 `_commit`),`test_a_void_call_that_times_out_comes_back_as_no_answer` 立刻翻紅。
2. **`expected_version` 上界檢查**:`validate_key_and_version` 現在跟 `new_budget` 一樣用 `0 < value <= SQLITE_INTEGER_MAX`,寫入與作廢共用同一支函式,邊界一致。把上界拿掉,新測試 `test_an_out_of_range_expected_version_is_rejected_before_anything_is_voided` 立刻翻紅(用真的 2**63 送進 `/void`,確認三張表都沒被動到)。
3. **簽發改吃鍵參數**:追過 `_sign(proposal, key)` 的三個呼叫點(`_after_expiry`、`_reconcile_not_found`,以及沒改的 `_void_then_fail`),`key` 一律是同一次對帳/處理裡跟著同一個 `row`/`proposal` 走的那把嘗試紀錄自己的鍵(`attempt_store.latest(tx, key)` 與 `attempt_store.snapshot(tx, key)` 用同一個 `key` 查,結構上不會配錯對)。沒有找到攻擊者能讓呼叫端簽出「不屬於這筆嘗試的鍵」的路徑。把 `key or operation_key(proposal)` 改回一律 `operation_key(proposal)`,兩支新測試(`test_a_resigned_capability_after_expiry_signs_the_stored_key`、`test_a_resend_signs_the_stored_key_not_a_recomputed_one`)都翻紅。
4. **測試骨架不再排除作廢**:`test_execution_e2e.py` 的 `PlannedHandler.read_fault` 已經把 `self.path.endswith("/void")` 的排除拿掉,改成寫入與作廢都吃故障排程。把排除加回去,`test_a_void_call_that_times_out_comes_back_as_no_answer` 立刻翻紅,證明現在的骨架不是假綠。

也翻了 `r2-snapshot.patch` 的檔案清單(多出 `capability.py`、`errors.py`、`attempt_store.py`、`runner.py` 等),這些都不在 `r2-delta.patch` 裡,是第 1 輪就審過、第 2 輪沒再動的既有內容;`ROUTES` 清單也還是原本 4 個讀取端點加 2 個寫入端點加 1 個作廢端點,沒有新掛路由,第 1 輪之後沒有新開的攻擊面。

唯一找到的一點落差跟安全性無關,列在下面(minor)。

### 1. `operation_version` 的「行為不變」跟實際不完全一樣

severity: minor
blocking: 否 只是文件精度落差,不是行為缺陷:這支方法目前沒有任何生產路徑呼叫(只在 `DspPort` 介面與測試裡出現,`execution.py` 的對帳邏輯全部改走 `operation_record`),而且落差的方向是「失敗得更保守」(200 但欄位不全時,新版丟例外而不是回傳版本號),不是攻擊者能利用的漏洞。

引句:「增量 3 的介面,行為不變;解析只有 operation_record 一份,不各自再解析一次」

說明:舊版 `operation_version` 只要求 `status == 200` 且 `version_after` 是正整數就回傳版本號;新版全部委派給 `operation_record`,而 `operation_record` 透過 `_record()` 額外要求 `campaign_id`、`action`、`params` 都要是正確型別,缺一個就回 `None` 進而讓 `operation_record`(進而 `operation_version`)丟 `DspUnavailable`。

具體例子(已實測驗證):輸入——DSP 對 `/operations/k1` 回 `200 {"version_after": 3}`(缺 `campaign_id`/`action`/`params`);舊寫法預期回傳 `3`;新寫法實際丟出 `DspUnavailable("查詢回 200")`。

```
operation_version raised DspUnavailable 查詢回 200
```

file: `src/rtb/executor/dsp_client.py:73-76`(`operation_version`)
file: `src/rtb/executor/dsp_client.py:104-115`(`_record` 的欄位要求)

## 附註

第 1 輪的 8 條發現(x1-1、sarch-1/2/3、sec-1/2、s2-1、s3-1)都已在這份 delta 裡折入,重查沒有發現改法本身有錯,也沒有帶進新的可利用漏洞或假綠測試。sec-2(作廢表只增不減)第 1 輪就折成圖譜規則加 REVISIT、不改程式行為,這輪沒有再動它,不重報。
