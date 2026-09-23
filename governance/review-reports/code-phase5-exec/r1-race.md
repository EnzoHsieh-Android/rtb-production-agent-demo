severity: major

## F1 S309 併發測試裡柵欄前失敗會讓另一方卡滿 10 秒、例外被執行緒吞掉,`outcomes` 少一筆卻看不出原因
severity: major
blocking: 是 — 這是專門用來「確定性重現版本衝突」的測試,若它自己在異常時序下會產生跟版本衝突無關、且原因被吞掉的假失敗,就達不到「S309 測試是否真的確定性地重現」這個审查目标要求的可信度,必須先补救(比如在 `run` 裡包 try/except 把例外塞進 `outcomes`,或把 barrier 逾時縮短並在逾時時給出明確訊息)才能信任這支測試的紅燈。
引句:「self.barrier.wait(10)」

`_RacingClient.write` 把柵欄放在「送出 HTTP 寫入之前」,這一點本身沒問題:兩邊的 `read_campaign`→`precheck`→`_sign`→`_take`(開始一筆)全部要成功走完才會呼叫到 `write()`,所以只要雙方都走到柵欄,兩邊的執行前檢查確實都已經通過(見 `src/rtb/executor/execution.py` 的 `_process`)。

但柵欄是雙方對稱的資源:只要有一邊在到達 `write()` 之前就失敗或逾時退出(例如真實 DSP 在慢機器/CI 過載下讓 `read_campaign` 的 HTTP 請求超過 `DspClient` 的 3 秒逾時、丟出 `DspUnavailable`,被 `_process` 接住後直接 `_release` 回 `Result.DEFERRED`,根本不會呼叫 `write()`),另一邊就會單獨卡在 `self.barrier.wait(10)`。`threading.Barrier` 在只有一方等待、逾時 10 秒後,會對「唯一還在等的那方」丟出 `BrokenBarrierError`。而 `run` 這個 closure(`tests/executor/test_version_conflict.py:172-173`,引句同段:`outcomes[name] = _one_writer(tmp_path, url, config, name, budget)`)沒有包 try/except,例外會在該執行緒裡直接往外拋——Python 的 `threading.Thread` 不會把子執行緒的未捕捉例外傳回主執行緒,只會印到 stderr,`outcomes[name]` 就永遠不會被寫入。主執行緒最後只看到 `assert len(outcomes) == 2, outcomes`(`tests/executor/test_version_conflict.py:187`)失敗、字典裡只有一筆,測試多跑了將近 10 秒,而真正的原因(逾時/DspUnavailable/BrokenBarrierError)完全不在斷言訊息裡,只能去翻 stderr。

我用一支獨立腳本(不改 repo,複製 `src`/`tests` 到 mktemp 目錄後跑)人工把其中一邊的 `DspClient` 逾時調到近乎 0 秒(模擬慢機器上讀 DSP 逾時、根本沒送到 `write()`)、另一邊維持正常 3 秒逾時,實測結果:
```
elapsed=10.02 outcomes= {'a': ('ok', <Result.DEFERRED: 'deferred'>), 'b': ('EXC', 'BrokenBarrierError()')}
```
證實了「柵欄前失敗會讓另一邊卡滿 barrier 逾時、例外被吞、`outcomes` 少一筆」這條路徑真實存在,不是臆測。

在這份測試目前寫死的合法輸入(`campaign_version_observed=1` 對上真實 DSP 種子版本 1、預算 150/160 都在 `max_budget=1000` 之內、`decision_expires_at` 給了 10 分鐘)下,雙方在正常機器上不會走到這條路徑——我把 `test_two_concurrent_writers_reproduce_a_version_conflict` 連跑 30 次(各自獨立行程)與另外用同一顆行程內直接呼叫 `_one_writer` 連跑 40 次,全數通過,耗時都在 10–15 毫秒等級,沒有一次偶發失敗,佐證「①在目前輸入下確定性重現版本衝突」這一半成立。真正的風險只在「兩套系統用真實時間」這個設計本身:一旦 CI/真實環境比預期更慢、讓某一邊在到達柵欄前就先因逾時鬆手,測試不會乾脆地紅,而是拖 10 秒才給一個看不出原因的斷言失敗,診斷成本高、也容易被誤判成「這支測試不穩,先 skip」。

