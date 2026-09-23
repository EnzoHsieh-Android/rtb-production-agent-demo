severity: clean

# 外家否決席(頂替 Codex)— Phase 7 增量 2 代碼審 r1

審材:/Users/enzo/rtb-3b/governance/review-reports/code-phase7-inc2/r1-snapshot.patch(已核對與 `git diff -U10 15ba6a7..HEAD -- src tests` 逐字相同,HEAD = b042431)。
實驗一律在 scratchpad 臨時目錄(`p8v.vD0d` 複製 src/tests、`p8clone` 為 HEAD 的完整 clone),未動 repo 任何檔。

只找 blocking 級,逐項查完沒有找到 blocker/major。

### 已看:名稱能不能被任何寫入端點改掉
- 路由表只有兩種方法;HTTP 基礎只實作 `do_GET`、`do_POST`(file: `src/rtb/httpkit.py:124`、`src/rtb/httpkit.py:127`),POST 路由只有 update_budget、pause_campaign、void_operation(file: `src/rtb/dsp/server.py:80`),沒有建檔或改名的路由;`seed_campaign` 在 src 內沒有任何呼叫者(grep 過 src)。
- 儲存層唯一的 `UPDATE campaigns` 只寫預算、狀態、版本三欄(file: `src/rtb/dsp/store.py:415`);`_next_state` 兩個分支都帶原名稱,引句:「return Campaign(campaign.id, budget, campaign.status, campaign.version + 1, campaign.name)」。`Campaign` 的 name 沒預設值,漏帶會直接型別錯誤而不是靜默清空。
- 寫入回應用的是操作結果(OperationResult),不含 Campaign,所以名稱也不會經寫入回應外流或被重放紀錄改寫。
- 臨時目錄實跑:舊資料庫的廣告改暫停後 `Campaign(id='old', budget=77, status='paused', version=5, name='')`,名稱沒變。

### 已看:舊資料庫開啟會不會壞或丟資料
- 「全部補好就直接返回」的判斷已把 name 算進去,引句:「if {"tenant", "name"} <= columns and {"policy_version", "expected_version"} <= op_columns:」;拿到立即寫入鎖後再查一次才加欄位。
- 臨時目錄實跑了三種舊資料庫:(a) 四個欄位都缺(沒有 tenant、沒有 name、操作紀錄也沒有 policy_version/expected_version),8 條執行緒同時開啟 → 0 個錯誤;讀回 `budget=77, status='active', version=4, name=''`、租戶 `t-default`,舊操作列完整保留(新兩欄為 None),之後寫入照常(version 4→5)。(b) 有 name 但沒有 tenant → 名稱 'hi' 保留、租戶補預設值。(c) 測試裡的「有 tenant 沒 name」情況。
- 舊測試裡的原始 SQL:`SELECT *` 前後快照比對(file: `tests/dsp/test_capability.py:70`、`tests/dsp/test_void.py:43`)多一欄仍然前後一致;`INSERT INTO campaigns (id, budget, status, version)` 有列欄位名(file: `tests/dsp/test_void.py:177`),新欄位靠預設值,不會壞。全部 tests/dsp 通過。

### 已看:最壞情況名稱會不會讓分析端讀回應超過 64 KB
- 上限檢查,引句:「if not isinstance(name, str) or len(name) > MAX_CAMPAIGN_NAME_LENGTH:」;`len` 算的是碼位,一個表情符號算 1 個字,伺服器 `json.dumps` 預設 ASCII 逃脫(file: `src/rtb/httpkit.py:236`),每個碼位最多 12 位元組。
- 臨時目錄實跑,真的 DSP 伺服器加分析端 `make_client` 加執行側 `DspClient.read_campaign`,五種 4096 字的名稱:

```
emoji 49225 ['CAMPAIGN_STATE', 'METRICS', 'CAMPAIGN_TEXT'] True 512 CampaignView(budget=100, status='active', version=1)
ctrl  24649 ...(\x00 每字 6 位元組)
quote  8265 ...
cjk   24649 ...
mix   36937 ...(表情符號與控制字元交錯)
```
  最壞情況 49,225 位元組 < 65,536;分析端五種都照常回三筆證據,名稱截到 512 字、已截斷標記 True。沒有比「一字 12 位元組」更大的單碼位逃脫,所以名稱塞不爆回應。
- 4097 字、`"\udc00"`、`"a\ud83d"` 都丟 ValidationRejected;剛好 4096 字可以建。

### 已看:執行側讀廣告現況會不會被多出的名稱影響
- 執行側只取 budget、version、status 三個欄位建 `CampaignView`,多出的鍵直接忽略(file: `src/rtb/executor/dsp_client.py:47`–`55`)。上面實跑:五種最壞名稱下 `read_campaign` 都正常回傳,回應都在 64 KB 以內,不會變成 DspUnavailable。

### 已看:Systems/Mock-DSP.md 的 ★INVARIANT★(冪等鍵最多套用一次、寫入原子、F1 的 DSP 側、預期版本不符一律拒收)
- 這份 diff 沒動 `execute`、`_apply`、冪等紀錄、作廢、交易邊界;`_next_state` 只多帶一個欄位。綁在這些合約上的測試都在 tests/dsp,跟 tests/kit、tests/analyzer 一起在臨時目錄跑:473 passed。
- 全套在 HEAD 的完整 clone 跑:複製目錄裡有 31 條失敗(靜態檢查、mypy SARIF、metrics),在完整 clone 重跑同樣四支檔全部通過(134 passed),是只複製 src/tests 造成的,跟這份 diff 無關。

### 已看:S208、S209、S219 字面
- S208:測試用只有 tenant 沒 name 的舊表,斷言預算、狀態、版本不變、名稱是空字串、租戶是 `t-acme`,也做了第二次開啟。字面「預算、狀態、版本、租戶都不變」有做到。
- S219:4097 字與含孤立代理字元的名稱都斷言 ValidationRejected 而且表是空的(另外也測了 42、None);4096 個需要代理對的字元,透過真的伺服器 GET 整份回應,斷言 `len(raw) < MAX_RESPONSE_BYTES`。字面有做到。
- S209:斷言 GET 回傳建檔時的名稱、改預算加暫停之後名稱不變、路由表的 POST 端點恰好是那三個,並掃儲存層原始碼,確認每個 `UPDATE CAMPAIGNS` 字串都不含 name(有「守衛的守衛」確認真的掃到東西)。字面有做到。
- tests/kit/test_shared_base.py 預期回應只多了 `"name": ""`,其餘逐字相等的斷言沒動,跟計劃「受影響的既有測試」一節一致。

總結:最高 severity clean,blocking 0 條。
