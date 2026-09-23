severity: clean

## 席位與做法

席位:租約只增不改表(3b 的租約紀錄表)的正確性。立場:假設這個做法在某個輸入或時序下會壞,
找那個輸入;找不到才承認它對。

不只讀,實際在臨時目錄照 spec 3b 節字面寫了一個最小可行實作並跑真測試(包括真的多執行緒競爭
與真的行程 os._exit 猝死),詳見下方「實際驗證」。過程:

```
mktemp -d                                    # 得到臨時目錄(不動 repo)
cp -r /Users/enzo/rtb-3b/src   $TMPDIR/src
cp -r /Users/enzo/rtb-3b/tests $TMPDIR/tests
cp /Users/enzo/rtb-3b/pyproject.toml $TMPDIR/pyproject.toml
```

先跑基準(未加任何租約程式碼),確認複製正確、既有 tests/analyzer/ 全綠:

```
$ cd $TMPDIR && PYTHONPATH=$TMPDIR/src .venv/bin/python -m pytest tests/analyzer/ -q
........................................................................ [ 70%]
..............................                                           [100%]
102 passed in 1.17s
```

## 最小實作(照 spec 3b 節字面,寫進 `src/rtb/analyzer/task_store.py`,理由見下)

- 新增 `lease_records` 表,欄位與主鍵照字面:`task_id, lease_seq, owner, expires_at`,
  `PRIMARY KEY (task_id, lease_seq)`;只用 `INSERT`,沒有 `UPDATE`/`DELETE`。
- `acquire_lease(task_id, owner, now, lease_length)`:在 `immediate_transaction` 裡讀「目前那一列
  (lease_seq 最大的)」,`owner` 非空且 `expires_at > now` 才算被持有;沒被持有就插入一列取得列、
  回傳收據;被持有就什麼都不寫、回傳 `None`。
- `release_lease(receipt, now)`:重讀目前那一列,只有還是收據那一列(序號與擁有者都對)才插入
  一列放掉列(擁有者空、到期時間=now);已被接手就不寫。
- `commit_step(..., receipt=None)`:在既有的寫入交易裡、核對任務序號之前先核對租約——帶收據時
  只比對「目前那一列是不是這張收據的取得列」(不看到期時間,照字面「核對的是號碼,不是時間」);
  不帶收據時,「目前有人持有有效租約」就回沒有寫入,沒人持有就照舊只核對序號。寫入成功且帶收據
  時,同一個交易裡插入放掉列。

把它放進 `task_store.py`(不是另開檔案)是照字面「所以在同一個資料庫檔、同一個任務模組裡開一張
只增不改的租約紀錄表」與「或在同一個模組開一張會被改的表,都會踩到它」這兩句的最自然讀法——
「任務模組」在這兩句裡指的是 task_store.py 本身(S41 那支測試也只掃 `task_store` 這一個模組的
原始碼,見 `file: /Users/enzo/rtb-3b/tests/analyzer/test_flow.py:404-409`,不是掃整個 analyzer
套件),所以我讓新程式碼落在會被 S41 掃到的地方,而不是找一個掃不到的旁支模組討巧。

## 已讀,無 finding 的子節

- 事故描述、使用者情境題(2026-09-23)、PRIOR-ART、RETIRE-IF、狀態(設計中):讀過,跟後面的
  機制設計一致,無 finding。
- 為什麼租約不照抄收件表的「改欄位」:S41 的守衛範圍verified(見上),claim 成立。
- 租約長度與外部呼叫逾時、保證什麼不保證什麼、怎麼測、事故 F3 轉正、使用者裁定、不做的事:
  不在「租約表本身的正確性」這一席範圍內(分別是效能參數、承諾範圍措辭、測試流程、治理流程、
  範圍裁定),讀過無 finding。
- 全文「這份計劃在解決什麼」事故 F3 句(已補「分析費用這一半只擋得住同時進行的重複…見增量
  3b〈保證什麼、不保證什麼〉」)與「## 落點」lands_in(增量 3b)行:跟 3b 正文的機制與家的裁定
  一致,無 finding。
