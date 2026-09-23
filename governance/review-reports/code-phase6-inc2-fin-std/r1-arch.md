severity: clean

本輪只看架構對齊(這寫法跟專案既有的一不一樣),不重複找 bug。

[S406] 舊收件表造法與 [S338] 的先例一致:兩者都是用 `sqlite3` 直接讀 `sqlite_master` 拿舊表 DDL、字串替換拿掉新值、`ALTER TABLE proposals RENAME TO p_old` 接 `executescript` 重建、`INSERT ... SELECT` 搬資料、`DROP TABLE p_old`,連 `noqa: S608` 註解都照抄(`tests/executor/test_aggregate_limit.py:307`、`tests/executor/test_guardrails.py` 內 `_increment_1_inbox`)。差異只是把 [S338] 內嵌在單一測試函式裡的建表/驗證邏輯,拆成 `_increment_1_inbox` 與 `_block_new_by_ratio` 兩個 helper 讓 `test_an_old_inbox_accepts_the_new_block_code_after_opening` 呼叫——這是同一造表手法下的重構,不是第二種做法,也沒跨層,判定與既有先例一致。

三份清單常數(`SINGLE_RULE_CODES`、`NON_SINGLE_CODES`、`HISTORICAL_CODES`)放在 `tests/executor/test_guardrails.py`,被 `tests/executor/test_execution.py` 用 `from tests.executor.test_guardrails import HISTORICAL_CODES` 匯入。查了專案裡測試模組互相匯入的先例:`tests/executor/test_multi_worker.py` 匯入 `tests.executor.test_runner`、`tests/executor/test_executor_boundaries.py` 匯入 `tests.analyzer.test_boundaries` 的 `NETWORK_MODULES`、`tests/dsp/test_void.py` 匯入 `tests.dsp.test_capability` 的 fixture。可見「測試模組互相匯入常數/fixture」在本專案是既有慣例,不是這次新創的作法;沒有觀察到把這類常數統一放進 `tests/executor/fakes.py` 的更一致放法——fakes.py 目前收的是替身類別與 `proposal()`/`write_config()` 這類建構 helper,不收像 `BLOCK_TRIGGERS` 分類這種跟單一測試檔案(護欄表)綁在一起的列舉分類常數。放在 test_guardrails.py 並被匯入,跟現有慣例一致。

[S402] 從參數化(1005 項)改成單一迴圈、失敗時回報第一個違反的預算(`test_the_analyzer_policy_never_trips_the_ratio_cap` 用 `next((b for b in budgets if ...), None)`)。這是測試技巧的選擇,不涉及第二種實作做法或跨層,且沒有發現與其衝突或更一致的既有寫法(`tests/analyzer/test_f5_end_to_end.py` 也用同樣的 `next((... for ... in ...), None)` 慣用語找「第一個/最後一個符合的」,寫法互相呼應)。

綜合:三處都跟專案既有造法一致,沒有 major 發現。
