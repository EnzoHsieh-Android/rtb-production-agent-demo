severity: major
# 審查報告

severity: major

### 1. 窗界「含起點、不含終點」在起訖剛好落在邊界時完全沒有測試覆蓋,邊界迴歸不會翻紅
severity: major
blocking: 是
引句:「窗內統計:起、迄(含起點、不含終點),迄減起不得超過 24 小時。」

觸發情境:`_Window.inside()`(`src/rtb/ops/metrics.py:422-423`,現讀 `return self.since <= at < self.until`)以及所有窗口讀取函式的 SQL(如 `src/rtb/executor/inbox_store.py:1567-1573` 的 `lifecycle_events_between_query`,現讀 `WHERE at >= ? AND at < ?`)都宣稱「含起點、不含終點」。但 `tests/ops/test_metrics.py` 全篇(含 `rows.py` 造的每一筆資料)沒有任何一筆事件、呼叫或嘗試列的時間剛好等於 `W_START`(`at(0)`)或 `W_END`(`at(minutes=60)`,見該檔第 19 行);所有測試資料都刻意避開邊界值,只測「窗內」與「窗外」,不測「剛好在邊界上」。

我在 `/tmp/rtb-mut`(repo 副本,未動 `/Users/enzo/rtb-p9i2`)分別做了兩個獨立變異並各自重跑:
- 把 `inside()` 改成 `since <= at <= until`(終點也算窗內):`pytest tests/ops/test_metrics.py` 18 個測試全過。
- 改回原檔後,把 `inside()` 改成 `since < at < until`(起點不算窗內):同樣 18 個測試全過。
- 把 `lifecycle_events_between_query` 的 SQL 從 `at < ?` 改成 `at <= ?`(終點也含入 SQL 層):不只 `tests/ops/test_metrics.py`,連同 `tests/ops/test_window_readers.py`、`tests/executor` 整個目錄共 609 個測試也全部通過。

也就是說,不論是 Python 層的 `inside()` 判斷、還是 SQL 層的半開區間,只要日後有人不小心把 `<` 打成 `<=`(或反過來),現有測試組完全偵測不到——即使這是規格明確寫死的核心語意(增量 2 幾乎每個窗內統計指標都靠它歸窗),而且以後增量 3 的服務水準指標(佇列等待「剛好第 30 秒算好」、對帳「剛好第 10 分鐘算好」)本身就是靠這種邊界定義,若增量 2 的窗界本身沒被守住,後續建在它上面的東西也測不出來。

建議修法:在 `test_metrics.py`(或專門的邊界測試)補一組「事件時間精確等於 `since`」與「精確等於 `until`」的資料點,斷言前者被算入、後者被排除在外(至少對 `terminal_event_rate`/`queue_wait_seconds` 這類直接依賴 `.inside()` 與 SQL 窗口讀取的指標各補一組)。

### 2. 端到端延遲的「鏈尾修訂檢查」被拿掉也不會讓 [S641] 的綁定測試變紅
severity: major
blocking: 是
引句:「if task != chain.last or revision != w.part.task_revisions.get(task, revision):」

觸發情境:`_end_to_end()`(`src/rtb/ops/metrics.py:636`)判斷一個終點事件是否可以當作某條接續鏈的「終點候選」時,用的是兩個條件的「或」:`task != chain.last`(這個任務是不是鏈尾)`或` `revision != 最新修訂`。[S641] 的綁定測試 `test_end_to_end_latency_follows_the_follow_up_chain_once` 造的資料裡,鏈上非鏈尾的事件時間永遠比真正鏈尾的事件時間早(`r1` 在第 5 分鐘被擋、`f1` 在第 8 分鐘被擋、鏈尾 `f2` 在第 12 分鐘才交給執行,注解自己也寫「不是鏈尾」)。

我在 `/tmp/rtb-mut` 把該行改成只留修訂檢查(拿掉 `task != chain.last or`),重跑 `pytest tests/ops/test_metrics.py`:18 個測試依舊全過,包含這支專門守 [S641] 的測試。原因是 `_end_to_end()` 用「同一個鏈根裡挑 `.at` 最新的那個當結果」來收斂多個候選(`ends.get(chain.root)` 那段),測試資料裡鏈尾事件剛好也是時間最晚的一筆,所以就算把「是不是鏈尾」這道明確檢查拿掉,靠「挑最新」這個副作用也能矇混出同一個答案。真正會讓這道檢查產生差異的情境——鏈上某個已被取代的舊任務,其終點事件時間反而比真正鏈尾晚(例如舊修訂先被擋下、鏈尾任務較晚才建立但較快結案這種時鐘不同步或非線性接續的情況)——完全沒有被造出來過。

建議修法:在該測試裡追加一條「非鏈尾任務的終點事件時間晚於真正鏈尾」的資料(例如讓 `r1` 或 `f1` 的擋下事件時間晚於 `f2` 的交給執行時間),斷言取出的 `end_to_end_seconds` 樣本仍然只用 `f2` 的時間、且只有一筆,逼「挑最新」這個捷徑失效、只剩明確的鏈尾檢查能守住。

### 3. 延遲範例「耗時相同時取結束時間最新」的並列規則沒有任何測試資料驗證
severity: minor
blocking: 否
引句:「比率與計數取分子裡時間最新的;延遲取最慢的(耗時相同取結束時間最新的)。同一任務只列一次。」

`_exemplars()`(`src/rtb/ops/metrics.py` 的 `slowest=True` 分支)明確定義了耗時打平時的排序規則(依 `(value, at)` 反序,同值時比 `at`),但 `tests/ops/test_metrics.py` 裡唯一測範例挑選的 `test_metric_exemplars_are_real_members_of_the_sample`,五筆佇列等待時間是 60、10、50、2、32 秒——彼此互不相等,完全不會觸發「同耗時比較 `at`」這條分支。若之後有人把 tie-break 誤改(例如漏比 `at`、或方向反了),沒有任何測試能發現。

建議修法:在該測試(或新增一支)裡造兩筆耗時完全相同、但結束時間不同的佇列等待樣本,斷言範例挑的是結束時間較晚的那一筆。
