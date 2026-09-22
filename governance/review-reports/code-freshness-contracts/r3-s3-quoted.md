severity: clean

## 審查方法與範圍

讀了 `governance/review-reports/code-freshness-contracts/r3-snapshot.patch` 全文(`dsp_client.py`、`flow.py`、`instrumented.py`、`policy.py`,以及對應的六支測試檔),並在 `/Users/enzo/rtb-production-agent-demo` 直接跑了 `pytest -q`(全套 665 個測試,含 `tests/analyzer/`、`tests/domain/test_task_state.py`)、`mypy src/rtb/analyzer/{flow,dsp_client,instrumented,policy}.py`、`ruff check`,三者皆乾淨,沒有再修改任何專案檔案。另外在 `/tmp/review-s3`、`/tmp/review-s3b` 兩份副本裡各做了一次實驗性驗證(不影響原專案)。

沒有找到會做出錯誤行為、破壞合約或資料的問題。逐項查過的重點:

### 1. 簽章一致性

全專案 grep `EvidenceSource`/`Decide`/`make_client`/`evidence_source(`/`decide(` 的每個實作與呼叫點(`dsp_client.py:67`、`instrumented.py:34,52`、`flow.py:152,170`,以及六支測試檔),兩參數/三參數都同步更新,沒有殘留舊簽章的呼叫點。

引句:「def __call__(self, task: TaskRow, now: datetime) -> tuple[Evidence, ...]:」

測試裡唯一用「吃任意參數」假物件的是 `tests/analyzer/conftest.py` 的 `Counting.__call__(self, *args)`,但這不會遮蔽漏傳參數:`test_decide_receives_the_same_now_that_advance_was_given`、`test_evidence_source_receives_the_same_now_that_advance_was_given` 都用索引直接斷言第二/三個引數的值,若漏傳或順序寫反,會直接 `IndexError` 或斷言值不符而紅,不是假綠。

引句:「assert d.calls[0][2] == later」

引句:「assert source.calls[0][1] == later」

### 2. 時間單一來源的四種真實情境

- 推進者整批共用一個時間:`tests/analyzer/test_boundaries.py` 的 `_walk_one_task_end_to_end` 用真的 HTTP 伺服器驗證過,年齡恆為 0,正常跑到底,不會卡彈跳。

引句:「整批共用同一個時間:推進者很自然的寫法,不能因此卡在蒐證與分析之間彈跳」

- 推進者每步取當下時間:evidence 的 `observed_at` 一定 ≤ 之後任一步讀到的 `now`(時間只會往前走),年齡為正且合理,不會誤判。這是靠 `flow.py` 把同一個 `now` 一路往下傳確保的。

引句:「evidence = c.evidence_source(row, now)」

- 當機重啟後很久才推進:`test_a_restarted_analyzer_does_not_decide_on_evidence_that_went_stale_while_it_was_down` 驗證過,`store.evidence_for()` 讀回舊 `observed_at`,重啟後的 `now` 年齡算出來很大 → `EXPIRED` → 正確退回 `COLLECTING_EVIDENCE`,不會誤判成新鮮。

引句:「state = flow.advance(store, "t1", lambda _t, _n: (), policy.decide, lambda _p: None,」

- 時鐘倒退:`check_freshness`(`src/rtb/domain/evidence.py:129-130`,此 patch 未改動)本來就是「年齡 < 0 直接判過期,不看差多少」,所以時鐘倒退只會讓流程多退回蒐證一次(安全側的保守行為),不會有卡死、也不會有過時證據被判新鮮的情況。`EvidenceSource` 協定的新增說明也明講了這個機制:

引句:「否則推進者整批共用一個時間時,證據會比決策用的時間還晚,年齡變負而被判過期。」

### 3. DSP 讀取比 `now` 晚幾秒,新鮮度偏向哪邊

`observed_at` 蓋的是呼叫端傳入、且早於實際 HTTP 往返完成的那個 `now`,所以算出來的年齡只會偏大(把證據看得比實際更舊),不會偏小。也就是說這個改動只會讓判斷更保守(較容易觸發重新蒐證),不可能把該過期的證據判成新鮮。

引句:「證據的讀取時間用呼叫端(流程層)傳來的時間,不自己讀系統時鐘,見 flow.EvidenceSource」

用 `/tmp/review-s3/repro_reuse_now.py` 重現了代碼審第 2 輪(`r2-s2.md`)當時抓到的「`dsp_client` 仍自己讀時鐘,批次共用 `now` 時年齡變負」的問題,確認這一版(r3)已經修好:`dsp_client.py` 不再呼叫 `datetime.now(UTC)`,而是老實用呼叫端傳入的 `now`,重跑同一支腳本三步後正確走到 `proposed`,不再卡在 `collecting_evidence`。

引句:「def fetch(task: TaskRow, now: datetime) -> tuple[Evidence, ...]:」

同時也用 Python 直接檢查過 `Decide.__call__.__doc__`、`EvidenceSource.__call__.__doc__` 確實掛上了說明字串,證實 `r2-s2.md` 第 2 點抓到的「docstring 位置寫錯,掛不上去」的問題這版也修好了(`def __call__` 改成多行、`:` 收尾,docstring 落在函式本體第一敘述,不再是類別本體的孤兒字串)。

引句:「`now` 是 `advance()` 手上、也會寫進歷史列的同一個時間;決策層不自己讀系統時鐘。」

### 4. 新測試是否假綠

對 `tests/domain/test_task_state.py` 重寫的 `test_no_dict_the_module_exposes_can_change_the_transition_table`,在副本裡故意把 `TRANSITIONS` 改成直接包住 `_FLOW`(重現「唯讀視圖底下共用同一份可變字典」的漏洞),這支測試立刻紅(抓到 `TRANSITIONS[FAILED]` 被改動),證實不是恆真斷言。

引句:「exposed = [name for name, value in vars(task_state).items() if isinstance(value, dict)]」

引句:「assert can_transition(S.FAILED, S.RECEIVED) is False」

另外對 `tests/analyzer/test_dsp_client.py` 新增的「讀取時間用呼叫端傳來的時間」斷言,也確認它是真的在測而不是裝飾性斷言(若 `dsp_client.py` 忘了把 `now` 傳進 `Evidence`,這行會直接對不上而紅)。

引句:「assert all(e.observed_at == NOW for e in evidence)」

## 結論

本次 r3 patch 看起來就是針對代碼審第 1、2 輪已記錄的 major 發現(`now` 參數原本可選自己讀時鐘、`dsp_client` 沒接住呼叫端的 `now`、docstring 位置寫錯、測試用 `gc.get_referents` 而非既有慣例)做的修正,逐一驗證後這幾點都確實補上了,沒有再看到新的遺漏。
