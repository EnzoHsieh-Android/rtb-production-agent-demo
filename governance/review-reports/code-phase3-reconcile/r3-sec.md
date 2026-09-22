severity: clean

## 查驗總結(給人看的白話版本)

這是第 3 輪(最後一輪),站攻擊者角度重新走了一遍派工詞列的四個重點,並用變異測試驗證關鍵修正不是裝飾:

1. **主看 r3-delta.patch(查詢方法拆分 + 作廢逾時測試改法)**:`DspClient.operation_version` 從「轉呼叫 `operation_record`」改回各自解析(共用的只有 `_lookup` 這個「查詢與查不到判斷」),`operation_record` 的欄位要求(campaign_id、action、params、version_after)完全沒變。這支方法目前在 `src/` 裡沒有任何生產路徑呼叫(`execution.py` 的對帳與驗證邏輯全部走 `operation_record`),所以就算之前(第 2 輪)那個「轉呼叫」寫法有行為缺陷,也不構成攻擊者可利用的洞,只是文件精度問題——這點第 2 輪的資安席（`r2-sec.md`）已經抓到並修正。我在暫存複本(`/private/tmp/.../scratchpad/r3check`,未動 repo)把 `operation_version` 改回轉呼叫 `operation_record` 重跑新測試 `test_an_old_style_operation_answer_still_reads_back_its_version`,立刻翻紅(`DspUnavailable: 查詢回的操作紀錄讀不懂`),證明這支新測試不是假綠、修法是有測試守著的真修正。

2. **作廢逾時測試改查作廢表**:舊寫法(第 2 輪)用 `store.void(key).state == "voided"` 當「DSP 真的沒作廢」的證據,但作廢本身是冪等的——不管剛才有沒有作廢過,再呼叫一次都回 `"voided"`,分不出兩種情況(第 2 輪 `x1-2` 已抓到)。新寫法直接查 `voided_keys` 表數筆數,我在暫存複本把 DSP 端 `_commit` 的提交順序改成「先提交、後套故障」(模擬作廢在逾時前其實已經悄悄成功),新測試立刻翻紅(`assert 1 == 0` 失敗),證明目前這版測試是真的守著「作廢呼叫逾時時,DSP 沒有留下作廢紀錄」這個安全性質,不是假綠。

3. **掃過 r3-snapshot.patch 全貌(整個增量 4)**,针對派工詞列的四個攻擊面逐項核對,結論與第 1 輪資安席(`r1-sec.md`)一致、且這輪沒有再變動這些程式:
   - **作廢端點的授權與範圍**:`check_scope` 的六項比對(campaign_id、action、idempotency_key、tenant、new_budget、expected_version)全部要對上聲明,`action` 被端點寫死成 `"void_operation"`,一般改預算/暫停憑證因動作不符必被拒(403),測試 `test_voiding_needs_a_void_capability_scoped_to_the_key` 覆蓋且我重讀過邏輯與 `capability_signer.py` 的 `sign_void`/`_tenant_of` 一致,分析端沒有簽發金鑰,不構成新的越權路徑。
   - **對帳會不會被 DSP 回應牽著誤終結**:`VOID_TABLE`/`RESPONSE_TABLE` 用 `state` 欄位互斥判斷「已作廢」與「已提交(查到)」,`operation_voided` 規則刻意排在 `version_conflict` 之前;`store.void()` 與 `store.execute()` 共用同一把 `BEGIN IMMEDIATE` 寫入鎖,查作廢表的語句留在寫入交易內(不是交易外查完再判),`_found`/`record_matches` 要求 campaign_id、action、new_budget、expected_version 全部對上才採信,對不上一律轉人工(`idempotency_conflict`)而不是靜默接受。DSP 在本系統的信任模型裡本來就是「外部事實來源」,不在威脅模型內。
   - **金鑰與憑證外洩**:嘗試紀錄只存憑證到期時間,不存 token 本體;DSP 端各種拒收例外一律回固定代碼、不回顯聲明內容;`capability_signer.py` 讀租戶設定用 `O_NOFOLLOW`+目錄先開+擁有者與權限位元檢查,不留 TOCTOU 開檔窗口。
   - **被提示注入的提案鎖死廣告或讓迴圈停機**:分析端不可信欄位在存入嘗試紀錄前已經過型別與白名單驗證;`sign`/`sign_void` 只接受設定檔明列的租戶與廣告,作廢動作只能鎖住「同一把冪等鍵」(等於同一份 payload 的同一個邏輯操作),換內容就換鍵,不會連坐到同廣告其他操作;會讓整個執行迴圈 `ExecutorHalted` 的路徑(設定檔壞掉/不安全、本地請求錯誤 4xx)都不是廣告內容能單獨觸發的,屬既有設計(增量 1 起就有),不是這批改動新開的洞。

跑了 `pytest tests/executor/test_execution_e2e.py tests/dsp/test_void.py tests/executor/test_reconcile.py tests/executor/test_execution.py`,117 條全綠。

沒有發現新的可被利用漏洞,也沒有發現假綠測試。
