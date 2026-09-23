severity: clean

## 已看:可信證據的鍵也要是短代號之後,既有建構點會不會被誤殺

實跑 `tests/domain/test_evidence.py`、`tests/analyzer/test_dsp_client.py`、
`tests/analyzer/test_task_store.py` 全套(在 mktemp -d 臨時目錄、複製 src/tests、
PYTHONPATH 指到臨時目錄),91 個測試全過,包含:

- `test_evidence_rows_written_before_the_allowlist_still_read_back`(S218):直接寫增量 1
  之前格式的舊歷史列(鍵含 `budget`、`id`、`status`、`version`、`campaign_id`、`window`、
  `impressions`、`clicks`、`conversions`、`spend`、`revenue`),透過 `TaskStore.evidence_for`
  讀回會重建 `Evidence(...)`(`src/rtb/analyzer/task_store.py:222-227`),觸發
  `__post_init__` 的 `_strings_fit_trust`,對鍵也跑 `is_id`。這些舊鍵全是英文字母、數字、
  底線組成,符合 `ID_PATTERN = r"[A-Za-z0-9._:-]{1,128}"`(`src/rtb/domain/_checks.py:12`,
  底線在字元類別裡),讀回不受影響。
- 對照模擬 DSP 實際回應的鍵:`src/rtb/dsp/store.py` 的 `Campaign`(`id, budget, status,
  version`)、`MetricsRecord`(`campaign_id, window, impressions, clicks, conversions, spend,
  revenue`)兩個 dataclass 經 `asdict()` 直接序列化成 JSON body(`src/rtb/dsp/server.py:127-140`),
  欄位名稱與上面舊列一致,同樣通過 `is_id`。`Campaign` 目前沒有 `name` 欄位,`_campaign_text`
  的 `name` 路徑在真實模擬 DSP 上恆走「非字串→None」分支,這點符合 diff 開頭註記「增量 2
  才加名稱欄位,不算缺漏」。
- 分析端用戶端自己組出的可信 payload(`STATE_FIELDS`/`METRICS_FIELDS` 的鍵)與測試裡的
  `GOOD_STATE`/`GOOD_METRICS` 也都是同一組鍵,已被既有 91 個測試覆蓋。

結論:目前 repo 裡「建構可信證據」的每個既有來源(DSP 用戶端、測試、history 表舊列)用的
鍵全部落在 `ID_PATTERN` 內,加鍵檢查沒有誤殺任何一處。

## 已看:廣告編號核對搬進 `_trusted` 後的例外型別

在臨時目錄用 `_trusted` 本體直接餵入以下組合(重現腳本,見下方輸出),確認缺漏/型別錯/
編號不符的任意組合都只丟 `DspRequestFailed`,不會有 `KeyError`/`TypeError` 逃逸:

```
id missing entirely -> DspRequestFailed(dsp:campaign 的可信欄位不合格:id)
id is int not str -> DspRequestFailed(dsp:campaign 的可信欄位不合格:id)
id is None -> DspRequestFailed(dsp:campaign 的可信欄位不合格:id)
id is dict -> DspRequestFailed(dsp:campaign 的可信欄位不合格:id)
id valid but mismatched + budget bad -> DspRequestFailed(dsp:campaign 的可信欄位不合格:budget)
```

原因:`campaign_field`(`"id"`/`"campaign_id"`)本身一定是 `fields` 字典的成員(呼叫端
`_trusted(state_body, STATE_FIELDS, "id", …)`、`_trusted(metrics_body, METRICS_FIELDS,
"campaign_id", …)`,`src/rtb/analyzer/dsp_client.py:157-160`),而 `fields[campaign_field]`
就是 `is_id`。所以第一段 `bad = [name for name, check in fields.items() if name not in body
or not check(body[name])]` 已經先把「缺漏」或「型別/格式不對」的 `campaign_field` 收進
`bad`;只有當 `bad` 為空(代表 `campaign_field` 已存在且通過 `is_id`,保證是字串)時才會走到
`body[campaign_field] != task.campaign_id`,此時存取一定安全。三格機械反查也顯示這支函式
沒有其他呼叫者或共改夥伴,改動範圍就在本檔內自洽。

## 已看:分析端匯入領域層 `proposal.MAX_INT`

`src/rtb/domain/proposal.py` 只 `import`(hashlib/json/math/re/collections.abc/dataclasses/
datetime/enum/types/typing)與 `from rtb.domain._checks import ID_PATTERN, is_aware,
is_plain_int`,不依賴 `rtb.analyzer` 任何東西;`rtb/domain/__init__.py`、
`rtb/analyzer/__init__.py` 都是空檔。`rtb.analyzer.task_store` 本來就已經
`from rtb.domain.proposal import ActionType, Proposal`(既有慣例),`dsp_client.py` 這次加
`from rtb.domain.proposal import MAX_INT` 是同一層對同一模組的再一次引用,方向仍是
analyzer → domain(單向),不是新增的跨層方向。在臨時目錄執行
`python -c "import rtb.analyzer.dsp_client"` 成功,無 `ImportError`/循環匯入。

## 已看:整份 diff 逐 hunk

- `Check` 型別從 `Callable[[Any, TaskRow], bool]` 改成 `Callable[[Any], bool]`,
  `STATE_FIELDS`/`METRICS_FIELDS` 所有 lambda 與 `is_id` 均已同步改成單參數,呼叫端
  `check(body[name])` 對應正確,ruff/mypy 對這兩支改動檔全過。
- `_strings_fit_trust` 只在 `trust_class` 為非 `UNTRUSTED_TEXT`(即可信)分支才檢查鍵,
  `UNTRUSTED_TEXT` 分支維持只檢查字串值長度、不查鍵格式——`_campaign_text` 的輸出鍵固定是
  `"name"`/`"truncated"`,不受影響,且此分支特意不限制鍵是因為這整份 payload 本來就是不可信
  文字的容器,鍵名格式不是這裡要防的威脅面(注入點是字串值),判斷為設計選擇而非缺陷。
- `problems` 用 `dict.fromkeys` 去重,只讓 `trust_class` 兩次檢查失敗時只回報一次,屬於既有
  行為的無害化,不影響任何測試斷言的錯誤訊息比對(測試都用 `match="trust_class"`/`"payload"`
  子字串比對,不要求恰好一次)。
- `MAX_DB_INT`(`2**63-1`)整支刪除、改匯入 `proposal.MAX_INT`,兩者數值相同,`_is_int_between`/
  `_is_count_or_none` 語意不變。

## 三格機械反查

受影響測試、共改夥伴、呼叫者三格皆空,人工核對後與程式碼實況一致:`_trusted`/
`_strings_fit_trust`/`proposal.MAX_INT` 這幾個改動點目前只有這份 diff 內的測試在用,
沒有查漏。

⚠ 交編排者:`py-memory satisfied — src/rtb/analyzer/task_store.py:263` 這則表態記錄指向
的那一行(`row = self._conn.execute(...).fetchone()`,在 `commit_step` 交易區塊內)不在這份
r2 diff 的改動範圍內(這次 diff 只改了 `tests/analyzer/test_task_store.py`,沒改
`src/rtb/analyzer/task_store.py` 本體),本輪審查範圍未涵蓋,真假留給有查過那支檔全貌的人判。

沒有 finding;最高 severity 為 clean,blocking 0 條。
