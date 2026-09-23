severity: clean

# 對答案審查報告:RTB_Phase7 增量 2(模擬 DSP 補「廣告名稱」欄位)

範圍:計劃檔「## 增量 2 設計」節(〈設計〉、〈不做的事〉、合約 S208/S209/S219、〈回退〉)與「使用者裁定」節裡跟名稱有關的兩條,逐條對照 `/Users/enzo/rtb-3b/governance/review-reports/code-phase7-inc2/r1-snapshot.patch`。

## 逐條裁定

### 設計

- **廣告多一個欄位:名稱(文字、非空值、預設空字串);建表語句加這一欄** → 已實作。`src/rtb/dsp/store.py:17` `tenant TEXT NOT NULL DEFAULT 't-default', name TEXT NOT NULL DEFAULT ''`。
- **舊資料庫照既有補欄位做法:每次連線先檢查,缺就在立即取得寫入鎖的交易裡加欄位、拿到鎖後再查一次,舊廣告名稱是空字串;「全部都已補好就直接返回」的判斷要把名稱一起算進去** → 已實作。`src/rtb/dsp/store.py:124` 判斷改成 `if {"tenant", "name"} <= columns and {...} <= op_columns: return`;`store.py:136-137` 在交易內用 `PRAGMA table_info` 重查後補 `ALTER TABLE campaigns ADD COLUMN name TEXT NOT NULL DEFAULT ''`,跟 tenant 那條同一套「拿到鎖後再查一次」的寫法。對應測試 `tests/dsp/test_campaign_name.py:224-242`(S208)用手刻的舊表(只有 tenant、沒有 name)驗證開啟後名稱補空字串、其餘欄位不變,且第二次開啟不重複加欄位。
- **建檔多一個名稱參數,預設空字串;只收字串,不過濾內容;長度上限 4096 字元;超過上限、不是字串、或含孤立代理字元一律丟 `ValidationRejected`** → 已實作。`store.py:76-84` 新增 `_check_campaign_name`,`isinstance` 檢查字串與長度上限、`encode("utf-8")` 抓孤立代理字元轉成 `ValidationRejected`;`store.py:155-157` `seed_campaign` 簽章加 `name: str = ""` 且呼叫 `_check_campaign_name(name)`。`MAX_CAMPAIGN_NAME_LENGTH = 4096`(`store.py:35`)。位元組估算註解(`store.py:33-34`)寫「每字 12 位元組、4096 字約 48 KB,仍低於分析端 64 KB」,與 spec 的 r2 修正(代理對估算)一致。測試 `test_the_dsp_caps_campaign_names_below_the_response_limit` 參數化 `[超長、孤立代理字元、非字串 42、None]` 都應拒收,並驗證拒收後表裡沒有殘留列(`SELECT count(*) ... == (0,)`)。
- **查廣告回傳名稱;改預算、暫停只更新預算/狀態/版本三欄,名稱保持不變** → 已實作。`_next_state`(`store.py:87-95`)兩個分支都把 `campaign.name` 原樣帶進新的 `Campaign(...)`,沒有用建構式漏帶。`get_campaign` 的 SELECT 加了 `name` 欄(`store.py:175`),`Campaign(*row)` 順序與 dataclass 欄位順序一致。查廣告的 HTTP 回應由 `src/rtb/dsp/server.py:130` 既有的 `asdict(store.get_campaign(campaign_id))` 產生,dataclass 多一個欄位就自動帶出,本增量不需要、也沒有改 server.py。測試 `test_writes_keep_the_campaign_name_and_no_route_changes_it` 端到端驗證:建檔帶攻擊性名稱 → 改預算 → 暫停 → 名稱不變,且 HTTP 查詢回應的 `name` 欄位等於建檔時的名稱。
- **沒有任何寫入端點能改名稱,比照租戶那條規則** → 已實作並有兩層守衛。①路由層:測試斷言 `POST` 路由集合為 `["pause_campaign", "update_budget", "void_operation"]`,沒有改名稱的路由。②儲存層:AST 掃描 `store_module` 原始碼裡所有以 `UPDATE CAMPAIGNS` 開頭的字串常量,斷言沒有一條含 `"name"`(`tests/dsp/test_campaign_name.py:293-299`),並且有「守衛的守衛」`assert updates`(先確認真的抓到了改廣告的 SQL 敘述,不是空清單通過)。
- **執行行程的 DSP 用戶端讀廣告時只取三欄,多出的名稱本來就被忽略,不用改** → 屬「不做」的部分,diff 未觸碰執行行程程式碼,與 spec「這裡只讀不改」一致。

