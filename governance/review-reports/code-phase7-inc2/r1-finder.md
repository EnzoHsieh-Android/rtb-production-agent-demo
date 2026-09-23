severity: clean

# 外家 finder 審查(Phase 7 增量 2:模擬 DSP 廣告名稱欄位)

審材:`/Users/enzo/rtb-3b/governance/review-reports/code-phase7-inc2/r1-snapshot.patch`(已核對跟 repo HEAD b042431 的 src/tests 一致)。所有實驗都在臨時目錄 `scratchpad/p8lqFl`(repo 複本)裡跑,repo 本身沒有動。

### 已看:相關測試在複本上實跑
- `tests/dsp tests/kit tests/analyzer`:473 passed。
- 其餘測試:674 passed、7 failed。7 條失敗全部在 `tests/test_static_wiring.py` 與 `tests/domain/test_metrics.py::test_lumos_lint_command_never_bypasses_the_nested_domain_config`,都是讀 CI 流程檔與 lumos 設定的靜態接線檢查。臨時複本沒有帶這些設定檔,所以失敗是環境造成的,跟這次改動無關。

### 已看:遷移與併發(S208)
- 用一個比租戶欄位還早的舊資料庫測:廣告表沒有租戶也沒有名稱,操作表沒有政策版本與預期版本。8 條執行緒同時開 `CampaignStore`,結果 0 個例外,讀回 `Campaign(id='o', budget=5, status='active', version=2, name='')`,租戶是 `t-default`。之後改預算照常升到版本 3,名稱仍是空字串。拿到鎖以後會重查欄位,重查有效。
- 新舊版本並存:舊程式照舊寫法插入廣告、不帶名稱欄位,新程式讀回名稱是空字串,不會壞(靠 `NOT NULL DEFAULT ''`)。
- 「欄位都齊就直接返回」的判斷已經把名稱算進去,diff 裡有 `if {"tenant", "name"} <= columns and ...`。

### 已看:建檔名稱驗證(S219)
- 名稱含空字元、換行、從右到左覆寫字元、表情符號時,寫進資料庫再讀回、以及經 HTTP 查詢回來,都跟原字串一致。
- 回應用預設的 `json.dumps`(`ensure_ascii=True`,位置在 `src/rtb/httpkit.py:236`)。每個字最多逃脫成 12 位元組(需要代理對的字元),4096 字約 48 KB,低於 `src/rtb/httpclient.py:21` 的 64 KB 上限。這個估算成立。
- 驗證的順序是先查型別、再查長度、最後查能不能編成 UTF-8。四種壞輸入(超長、孤立代理字元、整數、None)都會丟 `ValidationRejected`,資料庫一列都不會寫進去。這是測試本身就在斷言的事,我自己也跑過。
- 另外看到但不列 finding 的一點:如果傳進來的是一個改寫了 `__len__` 的 str 子類別,可以繞過長度上限(實測 5000 字被收下)。但 `seed_campaign` 沒有任何 HTTP 或外部入口,只有測試用來建種子資料,找不到會出事的真實情境,所以不列。

### 已看:寫入保留名稱、沒有改名稱的端點(S209)
- `_next_state` 的兩個分支都帶上了 `campaign.name`。`Campaign.name` 沒有給預設值,所以將來有人建構時漏帶會直接 TypeError,不會悄悄變成空字串。
- `_apply` 裡的 UPDATE 只改預算、狀態、版本三欄。
- 變異實驗:把 UPDATE 改成用開頭帶換行的三引號字串寫 `name = 'pwned'`。原始碼掃描那一段抓不到這種寫法,但同一支測試前面的行為斷言(`assert campaign.name == ...`)會翻紅。所以測試整體不會假綠。
- 路由表確實只有 `pause_campaign`、`update_budget`、`void_operation` 三個寫入端點。

### 已看:模擬 DSP 既有的四條不變量
- 冪等只套用一次、寫入全有或全無、F1 逾時語意、版本不符一律拒收:四條都走 `execute` → `_execute_in_transaction` → `_apply` 這條路。這次改動沒有碰交易邊界、指紋、冪等紀錄或版本比對,只在「算下一個狀態」時多沿用一個欄位,也只在讀取時多選一欄。這四條的綁定測試都在 `tests/dsp` 裡,在複本上全綠。

### 已看:其他讀查廣告回應的程式
- 執行端 `src/rtb/executor/dsp_client.py:46-55` 只取預算、狀態、版本三欄,多出來的名稱會被忽略。
- 分析端 `src/rtb/analyzer/dsp_client.py:64-98` 的可信欄位白名單不含名稱。名稱走 `_campaign_text` 這條路,超過 512 字截斷並標記。截斷是按字元切,表情符號這類字不會被切成一半。
- 逐字比對整份回應的既有測試只有 `tests/kit/test_shared_base.py` 一支,diff 已經同步改了。`tests/analyzer/test_dsp_client.py:122` 的 `GOOD_STATE` 不帶名稱,這條路本來就會把名稱記成空值,測試照樣全綠。

### 已看:圖譜寫回
- 同一個提交也改了 `Systems/Mock-DSP.md`,加了一行 RULE,帶 since/retire 欄位,並綁了三支測試;計劃也有更新。snapshot 只含程式碼,不影響審查結論。

總結:最高 severity clean,blocking 0 條。
