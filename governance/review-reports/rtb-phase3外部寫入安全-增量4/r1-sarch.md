severity: major

### 1. 「提交當下再驗憑證到期」的改法把驗證邏輯逼進儲存層,與既有「共用格式／執行行程簽發／DSP 驗證範圍」三層分工衝突

severity: major
blocking: 是 這是把 dsp/store.py(純儲存,對憑證「只記不驗」)變成第二個做憑證時間判斷的地方,而不是沿用既有唯一的驗證入口 dsp/capability.py + server.py 的 `_authorized_write`,屬於引入專案裡原本沒有的第二種驗證做法/跨層直呼,照字面實作會做錯。
引句:「寫入的資料庫交易裡、真的改之前,再比一次」

說明(白話):現在專案裡憑證的時間驗證只有一個地方做——`dsp/capability.py` 的 `verified_claims`/`_check_time`,在 `server.py` 的 `_authorized_write` 裡、呼叫 `store.execute()` 之前就做完;`dsp/store.py` 對憑證的唯一接觸是 `Operation.policy_version` 這個「只記不驗」的稽核欄位(`src/rtb/dsp/store.py:74` 的註解就是這樣寫的),`CampaignStore` 完全不知道有憑證這件事。

增量 4 卻要求把到期時間的複查搬進「寫入的資料庫交易裡、真的改之前」,而且明講「同鍵重放(已提交過的操作)照舊回原結果,不受這條影響」。對照 `_execute_in_transaction` 的實際結構(`file: src/rtb/dsp/store.py:307`),重放判斷 `_existing_operation`(`file: src/rtb/dsp/store.py:322`)在最前面、真正的狀態變更 `_apply`(`file: src/rtb/dsp/store.py:345`)在最後面——要做到「重放不受影響、只擋新操作」,這個到期時間檢查在邏輯上就只能插在這兩者之間,也就是必須落在 `dsp/store.py` 內部,而不能留在 `server.py` 的 `_authorized_write`(`file: src/rtb/dsp/server.py:160`)裡對 `store.execute()` 呼叫之前做一次就了事(那樣分不出「這筆是不是已經進到重放判斷」)。

這代表要嘛把憑證到期時間當新欄位塞進 `Operation`/`_execute_in_transaction`,讓儲存層第一次直接判斷「憑證」語意;要嘛在 `CampaignStore.execute()` 開一個新的、專案裡其它地方都沒用過的「交易中途回呼」機制。兩者都是增量 2 花三輪設計審才定下的「共用層只放格式機制、簽發放執行行程、驗證範圍放在模擬 DSP」分工(對照 `dsp/capability.py` 的 `verified_claims`,`file: src/rtb/dsp/capability.py:94`)之外,新開一條驗證路徑,而不是照現有模組邊界走。

具體例子:一筆合法請求在 `verified_claims` 通過時憑證仍在有效期內(未過期),但因為 `delayed_response` 故障在 `_apply_fault_before_commit`(`file: src/rtb/dsp/server.py:198`)裡睡到超過到期時間才進 `store.execute()`。照現有分工,`store.execute()` 不會、也不該知道「到期時間」是什麼,無法在真正改狀態前擋下它;要讓這筆被正確擋成 `capability_expired` 且三張表不變,唯一可行的路是讓 `dsp/store.py` 也學會驗憑證時間——這正是本節設計字面要求的做法,也正是這裡判定為 major 的「第二種做法」。
