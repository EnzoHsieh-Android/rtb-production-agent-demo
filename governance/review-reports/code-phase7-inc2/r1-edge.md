severity: minor

## F1 `_next_state` 把 `campaign.name` 穿進回傳值是死碼,拿掉不會讓任何測試翻紅

severity: minor
blocking: 否 — 不影響任何可觀察行為:`_apply` 的 `UPDATE campaigns SET budget = ?, status = ?, version = ? WHERE id = ?` 本來就沒有 `name` 欄位,`OperationResult` 也沒有 `name` 屬性,所以 `_next_state` 回傳的 `Campaign.name` 不管是對的、空字串、還是垃圾值,都不會被寫回資料庫或回傳給呼叫端;查廣告的名稱永遠是直接從資料庫 `SELECT` 出來的原值,跟 `_next_state` 這條路徑無關。真正保護「沒有寫入端點能改名稱」這件事的是 F5 那支 AST 守衛測試(見「已看」節),不是這行。
引句:「return Campaign(campaign.id, budget, campaign.status, campaign.version + 1, campaign.name)」

最小重現(臨時目錄,已在審查中實際跑過): 把 `src/rtb/dsp/store.py` 的
```
        return Campaign(campaign.id, budget, campaign.status, campaign.version + 1, campaign.name)
    return Campaign(campaign.id, campaign.budget, "paused", campaign.version + 1, campaign.name)
```
改成兩處都用 `""` 取代 `campaign.name`(等同拿掉「已補好判斷含名稱」這個機制),清掉 `__pycache__` 後跑:
```
PYTHONPATH=<tmp>/src <venv>/bin/python -m pytest -p no:cacheprovider tests/dsp/test_campaign_name.py -q
```
結果:`6 passed`,`test_writes_keep_the_campaign_name_and_no_route_changes_it` 沒有抓到——它在 `store.execute()` 兩次寫入(`update_budget`、`pause_campaign`)之後才用 `get_campaign` 重新 `SELECT`,讀到的是資料庫裡從沒被 UPDATE 碰過的原始 `name`,不是 `_next_state` 回傳的那個物件。

### 已看:①拿掉對應機制逐一驗證是否翻紅(臨時目錄,清 `__pycache__` 後跑)

| 拿掉的機制 | 改法 | 結果 |
|---|---|---|
| 遷移補欄位(`_migrate_columns`) | 移除 `ALTER TABLE campaigns ADD COLUMN name`,守衛條件退回只看 `tenant` | `test_an_old_dsp_database_gains_the_campaign_name_column` 翻紅(`sqlite3.OperationalError: no such column: name`) |
| 已補好判斷含名稱(`_next_state` 傳 `campaign.name`) | 兩處都改傳 `""` | **不翻紅**,見 F1 |
| 長度上限(`len(name) > MAX_CAMPAIGN_NAME_LENGTH`) | 拿掉長度判斷,只留型別檢查 | `test_the_dsp_caps_campaign_names_below_the_response_limit[xxx...(4097字)]` 翻紅(`DID NOT RAISE ValidationRejected`) |
| 型別檢查(`isinstance(name, str)`) | 拿掉,只留長度判斷 | `[42]`、`[None]` 兩個參數化案例翻紅(`AttributeError: 'NoneType' object has no attribute 'encode'` / int 同理,pytest.raises 抓不到指定例外仍算翻紅) |
| 孤立代理字元檢查(`name.encode("utf-8")` 那段 try/except) | 整段拿掉 | `["ok\ud800"]` 翻紅(`UnicodeEncodeError` 直接炸出來,沒被轉成 `ValidationRejected`,pytest.raises 失敗) |
| 改廣告的敘述不碰名稱(在 `_apply` 的 `UPDATE campaigns` 加上 `name = ?`) | `UPDATE campaigns SET budget = ?, status = ?, version = ?, name = ? WHERE id = ?` | `test_writes_keep_the_campaign_name_and_no_route_changes_it` 翻紅在 AST 守衛那條斷言(`assert all("name" not in sql.lower() for sql in updates)`),抓到了 |

六個機制裡五個真的有牙齒,只有「已補好判斷含名稱」這格是假的(F1)。

### 已看:②S219「最壞情況回應低於 64 KB」測法與位元組數重算

- 分析端實際讀取路徑是 `src/rtb/httpclient.py` 的 `_read_capped`:用 `response.read1(8192)` 分段讀、逐段累加 `total` 跟 `MAX_RESPONSE_BYTES`(=65536)比對,量的是 HTTP 本文的原始位元組數。測試用 `urllib.request.urlopen(...).read()` 一次讀完同一個 socket 回應本文,兩邊量的是同一份原始位元組流,方法上一致,沒有分析端會多算/少算的落差。
- 伺服器端組回應在 `src/rtb/httpkit.py:236`:`raw = json.dumps(payload).encode()`,預設 `ensure_ascii=True`。實測(`/Users/enzo/rtb-production-agent-demo/.venv/bin/python3`)各類字元在這個編碼方式下每個 Python 字元(`len()` 算的 code point)佔的位元組數:
  - 代理對字元(如 U+1F600,BMP 外):每字 12 位元組(`😀`)——4096 字組出的完整回應 **49225 位元組**
  - 反斜線 `\`、雙引號 `"`:每字 2 位元組(`\\`、`\"`)——4096 字回應 8265 位元組
  - 無短別名的控制字元(如 `\x01`):每字 6 位元組(`\u0001`)——4096 字回應 24649 位元組
  - 一般 BMP 非 ASCII 字元:每字最多 6 位元組(`\uXXXX`)
  代理對字元(12 位元組/字)確實是目前 JSON 逃脫規則下每字元位元組數的上限,沒有找到別的字元類型會超過它。`MAX_CAMPAIGN_NAME_LENGTH=4096` 配代理對字元的最壞情況回應是 49225 位元組,離 65536 還有約 16 KB 餘裕,`store.py` 註解「4096 字約 48 KB」與實測相符,S219 測的確是最壞情況,不到 major。

### 已看:③`tests/kit/test_shared_base.py` 的改動範圍

`test_the_dsp_server_behaviour_is_unchanged_after_extracting_the_shared_base` 裡唯一的改動是把 `_get(srv, "/campaigns/c1")` 的期望值從 `{"id": "c1", "budget": 100, "status": "active", "version": 1}` 改成多一個 `"name": ""`,比對方式仍是整個 dict 的 `==` 嚴格相等,其餘四個斷言(404 找不到廣告、404 無此路由、400 host 不符、400 故障注入停用、POST 壞 JSON 回 400)一字未動。沒有放寬既有斷言。

最高 severity:minor;blocking 條數:0(1 條 minor,不阻擋)。
