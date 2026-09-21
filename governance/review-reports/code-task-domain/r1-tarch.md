severity: minor

# r1 tarch 架構對齊審查

範圍:evidence.py、proposal.py、task_state.py 與 metrics.py、dsp/store.py 的慣例對照,以及兩篇知識筆記。
結論:沒有引入第二種做法或跨層直呼。例外與結果型別的分工符合 metrics.py docstring;領域層匯入乾淨。以下皆為重複與一致性問題。

## Finding 1:輔助函式與 ID 正則多處各寫一份
severity: minor
blocking: 否 重複而非分歧,目前行為一致,只有日後改一處漏另一處的漂移風險。

- 位置:src/rtb/domain/evidence.py、src/rtb/domain/proposal.py 各有 `_is_plain_int`,與 `src/rtb/dsp/store.py:110` 同一份;metrics.py 沒有這個函式(只有 `_is_number`、`_is_finite_number`),派工說明說 metrics 也有一份與事實不符,實為三份。
- ID 正則 `[A-Za-z0-9._:-]{1,128}` 在 evidence.ID_PATTERN、proposal.ID_PATTERN、store.IDEMPOTENCY_KEY_PATTERN(`src/rtb/dsp/store.py:49`)各一份。
- 走到哪:proposal.evidence_refs 必須符合 proposal.ID_PATTERN,而它指的是 Evidence.evidence_id(符合 evidence.ID_PATTERN);兩份正則若有一邊放寬,提案就可能引用不合法的證據編號,兩邊互不知道。
- 佐證:引句:「ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")」
- 建議:領域層抽一個小共用模組(例如 rtb/domain/_checks.py 放 `is_plain_int`、`ID_PATTERN`),proposal 直接引用 evidence 的 ID_PATTERN 或共用處;dsp/store 依賴方向往內引用領域層也不違反原則。不抽也可,但至少 proposal 從 evidence 匯入 ID_PATTERN,讓「證據編號格式」只有一個定義。

## Finding 2:Proposal 與 ParsedProposal 不自我驗證,與同層 Evidence、MetricResult 的做法不一致
severity: minor
blocking: 否 唯一入口 parse_proposal 有驗證,直接建構繞過驗證目前沒有呼叫端,屬慣例落差。

- 位置:src/rtb/domain/proposal.py 的 Proposal、ParsedProposal。
- Evidence 用 `__post_init__` 驗證並丟 ValueError,MetricResult 同樣如此;Proposal 卻是裸 dataclass,可以 `Proposal(action_type="rm -rf", ...)` 直接建出。ParsedProposal 的「有值則無錯、無值則有錯」不變式只寫在 docstring,`ParsedProposal(None, ())` 合法建構(既無提案也無錯誤,呼叫端若判 `if not errors` 會誤當成功)。
- 佐證:引句:「成功時 proposal 有值、errors 為空;失敗時 proposal 為 None、errors 列出所有問題。」

## Finding 3:提案檢查表混用 lambda 與具名函式,且有未使用的 FIELDS
severity: minor
blocking: 否 可讀性與死碼,不引入第二種做法。

- 位置:src/rtb/domain/proposal.py 的 `FIELDS` 與 `CHECKS`。
- `FIELDS` 全專案只出現在定義處(grep 無其他引用),與 CHECKS 的鍵重複一份欄位清單,兩者會漂移。
- CHECKS 十二項中十一項是重複 `raw["x"]` 的 lambda,`requested_change` 是具名 `_valid_change`(內部改用 `raw.get`)。lambda 依賴「缺欄位已先在 `_field_errors` 擋掉才會呼叫」才不會 KeyError,這個隱含前提沒寫在表旁。專案其他處(store.py 的 `_is_storable_count` 等)用具名小函式。
- 佐證:引句:「CHECKS: dict[str, Callable[[dict], bool]] = {」
- 建議:刪 FIELDS(需要順序就用 `tuple(CHECKS)`);表的註解補一句「呼叫前已確認欄位存在」,或改成 `(value) -> bool` 的檢查函式,由迴圈傳入 `raw[field]`,lambda 裡就不用重複索引。

## Finding 4:狀態機表是資料,但終點判定與可變性有小落差
severity: minor
blocking: 否 表確實是資料、轉換沒有散在條件判斷,只是建構細節可更明確。

- 位置:src/rtb/domain/task_state.py 的 `_FLOW`、`TERMINAL_STATES`、`TRANSITIONS`。
- 終點狀態靠「`_FLOW` 裡出路為空」反推,沒有明列;新增狀態時若忘了寫進 `_FLOW`,`TRANSITIONS[...]` 會 KeyError 而不是被測試明確抓到(現有測試檢查表完整,可接受)。`TRANSITIONS` 是可變 dict,外部可改(`TRANSITIONS[S.COMPLETED] = ...`),而 proposal 的 requested_change 已用 MappingProxyType 防這類問題,做法不一致。
- 佐證:引句:「TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {」
- `S = TaskState` 別名只為縮短表,可接受。

## 已讀,無 finding
- 例外與結果型別的選擇:IllegalTransition 與 check_freshness 的輸入錯誤丟 ValueError(程式錯誤),parse_proposal 對不可信輸入回結果型別,符合 metrics.py 與 dsp/errors.py 記載的分工。
- 領域層依賴:三個新模組只匯入標準函式庫的純模組,沒有跨層直呼(`src/rtb/domain/ruff.toml` 的禁用清單涵蓋)。
- tests/domain 三個測試檔:已讀,無 finding。
- 知識筆記 Systems/任務流程領域模型.md 與 Projects/RTB_Phase2任務流程_計劃.md:狀態清單、合法轉換、終點、四種新鮮度結果、PITFALL 引用的五個測試名稱皆在 tests/domain 實際存在(逐一 grep 驗過);已知缺口(年齡上限由呼叫端傳入、風險摘要只是字串、無設計審)如實。唯一沒提到的是 Finding 2 的落差,不算失實。

## 總結
最嚴重等級為 minor,blocking 條數 0。
