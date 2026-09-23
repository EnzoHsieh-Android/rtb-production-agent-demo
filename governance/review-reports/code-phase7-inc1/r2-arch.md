severity: clean

# 架構對齊審查 — code-phase7-inc1 r2

## 驗收:F1(DSP 用戶端重複開 MAX_DB_INT)

修到位。`MAX_DB_INT = 2 ** 63 - 1` 整個刪除,改成 `from rtb.domain.proposal import MAX_INT`,兩處使用點(`_is_int_between`、`_is_count_or_none`)都改讀 `MAX_INT`。跟 `src/rtb/analyzer/policy.py:25` 的 `from rtb.domain.proposal import MAX_INT, ActionType, Proposal` 是同一種匯入寫法,沒有另開一份上限常數。

file: `src/rtb/analyzer/dsp_client.py:28`(新增匯入)、`src/rtb/analyzer/dsp_client.py:47-51`(兩處改用 MAX_INT)

## 驗收:F2(白名單檢查函式簽章多帶 TaskRow)

修到位。`Check` 型別從 `Callable[[Any, TaskRow], bool]` 改成 `Callable[[Any], bool]`,`STATE_FIELDS`/`METRICS_FIELDS` 裡每支檢查函式都只收值(`lambda value: ...`);原本混在 `_is_campaign_of(value, task)` 裡的廣告編號核對抽出來,搬到 `_trusted` 內部單獨比對 `body[campaign_field] != task.campaign_id`。查過 `src/rtb/domain/proposal.py:163` 的 `CHECKS: dict[str, Callable[[dict[str, Any]], bool]]`,提案那份實際上是收整份 `raw` 字典(檢查函式自己 `raw[欄位]` 取值),不是逐字「只看值」;但作者這次做的「單欄檢查只碰自己的值,跨欄/跨情境的核對抽成獨立步驟」跟提案檔 `_field_errors` + `_expiry_errors` 分離的結構是同一種做法(單欄檢查對單欄檢查,跨欄核對另開一支、放在迴圈外面),沒有引入新結構。

file: `src/rtb/analyzer/dsp_client.py:32`(Check 型別)、`src/rtb/analyzer/dsp_client.py:56-72`(STATE_FIELDS/METRICS_FIELDS/_trusted)

## 修正本身有沒有引入新的第二種做法或跨層

沒有查到。廣告編號核對從「塞進逐欄 Check」改成「_trusted 內部一個獨立 if」,跟 `proposal.py` 裡「_field_errors 逐欄、_expiry_errors 另開一支做跨欄核對」是同一種切法,不算第三種做法。`_trusted` 新增的 `campaign_field: str` 參數是呼叫端(`fetch` 內兩次呼叫分別傳 `"id"`/`"campaign_id"`)顯式指定,沒有反查或隱式推斷,呼叫路徑仍在 analyzer 層內部,沒有跨層直呼 domain 以外的東西。

`evidence.py` 的 `_strings_fit_trust` 順手擴大成連 `payload.keys()` 一起檢查(`[*payload.keys(), *strings]`),沿用同一支 `is_id`,沒有另開檢查邏輯,結構上是既有函式的输入範圍擴大,不是新路徑。配的測試 `tests/domain/test_evidence.py` 新增 `test_trusted_evidence_keys_are_short_codes_too`,對應到本輪修正的行為。

已查:src/rtb/domain/proposal.py(MAX_INT 定義處、CHECKS/_field_errors/_expiry_errors 結構)、src/rtb/analyzer/policy.py(既有匯入慣例)、src/rtb/domain/_checks.py(is_id 定義)、src/rtb/domain/evidence.py(_strings_fit_trust 前後文)。機械反查三格皆空,沒有額外命中可交叉比對。

不對齊共 0 條,其中 major 0 條。
