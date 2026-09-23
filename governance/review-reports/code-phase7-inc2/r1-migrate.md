severity: clean

### 已看:補名稱欄位的遷移(`_migrate_columns`,`src/rtb/dsp/store.py:114-147`)

- 「全部都已補好就直接返回」的快速判斷 `if {"tenant", "name"} <= columns and {"policy_version", "expected_version"} <= op_columns: return` 有把 `name` 算進去(第 124 行),不是只查 `tenant`。
- 拿到寫入鎖(`begin_immediate`)之後,`columns = {row[1] for row in self._conn.execute("PRAGMA table_info(campaigns)")}` 會重新查一次(第 131 行),`tenant` 與 `name` 分別各自 `if "x" not in columns` 判斷是否要下 `ALTER TABLE`,不是共用同一個布林值,所以「有 tenant 沒 name」「沒 tenant 也沒 name」「全都有」三種舊資料庫狀態都能各自被正確判斷、不會誤補或漏補。
- 用 `threading.Barrier` 讓 8 條連線幾乎同時打開同一個舊資料庫,分三種初始狀態(無 tenant 無 name、有 tenant 無 name、全都有)各跑一次:三種狀態下所有能開啟的連線讀到的 `get_campaign` 與 `tenant_of` 結果全部一致且正確(欄位補齊、舊值保留、新欄位補空字串),沒有出現「重複 ALTER TABLE」或任何非預期例外。唯一一次例外是「全都有」情境裡 8 條連線同時觸發 `connect()` 的 `PRAGMA journal_mode=WAL`(第一次切 WAL 需要獨佔鎖)撞出 1 次 `StoreBusy`,這是 `sqlitekit.connect()` 既有、本次 diff 未觸及的行為,`StoreBusy` 是型別化、可重試的例外,不是新增風險。
- 遷移中途被殺掉的情境:在子行程裡把 `拿到鎖(BEGIN IMMEDIATE) → 睡 5 秒(此時 ALTER 與 COMMIT 都還沒下)` 之間送 `SIGKILL`(確認 `exitcode=-9`,真的殺在交易中途),之後用一條乾淨連線重新打開同一個資料庫:成功開啟、`name`/`tenant` 欄位都在、舊資料完整(`Campaign(id='old', budget=77, status='paused', version=4, name='')`),沒有殘留鎖定或半套 schema。這符合 SQLite WAL 的當機復原語意,也符合圖譜 Mock-DSP.md 既有 RULE(「儲存層建構子補欄位失敗時要關掉剛開的連線」,`[test:test_a_failed_column_migration_closes_the_new_connection]`)描述的設計,不是這次 diff 新引入的行為,本次只是把同一套已驗證過的補欄位模式套用到多一個 `name` 欄位。

重現指令與完整輸出:
```
PYTHONPATH=/Users/enzo/rtb-3b/src /Users/enzo/rtb-production-agent-demo/.venv/bin/python exp_migrate.py
```
(腳本在臨時目錄 `/var/folders/tc/xmllmxtn4q5704lsy80wc1kw0000gn/T/tmp.ExIc1vxVhL/exp_migrate.py`,不動 repo 任何檔)節錄:
```
=== 情境:無tenant無name(tenant=False, name=False), 8 條連線同時開 ===
成功開啟連線數:8 / 8,錯誤數:0
每條連線看到的 get_campaign 一致且正確:True(預期 name='' tenant='t-default'; 不一致筆數 0/8)

=== 情境:有tenant無name(tenant=True, name=False), 8 條連線同時開 ===
成功開啟連線數:8 / 8,錯誤數:0
每條連線看到的 get_campaign 一致且正確:True(預期 name='' tenant='t-acme'; 不一致筆數 0/8)

=== 情境:全都有(tenant=True, name=True), 8 條連線同時開 ===
成功開啟連線數:7 / 8,錯誤數:1(StoreBusy,來自 connect() 切 WAL 的鎖競爭,非 _migrate_columns)
每條連線看到的 get_campaign 一致且正確:True(預期 name='legacy-name' tenant='t-acme'; 不一致筆數 0/7)

=== 遷移中途被殺掉,會不會留下半套 ===
子行程已進入交易(拿到鎖):True
子行程結束,exitcode=-9
殺掉之後重新開啟成功:Campaign(id='old', budget=77, status='paused', version=4, name='')
欄位:{'budget', 'tenant', 'version', 'name', 'status', 'id'}
```

### 已看:`seed_campaign` 驗證名稱的時機(`src/rtb/dsp/store.py:152-164`)

`_check_campaign_name(name)` 在 `self._conn.execute("INSERT INTO campaigns ...")` 之前呼叫,而且驗證是純 Python(不碰資料庫),`ValidationRejected` 一定在任何寫入之前丟出。用 6 種壞名稱(超長、孤立代理字元、int、None、float、list)實測:全部拒收且拒收後 `campaigns` 表列數維持 0。另外用 barrier 讓兩條連線同時對同一個尚未建檔的 `id` 分別送「壞名稱」與「好名稱」:壞名稱那條在 Python 層就被拒收(不會碰到 SQL 層的 `IntegrityError` 之類的競態問題),好名稱那條成功建檔且最終存活的資料就是好名稱那筆。

### 已看:執行側與分析端讀多了名稱的回應,併發寫入(改預算、暫停)同時進行時名稱是否始終不變

`UPDATE campaigns SET budget = ?, status = ?, version = ? WHERE id = ?`(`src/rtb/dsp/store.py:415` 附近,`_apply`)不含 `name` 欄位,`_next_state`(第 87-95 行)兩個分支都把 `campaign.name` 原封不動帶進新的 `Campaign`。實測:3 個寫入執行緒(各自連線)在 2 秒內對同一筆廣告輪流改預算、暫停,同時 3 個讀取執行緒不斷 `get_campaign`,總共讀了 71,936 次,0 次名稱跟建檔時的原始值不同,讀取端也沒有非預期例外(`StoreBusy` 之外的錯誤 0 筆)。

### 已看:圖譜鏡頭(分支 `/Users/enzo/rtb-3b/docs/rtb-production-agent-demo-knowledge/Systems/Mock-DSP.md`)

沒有跟本次 diff 對不上的地方。相關 RULE 都跟程式行為一致:
- 「廣告多一個名稱欄位…只在建檔時設定,沒有任何寫入端點能改…舊資料庫照補欄位做法補名稱,舊廣告為空字串」`[since:2026-09-23]`——與 diff 行為一致。
- 「儲存層建構子補欄位失敗時要關掉剛開的連線,等鎖逾時轉成自己模組的『忙碌』例外」`[since:2026-09-23]`——與 `CampaignStore.__init__` 的 `except DatabaseBusy` / `except BaseException: self._conn.close()` 一致,本次遷移中途被殺的實驗行為也沒有違反。
- LUMOS-IMPACT 附上的三格反查(受影響測試/共改夥伴/呼叫者)皆為 0,機械反查沒有額外線索,已按規矩如實記錄,不代表沒有相依。

總結:severity clean,0 條 blocking finding。
