severity: major

## F1 兩支「結構檢查」測試(★INVARIANT★ 事故 F6 綁定測試之二)可用不改變行為的機械改寫繞過,審計員的 kill 配方對此是循環證據
severity: major
blocking: 是 — 這兩支測試正是這次要把事故 F6 從「讀碼推論」轉正為「有機械守衛」的關鍵補丁(第 1 次獨立審計就是因為缺這兩支才不同意),但它們本身只做純文字比對,静态字串一拆就瞎,等於把「排他性宣稱」的守衛換成另一個一樣脆弱、只是換了一層皮的守衛;現在的驗收紀錄與第 2 次審計都把它們當成「補上了機械守衛」來採信,屬於自我實現的假綠。

引句:「死信只能由管理指令逐筆重放:把死信改回待處理的寫法全庫只有重放方法一處。」

引句:「全庫沒有任何語句修改、刪除或覆寫這兩張表。」

- `test_only_the_replay_method_revives_a_dead_letter`(`tests/executor/test_dead_letter.py:461`)靠 `_functions_containing(REVIVE)`(`tests/executor/test_dead_letter.py:448`)用 `ast.walk` 找「含有子字串 `dead_letter_reason = NULL` 的字串常數」所在的函式,斷言只有 `_replay` 一個。`test_the_dead_letter_tables_are_only_ever_inserted_into`(`tests/executor/test_dead_letter.py:466`)是對整個 `src/rtb/**/*.py` 原始碼文字做 regex `\b(UPDATE|DELETE\s+FROM|...)\s+(dead_letters|dead_letter_ops)\b`。兩者都只認得「同一個字串常數/同一段連續文字裡剛好排在一起」的寫法。
- 我在 `/tmp/f6rev`(`cp -R` 出來的隔離副本,沒有對 `/Users/enzo/rtb-3b` 做任何寫入)做了兩個實驗,都不改動任何既有測試,只加新程式碼:
  1. 在 `receive()`(收件口 `_own`/取件那段,對應真正的定期入口)裡加了一段:用 `col = "dead_letter_reason" + " = NULL"` 拼出 SQL,對 `disposition = 'dead_letter'` 的所有列執行 `UPDATE proposals SET disposition = NULL, ... deliveries = 0`——這正是被 kill 配方描述為「死信不必重放就會再被交出去」的那種違規。跑
     `.venv/bin/python -m pytest tests/executor/test_dead_letter.py::test_only_the_replay_method_revives_a_dead_letter tests/executor/test_dead_letter.py::test_the_dead_letter_tables_are_only_ever_inserted_into`
     結果 **2 passed**——這兩支結構檢查完全沒看到新違規(因為 `+` 讓字串在 AST 裡拆成兩個 `Constant`,子字串比對就找不到)。同時原本的行為測試 `test_no_periodic_work_or_resend_revives_a_dead_letter` 確實抓到了(`process_one()` 回傳 `EXECUTED` 而不是 `IDLE`),說明目前這條路徑有行為測試當第二道防線頂住,但那是巧合式的重疊,不是結構檢查本身在守。
  2. 另外加了一個完全獨立、甚至沒有被任何地方呼叫的方法 `_quietly_compact_envelope`,對 `dead_letters` 表做 `verb = "UPD" + "ATE"; self._conn.execute(f"{verb} {table} SET deliveries = 0 WHERE id = ?", ...)`(`table = "dead_letters"` 也是變數)。跑
     `.venv/bin/python -m pytest tests/executor/test_dead_letter.py::test_the_dead_letter_tables_are_only_ever_inserted_into`
     結果 **1 passed**——regex 完全沒抓到,而且這次**沒有任何其他測試在守**(現有 19 支測試裡,對「死信表只增不改」這句話唯一的機械檢查就是這一支 regex)。
- 再回頭看 `Systems/提案收件口.md` 開頭欄位裡給這兩支測試配的 kill 配方,用的字串跟測試找的字串一字不差:
  引句:「放掉租約時順手把死信改回待處理,死信不必重放就會再被交出去」
  對應的 `old`/`new` 就是把 `dead_letter_reason = NULL` 原樣接在既有那一句字串常數後面,`lumos guard kill` 套用這個配方之後測試當然會翻紅——但這只證明「照這一種寫法改」會被抓到,不能證明「檢查本身抓得住任意寫法的同一個違規」。兩次獨立審計(`f6-audit-1.md`、`f6-audit-2.md`)都只驗證了「配方套下去會翻紅」與「讀碼確認目前只有一處」,沒有人試過「用另一種寫法製造同一個違規、看檢查還抓不抓得到」,所以這個假綠沒有被抓出來。
