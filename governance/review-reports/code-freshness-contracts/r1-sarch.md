severity: major
# 架構對齊審查報告

severity: major

## 已查範圍
讀了 `governance/review-reports/code-freshness-contracts/r1-snapshot.patch` 全文;對照 `src/rtb/analyzer/flow.py`、`src/rtb/analyzer/task_store.py`、`src/rtb/analyzer/dsp_client.py`、`src/rtb/domain/evidence.py`、`src/rtb/domain/task_state.py`、`src/rtb/executor/inbox_store.py`/`inbox_server.py`,以及既有測試裡的 ast/monkeypatch 慣例(`tests/analyzer/test_flow.py`、`tests/executor/test_inbox_server.py`、`tests/kit/test_shared_base.py`、`tests/dsp/test_store.py` 等)。

### 1. `policy.decide()` 的 `now` 參數是專案裡第三種處理「現在時間」的做法
severity: major
blocking: 是 這是引入專案原本沒有的第二種(實際是第三種)做法,不是風格偏好
引句:「now: datetime | None = None」
說明:專案現有兩種處理「現在時間」的既定做法都是「呼叫端明確餵時間進來」:①`flow.advance(..., now: datetime, ...)`、`TaskStore.create_task/commit_step/record_tool_call(..., now: datetime)` 都是必填、無預設值的參數,由呼叫端(流程層/進入點)決定並往下傳;②`inbox_store.py`/`inbox_server.py` 用可注入的 `clock: Callable[[], datetime] = utc_now`,而且明確記著「clock 在拿到寫入鎖之後才讀,先讀會拿過時的時間」——顯示這專案很在意「誰在什麼時機讀時鐘」。`domain/evidence.py` 的 `check_freshness(evidence, now, ...)` 也遵守這個既有慣例:`now` 必填、無預設值。

新增的 `policy.decide(task, evidence, now: datetime | None = None)` 卻是第三種寫法:參數可選,函式內部在 `now is None` 時自己讀系統時鐘(`datetime.now(UTC) if now is None else now`)。這造成兩個實際後果:(a) 在 production 路徑,`flow._from_analyzing` 呼叫 `c.decide(row, evidence)` 時不傳 `now`(因為 `Decide` Protocol 本來就沒有這個欄位),於是 `decide()` 內部自己另外讀一次系統時鐘,跟 `advance()` 自己手上已經有、且會傳給 `commit_step` 當 `written_at` 的那個 `now` 是兩次獨立的時鐘讀取,跟 inbox 那邊「單次讀時鐘」的紀律不一致;(b) 測試裡新增的 `test_a_restarted_analyzer_does_not_decide_on_evidence_that_went_stale_while_it_was_down` 得另外包一層 `decide_at_restart` lambda 才能把 `restarted_at` 塞進去,正是因為 `Decide` Protocol 沒有 `now` 這個洞——顯示這個可選參數是繞過既有介面合約做出來的旁路,而不是照現有兩種既定做法(讓 `flow.advance` 已經握有的 `now` 透過 Protocol 往下傳,或用像 inbox 那樣的注入式 `clock`)去擴充。

### 2. 新測試用 `gc.get_referents` + 直改私有表格,跟專案既有的「用 monkeypatch 動內部狀態」慣例不一致
severity: major
blocking: 是 引入專案原本沒有的第二種「測試裡動私有內部狀態」做法
引句:「gc.get_referents(TRANSITIONS)」
說明:`tests/domain/test_task_state.py` 新增的 `test_the_transition_tables_storage_is_not_shared_with_anything_the_module_exposes` 用 `gc.get_referents(TRANSITIONS)` 反射找出 `MappingProxyType` 背後真正的 dict,再直接賦值 `task_state._FLOW[S.FAILED] = frozenset({S.RECEIVED})`,並用手寫 `try/finally` 自行復原成 `task_state._FLOW[S.FAILED] = frozenset()`。

專案裡「測試要伸手動模組/類別的私有或內部狀態」這件事已有穩定、重複出現的既定做法:`pytest` 的 `monkeypatch.setattr(...)`,在 `tests/kit/test_shared_base.py`、`tests/kit/test_sqlitekit.py`、`tests/kit/test_httpkit.py`、`tests/executor/test_inbox_server.py`、`tests/analyzer/test_flow.py`、`tests/dsp/test_store.py`、`tests/analyzer/test_inbox_client.py` 至少七支測試檔、十餘處都是這樣用,而且 `monkeypatch` 會在測試結束時自動還原,不需要手寫 `try/finally`。這支新測試改用 `gc.get_referents` 找私有 dict 物件、再手動改寫加手動復原,是整個測試套件裡第一次出現的技巧,跟既有慣例是兩種不同的做法,且手動復原比 `monkeypatch` 的自動 teardown 更脆弱(斷言先於 `finally` 拋出例外時仍會執行沒問題,但一旦有人在這段中間又加一個提早 `return` 就會漏復原,`monkeypatch` 沒有這個風險)。

## 其他已查、判定一致的地方(供參考,非發現)
- 分層方向:`policy.py` 呼叫 `rtb.domain.evidence.check_freshness`(既有函式,本次只是接上呼叫,不是新寫決策邏輯進領域層),`flow.py` 完全沒被這個 patch 改動,新鮮度判斷放在 policy(決策層)、routing 仍留在 flow(流程層)——跟既有「domain 給純函式、analyzer 用純函式做決策、flow 只認 Decision 型別做狀態轉換」的分工一致。
- 命名與錯誤處理:`_all_fresh` 私有輔助函式命名跟既有 `_payload` 同風格;`MAX_EVIDENCE_AGE` 常數命名、註解風格跟 `UNDERPACING_THRESHOLD`、`BUDGET_INCREASE_FRACTION` 一致;沒有新增例外類別或改變既有「缺值一律 NoAction、不猜不丟例外」的錯誤處理慣例。
- 新測試用 `ast` 解析原始碼做結構性檢查(`tests/analyzer/test_boundaries.py`、`tests/domain/test_metrics.py`):這不是新做法。專案裡至少 `tests/dsp/test_store.py`、`tests/executor/test_inbox_server.py`、`tests/kit/test_shared_base.py`、`tests/analyzer/test_flow.py` 早就各自用 `ast.walk` 寫自己專屬的結構掃描(每支測試檔案各自量身寫一份、不抽共用輔助),是這個專案一貫的做法,不是第二種做法,也不算「該抽公用卻各寫一份」——因為現況本來就是「各邊界各寫一份」,新測試只是照著既有慣例多寫一份。
