severity: major

### 1. 這批測試沒有真正驗到「重啟恢復」(`recover_in_flight`),拿掉它九支測試照樣全綠

severity: major
blocking: 是 這批測試是事故 F2 轉正的證據,若「重啟恢復」這個被設計文件點名的機制壞掉或被拿掉,測試套件不會出聲,轉正會建立在假的保障上。
引句:「走真的啟動程式重啟(單一執行者鎖、重啟恢復、每輪先對帳)」

測試檔的模組說明把「重啟恢復」列為這批測試比既有測試多證明的兩件事之一(patch 第 19 行),對應到 `src/rtb/executor/runner.py` 的 `_open_and_recover` 呼叫 `attempt_store.recover_in_flight`。但實際把生產程式碼複製到臨時目錄、讓 `recover_in_flight` 整個失效(不轉任何鍵)之後重跑 `tests/executor/test_crash_recovery.py`,九支測試(S118、S123、S119、S122、S120、S124、S121、S125、S126)全部依然通過:

```
$ PYTHONPATH=src .venv/bin/python -m pytest tests/executor/test_crash_recovery.py -q
9 passed in 2.36s
```

原因是:這九支測試的 `restart()` 一律把時鐘撥快 `SHIFT_SECONDS=300` 秒(遠大於 60 秒租約),所以重啟後對帳(`Executor.reconcile_all` → `_reconcile`)自己就能透過 `attempt_store.unresolved_keys` 撿到還在 `in_flight` 的鍵、再靠 `_take_over` 拿到已過期租約的新收據、自己把它轉成 `unknown`(`execution.py` 的 `_reconcile` 裡「過期工作者留下的嘗試中:在新收據下先轉成結果不明」那段)。也就是說,`recover_in_flight` 在這批測試建構的情境裡永遠是多餘的一步,對帳自己就會做同一件事。

對照組:同樣的「拿掉 `recover_in_flight`」實驗跑在既有前例 `tests/executor/test_runner.py::test_the_runner_holds_a_single_instance_lock_and_recovers_before_ready`(S55)上,會正確地紅:

```
FAILED test_the_runner_holds_a_single_instance_lock_and_recovers_before_ready
AssertionError: assert 'in_flight' == 'unknown'
```

這證明 `recover_in_flight` 本身不是死碼——它真正必要的情境是「嘗試中的鍵沒有對應的處理中收件列」(S55 用 `attempt_store.begin()` 直接造出脫離收件表的舊資料),而新測試檔的九個死點全部發生在 `receive()` 已經把收件列寫成 `in_progress`之後,所以永遠有處理中列可以被 `_take_over` 接手,`recover_in_flight` 的獨立效果從未被這批測試施壓過。換句話說,測試檔文件宣稱驗到的「重啟恢復」,九支測試其實一支都沒有真的依賴它——這正是判準裡的「測試在被守的程式拿掉後仍然綠」。

file: `src/rtb/executor/attempt_store.py:423`(`recover_in_flight` 定義)
file: `src/rtb/executor/runner.py:117`(`_open_and_recover` 呼叫它)
file: `tests/executor/test_runner.py:113`(既有前例 S55,同一種拿掉法會正確變紅)

### 2. `die()` 診斷查詢沒有依鍵或任務過濾,測試檔擴充後容易悄悄印出錯的列

severity: minor
blocking: 否 目前這九支測試在死點時 `attempts`/`proposals` 表裡永遠只有一列相關資料,不影響現有斷言的正確性,是潛在脆弱性而非現有錯誤行為。
引句:「SELECT state FROM attempts ORDER BY seq DESC LIMIT 1」

`CHILD` 內嵌腳本的 `die()` 用 `"SELECT state FROM attempts ORDER BY seq DESC LIMIT 1"` 取得「死點狀態」印出來給斷言比對,但這個查詢沒有 `WHERE key = ?`;`seq` 是每把鍵各自從 1 起算(主鍵是 `(key, seq)`),全表 `ORDER BY seq DESC` 挑到的是「seq 數值最大的那一列」,不是「最近寫入的那一列」。目前每個死點測試只提交一份提案、只有一把鍵在途,所以恰好答對;但這支測試檔一旦被延伸成同時有多把鍵在途的情境(例如之後要加「兩把不同鍵同時當機」的案例),這行查詢可能撈到另一把鍵的狀態,讓死點斷言比對到錯的字串卻可能剛好巧合通過,或誤判死點位置。同一個問題也出現在下一行的 `"SELECT disposition FROM proposals"`(沒有 `WHERE task_id = ? AND revision = ?`,同樣是全表取一列)。

file: `tests/executor/test_crash_recovery.py:57-58`(對應 patch 第 56-57 行)