### 不做的事(增量 2 範圍)

- **不加素材文字、落地頁網址等其他文字欄位** → 已遵守,diff 只加了 `name` 一欄。
- **不加改名稱的寫入端點** → 已遵守,見上。

### 合約

- **[S208]** 舊資料庫開啟後補名稱欄位、舊廣告名稱空字串、其餘欄位不變 → 已實作,測試 `test_an_old_dsp_database_gains_the_campaign_name_column` 逐項斷言 `(budget, status, version, name) == (77, "paused", 4, "")` 且 `tenant_of == "t-acme"`。
- **[S219]** 名稱超過 4096 或含孤立代理字元丟 `ValidationRejected`;剛好 4096 個代理對字元的名稱,查詢回應應低於 64 KB → 已實作,測試同時涵蓋超長、孤立代理字元、非字串(`42`)、`None` 四種拒收情形(spec 只明講「超過上限」與「孤立代理字元」兩種,測試多覆蓋「不是字串」屬 spec 設計段落既有要求,非額外行為),並用真實跑起來的 HTTP server 驗證 `len(raw) < MAX_RESPONSE_BYTES`。
- **[S209]** 查廣告回傳建檔時的名稱;改預算與暫停後名稱不變;路由表沒有任何能改名稱的寫入端點 → 已實作,見上方設計節說明,三項斷言齊全。

### 回退(增量 2)

- 查廣告的回應拿掉名稱;欄位本身留在資料表裡不影響任何讀者,SQLite 拿掉欄位要重建表不值得 → 這是回退步驟的敘述,不是本增量要新增的行為,本增量 diff 沒有(也不需要)任何對應改動;判「不適用」,非缺項。

## 使用者裁定(名稱相關兩條)

- **「模擬 DSP 只加『廣告名稱』一欄。素材文字…落地頁網址…不屬於這個階段」** → 已遵守,diff 只新增 `name` 欄位,無其他文字欄位。
- **「模擬 DSP 的廣告名稱上限 4096 字元,超過就拒絕建檔;孤立代理字元這類寫不進資料庫的字元也拒絕」** → 已遵守,`MAX_CAMPAIGN_NAME_LENGTH = 4096` 且 `_check_campaign_name` 同時擋超長與孤立代理字元。

## 縮水

(無)

## 未實作

(無)

## 多做

(無;`tests/kit/test_shared_base.py` 的預期回應加上 `"name": ""` 是 S209 要求「查廣告應回傳名稱」的必然結果,不算多做,計劃「受影響的既有測試」小節也已記錄同一件事。)

## 措辭/枚舉差異

(無重大差異;S219 測試比 spec 原文多測了「非字串」「None」兩種拒收輸入,屬同段設計文字「不是字串…一律丟 ValidationRejected」的既有要求,非測試自行擴權。)

縮水+未實作共 0 條

## ⚠ 交編排者

- 計劃檔「增量 2 設計」狀態行寫「實作完成,待代碼審」,與 diff 內容(store.py + 兩支測試檔)在範圍上吻合,判斷過程沒有遇到需要用「以程式碼為準」裁掉的筆記矛盾,也沒有發現筆記本身寫錯的地方。
- diff 檔只涵蓋增量 2(模擬 DSP 名稱欄位),未包含增量 1 的其餘檔案異動,審查範圍與交付範圍一致,沒有跨增量污染。