- 建議修法方向(不要求現在就改,但應在合入前處理或至少把這條已知弱點寫回筆記並訂回頭條件):結構檢查改成在測試裡對 `sqlite3.Connection.execute`(或 `Cursor.execute`)做攔截/monkeypatch,在真的跑資料庫語句時檢查「這次執行的 SQL 字串」而不是靜態原始碼文字;或至少把 AST 掃描换成先用 `ast.parse` 求出每個 `Call` 節點裡最終傳給 `execute` 的字串(包含常數折疊 `BinOp`/`JoinedStr` 的求值),而不是逐個字串常數各自比對子字串。

## F2 收件口寫入時間改走嘗試紀錄模組轉換之後,目前找不到會傳入沒帶時區時間的呼叫端;字串格式沒變

不算發現,列成已查核的觀察:`inbox_store.py:405` 的 `_iso` 現在轉呼叫 `attempt_store.iso`(`src/rtb/executor/attempt_store.py:205`),兩者格式完全一樣(`moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")`),所以既有靠字串比大小判斷時間先後的邏輯(過期標記、保留期清理排序等)不受影響。追了 `_iso`/`iso` 的每一個呼叫路徑:
- `now` 一律來自某個 `clock: Callable[[], datetime]`,production 端預設值都是 `inbox_store.utc_now`/`datetime.now(UTC)`(`inbox_server.py`、`execution.py`、`approve.py`、`replay.py`、`runner.py` 全部同一個預設,沒有任何生產路徑手動塞一個 naive `datetime`);測試端 `tests/executor/conftest.py:14` 的 `Clock` 起始值 `datetime(2026, 9, 22, 12, 5, tzinfo=UTC)` 也帶時區。
- 提案裡的 `decision_created_at`/`decision_expires_at`(不可信輸入,直接被 `_iso` 拿去轉字串寫進資料庫)在進到收件口之前已經在網域層擋過:`src/rtb/domain/proposal.py:110-123` 的 `_parse_time` 對每個時間欄位做 `is_aware(parsed)` 檢查,不帶時區直接回 `None`,`_require_time`(`:126`)再把 `None` 轉成 `ValueError`,而且驗證步驟(`FIELD_VALIDATORS`,`:175-176`)在 `_require_time` 之前就跑過一次同樣的判斷,所以「沒帶時區的提案本文」在還沒進到 `inbox_store._iso` 之前就已經被解析層擋掉,不會讓收件口那邊的 `ValueError("時間必須帶時區")` 意外從一個正常的拒收路徑變成未接住的例外。
- 沒有找到任何呼叫端(伺服器、`runner.py` 執行迴圈、`replay.py`、分析端、`tests/executor/fakes.py` 測試替身)會把 naive 時間傳進收件口的寫入路徑。

## F3 定期入口覆蓋面與合約措辭核對:與程式行為一致

不算發現,列成已查核的觀察:
- `test_no_periodic_work_or_resend_revives_a_dead_letter` 呼叫的 `process_one()`/`process_awaiting()`/`reconcile_all()`/`h.store.accept(prop, h.clock)` 剛好對應真正生產環境唯一的定期入口——`src/rtb/executor/runner.py` 的 `_loop()`:「troubled = executor.reconcile_all()  # 先對帳,再處理待核可,再處理新提案」,`executor.process_awaiting()`,`result = executor.process_one()`,順序與呼叫對象和測試裡完全一致,沒有另外的背景執行緒或排程路徑(`inbox_server.py`、`execution.py` 內都沒有 `Thread`/`while True` 之類的隱藏迴圈)。這支測試確實走到了每一個會被定期呼叫的入口。
- 事故 F6 合約措辭與程式行為對照:「有這一次真的需要、此刻有效的核可則不判新鮮度」對應 `execution.py` 的 `_stale_without_approval`,「決策建立超過 15 分鐘」與 `guardrails.py` 的 `DECISION_FRESHNESS = timedelta(minutes=15)` 一致,兩次獨立審計已逐條核對過測試斷言與程式行為,本次複查沒有發現新的落差。
- Phase 8 驗收紀錄(`docs/rtb-production-agent-demo-knowledge/Verification/Phase8驗收紀錄.md`)裡「補了全庫結構檢查……與定期入口加重送都不改回死信的行為測試」這句話本身沒有說錯——這兩類測試確實都存在、確實都綁進了 kill 配方且套用配方後會翻紅——但如 F1 所述,它把「結構檢查」的證明力等同於「機械守衛」,沒有揭露這類靜態文字檢查在面對不改變行為的改寫時會失效,這一點審計報告與驗收紀錄都沒有寫明,算是說得比證據能撐的滿一點,但不到刻意誇大的程度(第 2 次審計的「次要建議」已經點出「沒有略過任何一關的旗標」缺專屬配方,方向類似,只是沒點到這兩支測試本身的脆弱性)。
