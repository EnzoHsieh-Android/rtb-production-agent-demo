severity: clean

新測試 tests/analyzer/test_f4_end_to_end.py 在寫法上跟同層既有測試一致,沒有引入第二種做法,也沒有跨層直呼。

伺服器啟停與 try/finally 的組法跟 test_f5_end_to_end.py 的 run_once 一模一樣:DSP 與收件口兩個伺服器各自起在自己的 try/finally 裡,連啟動執行緒都包在保護傘內,內層失敗外層照樣關得掉(f4 的 run_f4 就是照抄這段結構,連注解都寫著「比照 F5 端到端」)。真實時間的用法也一致,兩邊的 `_now()` 都回 `datetime.now(UTC)`,理由都寫「DSP 只容許 30 秒的時鐘誤差」。

推進迴圈與呼叫紀錄包裝這塊,f4 的 `_advance` 跟 f5 的 `_analyze` 內層迴圈同一個骨架:每輪重讀當下那一列、用 `instrumented.InstrumentedSubmit(analyzer, send, "inbox:submit", row)` 包起送件呼叫再丟進 `flow.advance`,跑滿 8 輪或進終點就停。f4 多帶了 `operation_lookup=instrumented.InstrumentedOperationLookup(...)` 這個參數,但這是 `flow.advance` 既有的簽章(src/rtb/analyzer/flow.py 的 `_Collaborators`/`advance`)與既有的類別(`InstrumentedOperationLookup`、`dsp_client.make_operation_lookup`,兩者在 tests/analyzer/test_instrumented.py、tests/analyzer/test_dsp_client.py 已經個別測過),不是端到端測試自己發明的新東西,只是把既有協作者一起接上而已,跟 f5 只接 submit 是同一種「接既有協作者」的做法,不算第二種做法。

另一方搶先寫入的兩種手法都對得上 test_version_conflict.py 的既有做法:
- 執行前就被改:f4 的 `_other_writer_changes_the_budget` 直接用 `CampaignStore(dsp_db).execute(Operation(...))` 寫,跟 test_version_conflict.py 的 `_fail_with_version_conflict`/`_one_writer` 一樣是「另一方直接寫店(store)」,不是繞過 store 直接戳資料庫寫入。
- 送出前一刻被搶先:f4 的 `_PreemptedClient(DspClient)` 覆寫 `write()`,第一次呼叫時先觸發另一方寫入再呼叫 `super().write()`,這跟 test_version_conflict.py 的 `_RacingClient(DspClient)`(同樣繼承 `DspClient`、覆寫 `write()` 插一腳,在裡面等柵欄再呼叫 `super().write()`)是同一種「DspClient 子類別插一腳」的手法,沒有另開一條路徑(例如直接改 executor 內部狀態或跳過 DspClient 呼 DSP)。

驗證階段(`_facts`)用 `sqlite3.connect` 開新連線直接查 `operations`、`attempts` 表這件事,乍看像跨層直呼,但這個做法在專案裡是既有慣例、不是 f4 自創:tests/executor/test_execution_e2e.py(同樣是端到端測試)在驗證階段也是 `sqlite3.connect(world.dsp_db)` 直接查表;tests/executor/test_crash_recovery.py、tests/executor/test_queue.py、tests/dsp/test_capability.py 等多支測試檔也都用同一手法在驗證階段開獨立連線讀內部表。f4 的 `stale_attempts`/`writes` 查詢跟這批測試同一個等級,不算引入新做法。

沒有發現跟既有兩支參照測試不同的第二種做法,也沒有看到跨層(例如測試直接呼 DSP 內部函式、或繞過 `Executor`/`flow.advance` 直接操資料庫寫入業務結果)的直呼。
