severity: minor

第 2 輪審查,對象是 r2-delta.patch(補 `DEFAULT_CAMPAIGN_NAME` 具名常數、`_next_state` 加註解、測試與筆記更正),以 r2-snapshot.patch(增量 2 完整差異)為輔助脈絡。逐 hunk 讀完,以下是找到的問題與已核對過沒事的項目。

## F1 `DEFAULT_CAMPAIGN_NAME` 用 f-string 拼進 `ALTER TABLE ... DEFAULT` 子句,常數若改成含單引號的值會拼壞 SQL
severity: minor
blocking: 否 — 現行常數值是 `""`,不含引號,r2 這次改動本身不會讓現有行為壞掉,是「未來若改常數值」才會觸發的潛伏風險,不是本輪引入的功能性回歸
引句:「DEFAULT '{DEFAULT_CAMPAIGN_NAME}'」

`_migrate_columns` 裡新增的補欄位敘述:

```python
self._conn.execute(
    "ALTER TABLE campaigns ADD COLUMN name TEXT NOT NULL "
    f"DEFAULT '{DEFAULT_CAMPAIGN_NAME}'")
```

用 f-string 直接把常數字面值嵌進 SQL 字面量,沒有做任何逃脫。用最小重現驗證:把同樣的拼法套一個含單引號的值(例如把 `DEFAULT_CAMPAIGN_NAME` 想像成之後改成 `"O'Brien"`)會直接拼壞 SQL 語法:

```
$ python3 repro_alter.py
DEFAULT_CAMPAIGN_NAME='' -> OK
DEFAULT_CAMPAIGN_NAME="O'Brien" -> OperationalError near "Brien": syntax error
```

（`repro_alter.py` 內容:對同一句 f-string 拼法分別代入 `""` 與 `"O'Brien"` 執行 `ALTER TABLE`,前者成功、後者拋 `sqlite3.OperationalError`。）

對照同檔既有的 `DEFAULT_TENANT` 拼法(`f"DEFAULT '{DEFAULT_TENANT}'"`,`DEFAULT_TENANT = "t-default"`,同樣沒有引號、同樣沒有逃脫),`DEFAULT_CAMPAIGN_NAME` 這次改動只是照抄既有慣例,不是新引入的拼法。也就是說這是這支檔案既有的、跨兩個常數共通的脆弱點,不是 r2 這輪修正自己造出來的新洞;會不會爆看的是以後有沒有人把這兩個常數改成含單引號的值(例如給預設租戶或預設名稱一個帶所有格符號的字串)。目前兩個常數的值都不含引號,實際運行沒有壞。

建議(供編排者參考,非本輪必改):兩個常數都改用參數化(把 `DEFAULT ?` 換成先建欄位、再用 `UPDATE ... SET name = ?` 補值,或用 `sqlite3` 的識別字/字面值逃脫),但這屬於既有慣例的技術債,不是這次 diff 本身要背的責任。

### 已看:DEFAULT_CAMPAIGN_NAME 換成常數後,建檔與補欄位行為是否不變
`_migrate_columns` 補欄位那句:原本硬寫 `DEFAULT ''`,改後 `DEFAULT_CAMPAIGN_NAME` 值就是 `""`,f-string 展開後產生的 SQL 字串逐字元相同,不是行為改動。`seed_campaign` 的預設參數同理,原本 `name: str = ""` 改成 `name: str = DEFAULT_CAMPAIGN_NAME`,值同為 `""`,呼叫端沒傳 `name` 時建出來的廣告名稱不變。用 `tests/dsp/test_campaign_name.py::test_an_old_dsp_database_gains_the_campaign_name_column` 與 `tests/kit/test_shared_base.py` 相關測試實際跑過(`pytest tests/dsp/test_campaign_name.py tests/kit/test_shared_base.py`,10 個測試全過),行為確認沒變。

### 已看:`_next_state` 新加的註解與程式實際行為是否一致
註解說「名稱照抄只是因為 Campaign 必填;名稱不被寫入改掉,靠的是 _apply 的 UPDATE 根本不寫名稱欄」。查了 `CampaignStore._apply`(store.py:415-419),實際的 `UPDATE` 敘述是:

```python
"UPDATE campaigns SET budget = ?, status = ?, version = ? WHERE id = ?"
```

確實沒有 `name` 欄。`_next_state` 兩個分支都用 `campaign.name` 原樣建構新的 `Campaign`,是因為 dataclass 的 `name` 欄位必填、建物件時不能省略,實際「名稱不會被寫入端點改掉」這件事的機制是 `_apply` 那句 UPDATE 本身沒有 `name = ?`。註解的說法跟程式行為一致,沒有找到落差。

## ⚠ 交編排者
F1 的觸發條件是「以後有人改常數字面值」,不是這次 diff 的行為;是否要在這輪就順手把 f-string 拼字面量的既有慣例(`DEFAULT_TENANT` 與 `DEFAULT_CAMPAIGN_NAME` 共通)改掉,還是留到之後,交編排者判斷是否值得追加範圍。

最高 minor,blocking 條數 0。
