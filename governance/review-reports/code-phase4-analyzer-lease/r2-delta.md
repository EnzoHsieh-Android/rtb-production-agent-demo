severity: minor

## F1 release_lease_quietly 只有「吞 DatabaseBusy 不蓋掉原例外」的測試,沒有「非資料庫例外不該被吞」的反向測試
severity: minor
blocking: 否 — 現有程式碼本身沒有錯(手動核對 except 子句範圍正確,只列 `sqlite3.Error, DatabaseBusy`),純粹是測試覆蓋缺口,不是這輪 diff 引入的錯誤行為;不翻紅任何現有測試。
引句:「except (sqlite3.Error, DatabaseBusy):
            return」
file: `/Users/enzo/rtb-3b/src/rtb/analyzer/task_store.py:324-330`(`release_lease_quietly`,本輪新加的函式,把上一輪 flow.py 裡的 `_release_keeping_the_original_error` 搬過來)。唯一相關測試 `test_a_failed_release_never_masks_the_original_error`(`/Users/enzo/rtb-3b/tests/analyzer/test_task_lease.py:367-378`)monkeypatch `release_lease` 只丟 `DatabaseBusy`,只驗了「該吞的有吞、不蓋掉原例外」這一半;沒有任何測試驗「不該吞的沒被吞」(例如 `release_lease` 因程式錯誤丟出 `AttributeError`/`TypeError` 之類非資料庫例外,要老實蓋過並往外傳)。

在臨時目錄複製 repo(`mktemp -d`,不動 repo 本身)把 `release_lease_quietly` 的 `except (sqlite3.Error, DatabaseBusy):` 改寬成 `except Exception:`(模擬未來回歸——吞的範圍被誤放寬,程式錯誤也被吞掉、原例外被蓋掉不再往外傳)後重跑 `tests/analyzer/test_task_lease.py`,17 個測試全數 `passed`,沒有任何一個翻紅:

```
$ diff <(sed -n '325,330p' orig) <(sed -n '325,330p' mutated)
-        except (sqlite3.Error, DatabaseBusy):
+        except Exception:
$ PYTHONPATH=<tmp>/src pytest -p no:cacheprovider tests/analyzer/test_task_lease.py -q
17 passed in 0.16s
```
（完整重現腳本已在 `mktemp -d` 臨時目錄跑過,repo 本身未改動。）建議補一條:monkeypatch `release_lease` 丟一個非 `sqlite3.Error`/`DatabaseBusy` 的例外(如 `AttributeError`),斷言它從 `advance()` 原樣往外傳、且不是被 `release_lease_quietly` 吞掉後又冒出別的東西。

---

## 已查證、判定不是漏洞(附實驗,供對照)

以下是題目指名要查的三個角落,實際做了實驗驗證,結論是「乾淨」,不算 finding,但寫在這裡交代查證過程與依據(引句仍逐字出自 r2-snapshot.patch)。

**① `acquire_lease` 的 TaskNotFound 查詢與交易/呼叫順序**:「+            if self._conn.execute(
+                    "SELECT 1 FROM tasks WHERE task_id = ? LIMIT 1", (task_id,)).fetchone() is None:
+                raise TaskNotFound(task_id)」這段落在 `with immediate_transaction(self._conn):` 區塊內(`/Users/enzo/rtb-3b/src/rtb/analyzer/task_store.py:300-303`),而 `immediate_transaction`(`/Users/enzo/rtb-3b/src/rtb/sqlitekit.py` 的 `except BaseException: ... ROLLBACK; raise`)保證任何例外都回滾,測試 `test_acquiring_a_lease_for_an_unknown_task_writes_nothing` 也驗了沒寫進 `task_leases`。呼叫順序上,`advance()` 先呼叫 `store.latest(task_id)`、`row is None` 就直接 `raise TaskNotFound(task_id)`(這行在呼叫 `acquire_lease` 之前),而 `tasks` 表只增不改(SCHEMA 註解與 S157 測試都驗過),沒有任何刪除路徑,所以走 `advance()` 這條路時 `acquire_lease` 內部那個存在性檢查永遠不會為真——不會在「不該丟」的時候丟,只是給直接呼叫 `acquire_lease`(不經 `advance()`)的呼叫端多一層保護,現有測試也是這樣直接呼叫著測的。

**③ 擁有者字串在行程重啟、編號重用下能不能騙過圍籬**:在臨時目錄跑了一段腳本,故意讓兩次 `acquire_lease` 對同一個任務用完全相同的 owner 字串(模擬 pid 重用+執行緒識別重用+`itertools.count()` 歸零的最壞情況):
```
receipt_a = store.acquire_lease("t1", SAME_OWNER, NOW)          # lease_seq=1
receipt_b = store.acquire_lease("t1", SAME_OWNER, AFTER_EXPIRY) # lease_seq=2,owner 與 a 相同
store._holds(receipt_a) -> False   # 舊收據不再被認為持有
store._holds(receipt_b) -> True
store.commit_step("t1", 1, ..., lease=receipt_a) -> False  # 拿舊收據送出也擋下來
```
三個斷言全部成立。原因是圍籬比對的是「+        return current is not None and current[:2] == (lease.lease_seq, lease.owner)」——序號跟擁有者兩個都要對得上,而 `lease_seq` 是同一個任務內在寫入交易裡嚴格遞增、由資料庫序列化保證不重複的欄位,不是由 owner 字串推導出來的;owner 字串只是附加在收據上的稽核資訊(供 `test_a_lease_owner_names_the_process_and_thread` 核對「看得出是哪個行程」)。所以即使 owner 真的重複,拿到重複 owner 的兩張收據仍然是不同的 `lease_seq`,圍籬用序號就能正確分辨新舊,騙不過去。額外驗了 `itertools.count()` 本身:8 條執行緒各呼叫 `next()` 20000 次共 16 萬次,結果無重複值,CPython 下這個計數器本身是執行緒安全的(不需要額外鎖)。

**② `release_lease_quietly` 的呼叫時機與行為**:讀 `_advance_holding` 與 `advance()` 的例外路徑,`release_lease_quietly` 只會在 `advance()` 外層 `except BaseException` 被呼叫一次(`_advance_holding` 內部 `if not committed: store.release_lease(lease, now)` 那次若丟例外,因為寫入交易本身在丟例外時已回滾,不會有半途狀態,外層再呼叫一次 `release_lease_quietly` 是安全的重試,不會造成重複放掉或資料不一致)。這部分沒查到問題;吞的範圍過寬這件事已經拆成 F1 單獨列出。

最高 severity: minor;blocking 條數:0。
