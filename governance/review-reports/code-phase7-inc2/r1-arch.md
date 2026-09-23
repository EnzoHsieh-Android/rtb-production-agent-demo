severity: minor

# 一、分層與依賴方向

本次 diff 只動 `src/rtb/dsp/store.py`(加 `name` 欄位、`_check_campaign_name`、遷移邏輯)與兩份測試檔,沒有新增 import、沒有跨層直呼。

- `store.py` 仍只依賴 `rtb.dsp.errors`、`rtb.sqlitekit`,和改動前一致(對照 `src/rtb/dsp/store.py:27-35`,改動前後 import 區塊沒有變化)。
- `server.py`、`capability.py` 完全沒被本次 diff 碰,`GET /campaigns/{id}` 能回傳 `name` 純粹是因為 `_get_campaign` 本來就是 `return asdict(store.get_campaign(campaign_id))`(`src/rtb/dsp/server.py:127-130`),資料層加欄位、HTTP 層零改動地跟著序列化——這是沿用既有的「dataclass → asdict 通用序列化」分層方式,不是在 HTTP 層另開一條路徑塞欄位。
- `capability.py` 的 `BODY_FIELDS`/`SCOPE_FIELDS` 沒有出現 `name`,跟租戶一樣「沒有任何寫入端點認得這個欄位」,分層責任沒有位移。

判定:此問無不對齊。

# 二、命名與錯誤處理

- `_check_campaign_name(name)` 對照同檔既有 `_check_window(window)`(`src/rtb/dsp/store.py:186-188` 改動前版本):同樣是「條件不合法就直接 `raise ValidationRejected`,無回傳值」的寫法,命名也延續 `_check_` 前綴慣例,結構一致。
- 例外鏈:`except UnicodeEncodeError as exc: raise ValidationRejected(...) from exc` 延續本檔既有的 `except DatabaseBusy as exc: raise StoreBusy(str(exc)) from exc` 這類「型別化錯誤 + `from exc` 保留原因」寫法(見 `CampaignStore.__init__`、`_begin_write_transaction`),沒有另創錯誤處理風格。
- `Campaign` 新增必填欄位 `name: str` 沒給預設值,放在 dataclass 最後一格,呼叫端(`_next_state`、`seed_campaign`、`get_campaign`)全部同步改成五個位置參數——跟改動前 `budget`/`status`/`version` 這些既有欄位的處理方式相同,沒有另開 kwargs-only 或 `Optional` 的第二種欄位加法。

## F1
severity: minor
blocking: 否 — 純命名一致性問題,不影響結構或行為,不擋合併
引句:「self._conn.execute("ALTER TABLE campaigns ADD COLUMN name TEXT NOT NULL DEFAULT ''")」

租戶補欄位是用具名常數組字串:`f"DEFAULT '{DEFAULT_TENANT}'"`(改動前 `store.py` 的 `_migrate_columns`)。名稱補欄位卻直接把空字串字面值 `''` 寫死在 SQL 裡,沒有比照租戶那次去開一個 `DEFAULT_CAMPAIGN_NAME = ""` 之類的具名常數。兩處做的是同一件事(補欄位預設值),命名慣例卻不同,屬於「常數命名不一致」,結構本身沒問題。

# 三、第二種做法

- 補欄位的技術路徑:`_migrate_columns` 對 `name` 欄位用的仍是「`PRAGMA table_info` 檢查缺欄位 → `BEGIN IMMEDIATE` 交易內 `ALTER TABLE ... ADD COLUMN` → `COMMIT`」同一套機制,跟 `tenant`、`operations.policy_version`/`expected_version` 完全同一支函式、同一個交易,不是另開一條遷移路徑。判定:對齊,沒有第二種做法。
- 沒有自造同檔已有的工具:名稱驗證直接呼叫 `ValidationRejected`(既有型別),沒有另造一個平行的驗證例外或平行的「檢查字串長度」工具函式;UTF-8 可寫入檢查也是本次新內容,同檔內找不到既有的「檢查字串可編碼性」函式被繞過或重造的情形。
- 兩點分層/技術路徑對齊,唯一的落差是上面 F1 的常數命名習慣,已計入該題。

不對齊共 1 條,其中 major 0 條

⚠ 交編排者:作者在 py-memory 題的表態(名稱 4096 字元上限、查廣告只取一列)兩點都查證屬實——`MAX_CAMPAIGN_NAME_LENGTH = 4096` 確實在 `store.py:55`;但「查廣告只取一列」的 `fetchone()` 實際落在 `get_campaign`(改動後約 `store.py:287-288`),不是第 55 行,證據行號沒對準,只是指錯行,內容本身沒有造假,已依指示不列入 finding,提醒編排者知悉即可。三格機械反查(受影響測試/共改夥伴/呼叫者)皆為 0,本次判斷純靠讀 diff 與對照檔,沒有額外相依可查證,若之後補上圖譜節點建議回頭核對一次。