- 「## 回退」最後一句「增量 3b 的回退見該節(拿掉分析側租約,租約紀錄表留著不影響)」:驗證見
  下方「回退可行性」,claim 成立。

## 實際驗證(逐條對應 spec 3b 節的字面宣稱)

跑的測試檔:`$TMPDIR/tests/analyzer/test_task_lease_findings.py`(自己寫,7 個情境)+
`$TMPDIR/scratch_crash_test.py`(自己寫,真行程猝死)。全部指令都用
`/Users/enzo/rtb-production-agent-demo/.venv/bin/python`,`PYTHONPATH=$TMPDIR/src`。

1. **兩條執行緒(實際是 20 條)同時取得,只有一個贏**(對應「目前沒人持有才新增一列取得列」
   與「主鍵本身也是一道防線」):
   ```
   test_two_real_threads_racing_to_acquire_only_one_wins PASSED
   ```
   20 條執行緒用 `Barrier` 逼真的同時出發,20 次都正常回報(沒有例外被吃掉)、恰好 1 個拿到收據、
   `lease_records` 表最終只有 1 列——沒有主鍵衝突的殘影。`BEGIN IMMEDIATE` 把整個資料庫的寫入
   序列化,所以「兩個交易算出同一個下一號」這件事在這支程式碼路徑下沒有實際發生過。

2. **過期接手後,舊持有者提交寫不進去,即使它接的任務序號仍是最新一列**(S152 字面):
   ```
   test_expired_holder_cannot_commit_after_a_takeover_even_when_sequence_still_matches PASSED
   ```
   A 取得租約(seq=1)、過期後 B 接手(seq=2,此時任務表還沒被任何人寫過,序號檢查會過)、
   A 拿舊收據 `commit_step` 回 `False`,任務表沒被 A 寫入。租約檢查獨立於任務序號檢查、且先
   於它執行,跟設計「先核對租約」的順序相符。

3. **放掉列之後立即可以被別人取得,不必等到期**(對應「有這種列,下一次呼叫才不必等租約到期」):
   ```
   test_release_then_immediately_reacquire_succeeds PASSED
   ```
   同一個 `now`,A 放掉、B 立刻取得成功(lease_seq 多跳一號給放掉列)。

4. **主鍵衝突時的行為**(繞過 `acquire_lease` 正常路徑,直接對同一組主鍵塞第二列):
   ```
   test_manually_forced_primary_key_collision_raises_not_silently_ignored PASSED
   ```
   丟出 `sqlite3.IntegrityError`(訊息含 UNIQUE constraint),不是靜默失敗或覆蓋。這條路徑在
   `acquire_lease` 正常呼叫下不會被觸發(見第 1 點),但值得記一筆:spec 沒有寫「如果真的觸發了
   這道防線,呼叫端該怎麼收」——因為第 1 點已證明正常使用下它不可達,不構成可指出具體失敗場景的
   finding,只留在這裡當佐證,不升級。

5. **過期但沒人接手,持有者仍寫得進去(核對號碼、不核對時間)**(S158 字面):
   ```
   test_an_expired_holder_that_nobody_took_over_still_commits PASSED
   ```
   `now` 撥到遠超過租約長度、但沒有第二個 `acquire_lease` 發生,原持有者仍能提交成功。

6. **不帶收據的呼叫端:別人持有就擋、沒人持有就照舊只核對序號**(S156 字面):
   ```
   test_commit_without_receipt_is_blocked_by_a_live_holder_but_not_by_nobody PASSED
   ```

7. **兩條真執行緒同時搶、只有贏家真的「花錢」**(對應 S150 的精神,用一個計數器模擬花錢動作):
   ```
   test_two_real_threads_only_one_pays_for_the_external_call PASSED
   ```
   只有 1 次模擬花費、任務表只推進 1 步。