## 已看:S309 柵欄是否放對位置、執行前檢查是否雙方都真的過了
`Executor._process`(`src/rtb/executor/execution.py`)依序做 `read_campaign` → 到期判斷 → `precheck`(讀 DSP 現況、版本比對)→ `_sign` → `_take`(在 `InboxStore.transaction()` 的交易裡呼叫 `attempt_store.begin` 開始一筆)才輪到 `self.dsp.write(...)`;`_RacingClient.write` 把柵欄放在覆寫的 `write()` 最前面,所以能到柵欄的兩邊都已經各自通過執行前檢查、也各自成功開了一筆嘗試——柵欄位置正確,不是「檢查前」誤放。兩套系統(`a.db`/`b.db`)各自獨立的 `InboxStore`,彼此之間唯一共用資源是同一個真實 DSP(`CampaignStore` 用 `begin_immediate` 序列化寫入,`BUSY_TIMEOUT_SECONDS=5.0` 遠大於本機同時寫入的實際耗時),符合「DSP 只接受一方」這個假設的機制基礎。

## 已看:類別層級的 `_RacingClient.barrier` 是否跨測試殘留
`barrier` 是類別屬性,但每次測試函式最前面都重新指派一個新的 `threading.Barrier(2)`(`tests/executor/test_version_conflict.py:180`),舊物件沒有被保留參照。用同一支腳本在單一 Python 行程內連續呼叫 40 次(重用同一個類別、每次覆寫 `barrier`),沒有出現任何殘留導致的異常或計數對不上,40/40 全過。目前 repo 裡也沒有其他測試同時使用 `_RacingClient`,單一 pytest 行程內沒有殘留風險;若未來換成 pytest-xdist 之外的「同行程平行跑測試」框架才需要重新檢查,目前寫法在這份 diff 的執行方式下沒有具體可重現的失敗場景,不標成 finding。

## 已看:② `execution.py` 開始一筆撞到既有失敗鍵,`begun.row.code` 是否跟讀到失敗狀態同一筆
`attempt_store.begin`(`src/rtb/executor/attempt_store.py:289-317`)撞到既有鍵時,是用單一次 `latest(tx, key)` 查詢把 `state` 與 `code` 一起讀出來(`_COLUMNS` 一次 SELECT 取整列,`src/rtb/executor/attempt_store.py:172-186` 的 `_row`),不是分兩次查詢,所以不存在「讀 state 時是失敗、讀 code 時已經被改掉」這種同一次呼叫內部的競態窗口。而外層呼叫 `begin` 的地方(`Executor._take`,`src/rtb/executor/execution.py` 的 `with self.store.transaction() as tx:`)一定包在 `InboxStore.transaction()` 裡,這支方法一律用 `immediate_transaction`(BEGIN IMMEDIATE)(`src/rtb/executor/inbox_store.py:332-342`);而全 repo 唯一能拿到 `ExecutorTransaction` 的入口就是這支 `transaction()`(`attempt_store.py:117` 的 issuer 憑證檢查擋掉了繞過),換句話說寫下失敗那一筆的另一個工作者也一定是透過同一種 BEGIN IMMEDIATE 交易寫入——SQLite 的 IMMEDIATE 鎖讓同一個資料庫檔案同時只會有一個這種交易在跑,不會有「另一個工作者正在寫失敗、這邊同時在讀」的重疊窗口。S309 測試沒有覆蓋「單一共用 store、兩個工作者搶同一把鍵」這個情境(它刻意用兩個獨立 store 模擬兩套系統),但這條路徑的安全性是靠 `immediate_transaction` 的序列化保證,不是靠這支新測試撐住;既有的 `tests/executor/test_multi_worker.py`(這份 diff沒有動它)已經在驗證同一個 store 下多工作者的鎖語意,程式碼讀下來沒有找到跟 `block_code_for_failure` 相關、會被繞過鎖的新路徑。

## 已看:③ `version_conflict_count` 在並行寫入時的一致性
測試裡呼叫它一律要先拿到 `tx: ExecutorTransaction`(`with h.store.transaction() as tx: attempt_store.version_conflict_count(tx)`),而如上一節所述,唯一能造出這個 `tx` 的地方就是 `InboxStore.transaction()`,一樣是 BEGIN IMMEDIATE。所以這支「唯讀」查詢實際上是在寫入鎖下面做的,跟任何並行寫入互斥,不會讀到跨列不一致的中間狀態(不像一般唯讀查詢可能在 WAL 下讀到快照但仍與正在提交的寫入交錯)。程式本身的 docstring 也寫明這是「事後查帳用」,沒有宣稱要支援高頻並行查詢;用 BEGIN IMMEDIATE 換一致性、犧牲一點吞吐量在這個用途下是合理的取捨,不是缺陷。

⚠ 交編排者:F1 的觸發條件(真實 DSP 讀取超過 3 秒逾時)在本機與一般 CI 環境下機率很低,我沒有在未動任何逾時/延遲參數的前提下讓原始測試自然觸發過(30+40 次全過);是否值得為了這個低機率但診斷成本高的路徑要求補 try/except,還是接受「萬一真的卡 10 秒、去翻 stderr」的現狀,這是团队對测试可维护性的取舍,不是我能單方面替團隊拍板的地方,留給編排者定奪。

最高 major,blocking 1 條。
