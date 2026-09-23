severity: clean

逐 hunk 讀完 `src/rtb/analyzer/dsp_client.py`、`src/rtb/domain/evidence.py` 與三支測試檔的完整 diff;另外對照了 `src/rtb/dsp/store.py`、`src/rtb/dsp/server.py`(模擬 DSP 實際回傳型別)、`src/rtb/analyzer/task_store.py`(歷史表讀寫)、`src/rtb/analyzer/flow.py`(呼叫端怎麼吞例外)與 `src/rtb/domain/_checks.py`。跑了 `tests/analyzer/test_dsp_client.py`、`tests/domain/test_evidence.py`、`tests/analyzer/test_task_store.py`(PYTHONPATH=/Users/enzo/rtb-3b/src,repo 內直接跑、沒有改動任何檔案),90 個全過。沒有找到夠格報 blocker/major/minor 的洞。

### 已看:信任邊界(逐欄白名單、可信欄位驗證、payload 型別)
- `_trusted()` 只從固定的 `fields`(白名單)取值回傳,不合格就整個丟 `DspRequestFailed`,不把攻擊者可控的欄位值放進錯誤訊息:引句:「bad = [name for name, check in fields.items()」——這裡的 `name` 迭代的是白名單鍵,不是回應本文裡任意鍵,所以名單外欄位的名稱也進不了例外訊息。`on_call` 鉤子只記 `type(exc).__name__`(既有寫法,這次沒改),不記例外訊息內容,不會把任何自由文字帶進 tool_calls 紀錄。
- `payload=MappingProxyType(state)` / `MappingProxyType(metrics)` / `MappingProxyType(text)` 三處都用 `MappingProxyType` 包,`Evidence._is_payload` 只認 `MappingProxyType`,plain dict 建構會被擋(既有行為,未被這次修改削弱)。
- `Evidence.__post_init__` 新增的 `_trust_matches_kind` 與 `_strings_fit_trust` 是型別層面的守門,對「任何」`Evidence` 建構都會跑(不管是不是走 `dsp_client`),所以「可信證據裝自由文字」這條路徑在建構當下就會丟 `ValueError`,不是只靠 `dsp_client` 自律——這點跟圖譜節點 `[[Systems/任務流程領域模型]]` 裡「自由文字能不能被當成可信,由型別保證,不靠每個用戶端自律」的 RULE 一致。
- `is_id`(短代號檢查)先 `isinstance(value, str)` 才比對格式,布林值進不了這條路;`STATE_FIELDS`/`METRICS_FIELDS` 裡唯一可能收到布林的欄位(`budget`、`version`、`impressions` 等)都走 `is_plain_int`/`is_plain_number`,兩者都明確排除 `bool`,S205 測試裡的 `("state", "budget", True)`、`("metrics", "revenue", True)` 也覆蓋到這個路徑。沒有找到能讓布林或空值繞過短代號檢查、混進可信證據的路徑。

### 已看:可信欄位驗證 vs 模擬 DSP 實際回應(src/rtb/dsp/store.py、server.py)
逐欄核對值域,全部對得上,不會讓分析在正常情況下卡住重試:
- `id`/`campaign_id`:`_get_campaign`/`_get_metrics` 回傳的 `id`/`campaign_id` 就是請求路徑裡的 `task.campaign_id` 本身;而 `TaskStore.create_task` 已經用 `is_id` 驗過 `campaign_id`(`src/rtb/analyzer/task_store.py:187`),所以 `_is_campaign_of` 恆為真,不會誤殺。
- `budget`/`version`:SQLite `INTEGER` 欄位,`Campaign` dataclass 型別是 `int`,`_is_int_between` 的界線(0/1 到 `2**63-1`)跟 `dsp/store.py` 自己的 `SQLITE_INTEGER_MAX = 2**63-1` 同一個上限,不會誤殺。
- `status`:只可能是 `_next_state` 寫入的 `"active"`/`campaign.status` 或改成 `"paused"`,跟 `CAMPAIGN_STATUSES` 完全對得上。
- `window`:用戶端只打 `?window=1h`,`MetricsRecord.window` 回顯的就是查詢用的 `"1h"`,在 `METRIC_WINDOWS` 內。
- `impressions`/`clicks`/`conversions`:`_checked_metric` 保證是整數或 `None`,存進 `INTEGER` 欄位;`_is_count_or_none` 接受 `None` 與 `abs(value) <= 2**63-1`,不會誤殺。
- `spend`/`revenue`:欄位是 `REAL`,SQLite 型別親和性會把插入的整數自動轉成浮點數存回,讀回一定是 `float` 或 `None`;`_is_finite_or_none` 接受兩者。

### 已看:名稱截斷(字元數截斷、代理對/組合字元、512 邊界)
`name[:MAX_UNTRUSTED_TEXT_LENGTH]` 是 Python 字串切片,Python 的字串以「code point」為單位(不是 UTF-16 code unit),不會發生 JS/UTF-16 那種切斷代理對(surrogate pair)產生殘缺字元的問題;真的組合字元(例如「e」+ U+0301)在邊界被切開只會產生視覺上不完整但語法合法的字串,不會讓 `json.dumps(..., ensure_ascii=True)` 或 SQLite 寫入失敗(`json.dumps` 對控制字元與非 ASCII 一律跳脫成 `\uXXXX`,不會把裸控制字元或裸代理值直接送進 `str.encode`)。512、剛好 512(不截斷)、超過 512、空字串、`None`、非字串(數字/字典)這幾種邊界都有測試覆蓋(`test_an_oversized_or_odd_campaign_name_is_bounded_without_failing_the_fetch`),邏輯上沒發現會讓證據建構失敗或寫不進 SQLite 的輸入。

### 已看:舊歷史列相容(S218)
`evidence_for` 直接用讀回的欄位重建 `Evidence(...)`,一定會跑新的 `__post_init__` 驗證(含 `_trust_matches_kind`、`_strings_fit_trust`),不合格會被 `except (ValueError, KeyError)` 包成 `CorruptedHistoryRow`——`src/rtb/analyzer/task_store.py:230`。增量 1 之前的 `dsp_client` 是整份轉存(沒有白名單),但實際能出現在舊列裡的字串只有 DSP 當時真的會回的欄位:`id`/`campaign_id`(短代號格式)、`status`(`active`/`paused`,純英文字母)、`window`(`1h`/`1d`/`7d`)——全部剛好落在 `is_id` 的 `[A-Za-z0-9._:-]{1,128}` 字元集內,所以測試 `test_evidence_rows_written_before_the_allowlist_still_read_back` 讀得回來不是巧合湊出來的邊界情況,而是舊資料形狀本來就相容。沒有找到會讓舊列讀回失敗的具體輸入。

### 已看:既有測試有沒有被改弱
`tests/analyzer/test_dsp_client.py` 的 S42 → S203 改名是這次合約變更(兩筆→三筆)本身,斷言只有變多沒有變少(新增 trust_class、evidence_id、version 一致性的斷言)。`tests/domain/test_evidence.py`、`tests/analyzer/test_task_store.py` 都只新增測試,沒有刪減或放寬既有斷言。

### 已看:圖譜鏡頭
`LUMOS-IMPACT` 附的機械反查三格皆空,但直接查 `[[Systems/任務流程領域模型]]`(about_code 含 `src/rtb/domain/evidence.py`)與 `[[Systems/分析行程流程與檢查點]]`(about_code 含 `src/rtb/analyzer/dsp_client.py`)發現兩篇都有這次改動的直接 RULE,不是不相關節點:
- `任務流程領域模型` 裡 `[since:2026-09-23]` 的 RULE 完整寫出「證據種類多『廣告文字』;信任標記是不可信文字若且唯若種類是廣告文字;標成可信的證據……每個字串值都必須是短代號……不可信文字證據的字串最多 512 字元……讀回歷史表的舊列也會重跑」——這份 diff 的 `_trust_matches_kind`/`_strings_fit_trust` 實作跟這條 RULE 逐字對得上,沒有牴觸。
- `分析行程流程與檢查點` 裡同樣 `[since:2026-09-23]` 的 RULE 逐欄列出白名單的值域(預算 0–2**63-1、狀態 active/paused、版本 1–2**63-1、指標欄位……),跟 `STATE_FIELDS`/`METRICS_FIELDS` 的檢查函式逐項對得上;RULE 也明講「名單外的欄位整個丟掉,連名稱都不記」「現況的內容雜湊只算白名單欄位,只改名稱不會讓現況看起來變了」,對應 `test_only_changing_the_name_leaves_the_state_hash_alone`,一致。
- 同一節點裡 `★INVARIANT-PLANNED★`(事故 F5,`[due:2027-01-15]`)還在 watch 狀態、增量 2(模擬 DSP 加名稱欄位)確實還沒做,跟這份 diff 的範圍聲明一致,不算缺漏。
- `flow.py` 的 `_from_collecting_evidence` 用 `except Exception:`(純讀取,重試永遠安全)包住 `EvidenceSource` 呼叫,`DspRequestFailed`(含這次新增的「可信欄位不合格」情況)是 `Exception` 子類,會被同一條路徑接住、留在原狀態重試,不會被誤判成永久失敗——跟這兩篇筆記裡「`EvidenceSource`……失敗一律視為暫時性、可以放心重試」的 RULE 一致。

沒有發現筆記與程式碼對不上的地方,不需要另立 Issue。

## 總結
severity: clean
blocking 條數:0。
