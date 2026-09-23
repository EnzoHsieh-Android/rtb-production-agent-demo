severity: major

### 1. 「舊鍵寫下多久」的新鮮判斷只擋得住短暫延遲,DSP 呼叫一旦拖過一個 VISIBILITY_TIMEOUT,活著的對帳會被判成過期,讓 DSP 收到第二次寫入,違反自己標的合約

severity: major
blocking: 是 破壞合約(S134/S138 隱含要求的「兩個工作者之中最多一個能走到 DSP 寫入」),且已用真跑的實驗重現
引句:「receipt is None and row.written_at > now - VISIBILITY_TIMEOUT:」

說明(攻擊者鏡頭:能不能讓活著的對帳被判成過期):

`_reconcile` 對舊鍵(沒有收件表訊息、沒有收據)判斷「是不是還有人在做」完全只看 `row.written_at` 是不是比 `now - VISIBILITY_TIMEOUT`(60 秒)新,新就跳過、不然就當孤兒轉走再重送(`src/rtb/executor/execution.py:591-598`)。`recover_in_flight` 的重啟恢復用同一個公式(`src/rtb/executor/attempt_store.py:448,458`,`runner.py:97`)。但這 60 秒只是「猜」——`inbox_store.py:40` 自己註記「VISIBILITY_TIMEOUT = timedelta(seconds=60) # 暫用,沒有實測校準:一輪最慢是幾次 DSP 呼叫加本地寫入」,程式碼裡沒有任何地方保證「活著的工作者一次 DSP 呼叫一定在這之前寫完結果」這句設計文件裡的假設成立。

**輸入 → 預期 → 實際**:
- 輸入:一把舊鍵(無收件表訊息、無收據),工作者 A 對帳它,寫下嘗試中(`written_at=T0`)後呼叫 DSP 寫入,這次呼叫因為 DSP 慢(或本機忙碌重試疊加)真的拖超過 60 秒還沒回應;在 A 還卡在 DSP 呼叫途中,時鐘走到 `T0+61s` 時工作者 B 也對帳同一把鍵。
- 預期(依 [S134] 的合約字面義:「DSP 最多收到 1 次寫入」,`tests/executor/test_multi_worker.py:259` 也是這樣斷言 `len(h.dsp.writes) <= 1`):DSP 全程只收到 1 次寫入呼叫。
- 實際(用專案自己的假 DSP 與收件表在臨時目錄實測,PYTHONPATH=src 直接呼叫 `Executor.reconcile_all`,不動 repo):B 把 A 那筆判成「寫下超過一個租約時間」的孤兒,轉成結果不明後自己重送,DSP **真的收到第二次寫入呼叫**(`len(h.dsp.writes) == 2`);A 醒來後它那筆已經序號對不上,乾淨走 `LeaseLost`(不停機),而且因為假 DSP 對同鍵同內容重送有做冪等比對,最終業務上只套用一次(`len(h.dsp.operations) == 1`,版本只加一次)。也就是說**資料沒壞、系統沒停機,但「DSP 最多收到 1 次寫入」這條這批改動自己聲稱要保的規則被打破了**——而且完全沒有測試守著這條路徑:現有的 `test_two_workers_reconciling_a_legacy_key_never_halt_and_write_once`(S134)測的是「兩邊都從 UNKNOWN 起跑,搶著寫第一筆嘗試中」那個由資料庫 CAS 保護的競爭點(`meet_before(monkeypatch, "_write")`,兩邊卡在寫嘗試中「之前」,輸家連 `dsp.write` 都叫不到),跟這裡「鍵已經是 IN_FLIGHT、A 已經在呼叫 DSP 途中被 B 用『寫下多久』判定放棄」是完全不同的競爭點,S138 新加的兩支測試(`test_a_restarting_worker_leaves_a_live_legacy_reconciliation_alone`、`test_reconciliation_recovers_a_stale_legacy_in_flight_attempt`)也都沒有覆蓋「A 卡在 DSP 呼叫途中、時鐘同時走過 60 秒」這個組合。

**能不能被攻擊者利用**:預設 `--dsp-timeout-seconds=5.0`(`runner.py:52`)而且 `src/rtb/httpclient.py` 的 `_read_capped` 有把單次請求的總耗時上限做到位(deadline 是在 `open()` 之前先算好),所以光靠對方(DSP)拖回應時間,在預設設定下無法把單次呼叫撐過 5 秒左右,單靠這點還撐不到 60 秒——這點是好消息。但這條「60 秒安全邊際」完全是靠設定巧合撐住,程式碼裡**沒有任何地方驗證或強制「`--dsp-timeout-seconds` 要遠小於 `VISIBILITY_TIMEOUT`」**;只要維運把逾時調大(這是公開、有預設值但無上限檢查的 CLI 參數)、或本機在忙碌時疊了幾輪 S135 的「連續 3 輪才停機」重試與排隊(同樣沒有量過真實耗時,見 `runner.py` 的 `BUSY_LIMIT`/`interval`),單一舊鍵嘗試的實際耗時就可能被推過 60 秒,這條保護就會在正常維運下(不需要遠端攻擊者控制回應內容)失效,而且失效時沒有任何測試或告警會發現。

file: `src/rtb/executor/execution.py:591`
file: `src/rtb/executor/attempt_store.py:458`
file: `src/rtb/executor/inbox_store.py:40`
file: `tests/executor/test_multi_worker.py:259`
