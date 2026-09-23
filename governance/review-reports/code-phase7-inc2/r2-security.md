severity: clean

範圍:`src/rtb/dsp/store.py`(廣告名稱欄位、建檔驗證、補欄位遷移)與對應測試 `tests/dsp/test_campaign_name.py`、`tests/kit/test_shared_base.py`。r2-delta 把 `ALTER TABLE campaigns ADD COLUMN name TEXT NOT NULL DEFAULT '...'` 的字面 `''` 改成具名常數 `DEFAULT_CAMPAIGN_NAME`(與既有 `DEFAULT_TENANT` 同一寫法)並補了容量估算註解,同一版本 `tenant` 補欄位已用相同 f-string 拼接 DEFAULT 子句的寫法、之前審查未挑出問題,這次改動一致沿用。

## 1 不可信輸入流到危險操作
已看,無阻擋性發現;有一條 minor 記在下面。

- 名稱本身寫進資料庫走的是參數化查詢:`seed_campaign` 用 `INSERT INTO campaigns (...) VALUES (?, ?, ?, 1, ?, ?)`,name 是 bind 參數,不是拼字串。
- 唯一用 f-string 組出的 SQL 是補欄位的 `ALTER TABLE campaigns ADD COLUMN name TEXT NOT NULL DEFAULT '{DEFAULT_CAMPAIGN_NAME}'`(store.py:139-141)。拼進去的是模組層具名常數 `DEFAULT_CAMPAIGN_NAME = ""`,不是任何請求路徑上的值——攻擊者控制的廣告名稱從未流進這行。這正是 r2-delta 修正的重點(把裸字面 `''` 換成具名常數),行為上兩版本等價,但具名常數讓「這裡拼的是常數不是輸入」这件事一眼可查,方向正確。
- `UPDATE campaigns SET budget = ?, status = ?, version = ? WHERE id = ?`(store.py:419,未在本次 diff 內但由測試守衛)沒有寫 `name` 欄,且 `tests/dsp/test_campaign_name.py` 的 S209 用 AST 掃過 `store.py` 全部 `UPDATE CAMPAIGNS...` 字串常數,斷言沒有一條含 `name`——這是機檢守衛,不是靠人讀。名稱只在建檔時寫入(`seed_campaign`),沒有任何寫入端點(`update_budget`/`pause_campaign`/`void_operation`)能改它,測試也逐一列出寫入路由清單驗證這件事。
- 名稱長度上限(4096 字元)、非法代理字元(孤立 surrogate)在 `_check_campaign_name` 擋下,用的是 `str.encode("utf-8")` 觸發 `UnicodeEncodeError` 的標準做法,不是自寫的字元黑名單,不會有繞過空間(合法 UTF-8 才能過關,SQLite 本身也只收合法 UTF-8)。
- 回應大小:分析端 `httpclient.py` 的 `MAX_RESPONSE_BYTES = 64*1024` 是讀取上限,`httpkit.py:236` 的 `json.dumps(payload).encode()` 用預設 `ensure_ascii=True`,代理對字元會被跳脫成 `\uXXXX\uXXXX`(12 bytes/字),4096 字最壞情況約 48 KB,測試 `test_the_dsp_caps_campaign_names_below_the_response_limit` 直接量測 `len(raw) < MAX_RESPONSE_BYTES` 而非只信註解裡的算術,值得肯定。另外查過 `history()`/`HistoryEntry` 不含 `name` 欄位,不會被操作次數放大成回應炸彈。

### F1 ALTER TABLE 的 DEFAULT 子句仍用 f-string 拼常數,不是參數化 DDL
severity: minor
blocking: 否 — 目前兩個插進去的值(`DEFAULT_TENANT`、`DEFAULT_CAMPAIGN_NAME`)都是寫死在模組層的字面常數,沒有任何程式路徑能讓請求內容流進這兩個名字所綁的字串,不成立可執行的攻擊路徑,只是模式本身脆弱(SQLite DDL 不支援 `?` 綁參數,只能這樣拼,若未來有人把某個「預設值」改成帶引號或使用者可控的字串就會裂開)。
引句:「f"DEFAULT '{DEFAULT_CAMPAIGN_NAME}'")」

## 2 權限與範圍
已看,無。名稱不可被任何寫入端點更改(比照既有的 tenant 欄位設計);`seed_campaign` 是建檔專用的內部/測試種子函式,不在 HTTP 路由表上,一般攻擊者打不到它,只能透過「建立廣告」這個動作決定名稱內容一次,之後鎖死。

## 3 密鑰與個資
已看,無。名稱是攻擊者自己選的不可信文字,不涉及讀出既有密鑰或他人個資;沒有把名稱寫進 log、錯誤訊息或憑證欄位。

## 4 加密與隨機數
已看,無關。本次 diff 沒有觸碰任何雜湊、簽章、隨機數或憑證邏輯。

## 5 執行邊界
已看,無。沒有 `eval`/`exec`/`pickle`/`subprocess`/`shell=True`,`_check_campaign_name` 只做型別、長度、UTF-8 編碼檢查,不解析名稱內容、不反序列化。

## 6 行動端
已看,不適用。純後端 Python 服務,無行動端程式碼。

## 新依賴
已看,無。沒有新增任何套件或 import 來源。

## 總結
這輪 diff(以及本輪修正)沒有找到可被利用的洞。名稱從「不可信文字」進到儲存層全程走參數化查詢,唯一的字串拼接對象是寫死常數而非請求輸入,且有 AST 層級的機檢守衛防止未來有人不小心把 `name` 塞進 UPDATE 語句造成寫入端點意外開放改名。回應體積上限有機檢測試實測而非只靠註解裡的估算,可信度較高。F1 是留給未來維護者的提醒,不影響本次合入。