8. **真的行程猝死(os._exit,不是丟例外)**:子行程取得租約、印出「已花錢」的訊號、
   `os._exit(9)` ——不經過任何 Python 例外處理,不會走設計第 7 步的放掉租約:
   ```
   dying subprocess exit code: 9
   dying subprocess stdout: ACQUIRED / PAID
   lease rows after crash: [(1, 'dying-owner', '2026-09-23T12:00:05.000000Z')]
   acquire while still valid -> None
   acquire after expiry -> LeaseReceipt(task_id='t1', lease_seq=2, owner='second-owner')
   commit after re-acquire -> True
   dead owner late commit -> False
   ALL OK
   ```
   租約表在硬終止後結構乾淨(只多一列取得列,沒有半寫的列);到期前搶不到,到期後新呼叫端能
   重新取得、重新「花錢」、成功提交——正是設計自己承認的「至少一次的代價」,測起來跟文字一致;
   死掉的舊持有者事後想用舊收據補提交也寫不進去。

9. **回退可行性**:把上面加的方法整組移除、`commit_step` 的呼叫端全部不傳 `receipt`,回到 Phase 2
   的行為——這正是基準跑的那 102 支既有測試(第一段指令)本來就在驗證的東西,而且是在**完全沒有
   `lease_records` 表存在**的情況下綠的;加了表之後再跑同一批(見下)結果不變,證明「表留著不影響」
   這句話成立,不需要另外驗證「移除程式碼後」的狀態。

## 既有 tests/analyzer/ 全套(加了租約程式碼與我自己的測試檔之後)

```
$ PYTHONPATH=$TMPDIR/src .venv/bin/python -m pytest tests/analyzer/ -q
........................................................................ [ 66%]
.....................................                                    [100%]
109 passed in 1.27s
```

點名檢查(指令要求的那幾支):
```
$ PYTHONPATH=$TMPDIR/src .venv/bin/python -m pytest tests/analyzer/test_flow.py -v \
  -k "s41 or history_table or update_or_delete or many_real_threads or crash_before_commit or two_concurrent"
test_two_concurrent_advance_calls_on_the_same_task_never_both_commit_conflicting_outcomes PASSED
test_many_real_threads_racing_advance_on_the_same_task_commit_exactly_once PASSED   # 二十條執行緒那支
test_a_crash_before_commit_while_leaving_received_loses_nothing PASSED
test_a_crash_before_commit_while_collecting_evidence_loses_nothing PASSED
test_a_crash_before_commit_while_analyzing_loses_nothing PASSED
test_a_crash_before_commit_while_proposed_loses_nothing PASSED                      # 提交前當機
test_the_history_table_has_no_update_or_delete_statements PASSED                    # S41
test_the_update_or_delete_guard_actually_catches_a_planted_statement PASSED
8 passed in 0.08s
```

## S157(舊資料庫自動多出租約表)額外驗證

用**未改過的原版** `task_store.py`(`/Users/enzo/rtb-3b/src`,沒有租約程式碼)先建一個資料庫、寫一
筆任務,再用臨時目錄裡**加了租約程式碼**的版本重新開啟同一個檔案:

```
old db created with original (unpatched) task_store.py
history of old task still there: TaskRow(task_id='old-task', seq=1, state=<TaskState.RECEIVED...>, ...)
lease acquired on old db after auto-migration: LeaseReceipt(task_id='old-task', lease_seq=1, owner='newowner')
```

`CREATE TABLE IF NOT EXISTS` 自動補表、既有歷史列不動、補完表立刻可用,跟 S157 與「不需要增量 1
那種重建表」的字面一致。

## 結論

在臨時目錄照 spec 3b 節字面實作並跑了並行(20 執行緒搶同一把租約、2 執行緒同時「花錢」)、
過期接手、放掉後重取、主鍵衝突、核對號碼不核對時間、真行程猝死、舊資料庫自動遷移共 8 類情境,
外加既有 tests/analyzer/ 全套(102 支基準 + 8 支點名檢查),全部符合 spec 3b 節對租約表的字面
宣稱,沒有找到會壞的輸入或時序。這一席判定:clean。

最嚴重 severity:clean;blocking 0 條。
