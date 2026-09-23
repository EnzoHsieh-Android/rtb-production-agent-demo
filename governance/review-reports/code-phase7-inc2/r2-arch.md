severity: clean

### 驗收:上輪 minor(名稱補欄位空字串沒比照 DEFAULT_TENANT 用具名常數)已修到位

作者新增 `DEFAULT_CAMPAIGN_NAME = ""`,並在兩處都換掉原本寫死的空字串:

file: `src/rtb/dsp/store.py:56`(新增常數,注解風格與 `DEFAULT_TENANT` 對仗:「沒給名稱的廣告(含補欄位前的舊資料)名稱是空字串」對「沒指定租戶的廣告(含補欄位前的舊資料)都屬於它」)

file: `src/rtb/dsp/store.py:262-264`(`_migrate_columns` 內 `ALTER TABLE campaigns ADD COLUMN name TEXT NOT NULL DEFAULT '{DEFAULT_CAMPAIGN_NAME}'`,f-string 組法與同函式裡 `tenant` 那行 `DEFAULT '{DEFAULT_TENANT}'` 完全同構)

file: `src/rtb/dsp/store.py:283`(`seed_campaign` 簽名 `name: str = DEFAULT_CAMPAIGN_NAME`,與同簽名裡 `tenant: str = DEFAULT_TENANT` 同構)

兩處改法(ALTER TABLE 預設值、函式參數預設值)都逐一比照 `DEFAULT_TENANT` 的既有寫法,沒有另開一套。上一輪那條 minor 已收斂。

### 驗收:修正本身沒有引入新的不一致

檢查過的幾個可能岔路,結果都落在既有慣例內:

- `SCHEMA` 字串裡的 `CREATE TABLE` 仍是 `name TEXT NOT NULL DEFAULT ''`(store.py:39)寫死字面值、沒有用常數——這跟同一行 `tenant TEXT NOT NULL DEFAULT 't-default'` 一樣寫死,是 baseline(r1 之前)就有的既有做法,`SCHEMA` 本來就是純 DDL 常數字串,不是這次改動引入的新岔路,也不是這輪修正的範圍。
- `Campaign.name` 不給預設值(要求呼叫端一律沿用 `campaign.name`),`_next_state` 兩個分支都手動把 `campaign.name` 帶過去——這是刻意設計(行內註解已說明:「不給預設值,改狀態時必須沿用」),跟 dataclass 其他必填欄位(`id`/`budget`/`status`/`version`)待遇一致,沒有另闢欄位風格。
- `seed_campaign` 對 `name` 呼叫 `_check_campaign_name`,對 `tenant` 沒有對應檢查函式——這個不對稱在 baseline 就存在(`tenant` 本來就沒有校驗函式),不是這次 delta 新增的落差。

三處都對照過 `src/rtb/dsp/store.py` 裡 `DEFAULT_TENANT` 一路的既有寫法(常數定義、SCHEMA 內聯字面值、ALTER TABLE f-string、函式參數預設值四個位置),`DEFAULT_CAMPAIGN_NAME` 除了「跟著 SCHEMA 既有慣例維持字面值」以外全部比照辦理,沒有引入第二種做法。

不對齊共 0 條,其中 major 0 條。

⚠ 交編排者:三格機械反查(受影響測試/共改夥伴/呼叫者)皆為 0,本輪判斷完全基於程式碼直讀比對,沒有额外歷史脈絡可交叉驗證;若之後想確認「SCHEMA 內聯字面值 vs 具名常數」這條路是否要在未來某次重構一併處理,建議另開一條非阻塞的觀察,而不是掛在本次 F1 修正上——目前判定這不屬於本次修正範圍,予以放行。
