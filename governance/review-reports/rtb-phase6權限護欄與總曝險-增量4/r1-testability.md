severity: major

鏡頭:可測性(S360–S367 逐條能不能真的寫出會翻紅的測試、拿掉實作會不會真的紅、有沒有條款寫的行為沒測試守或會假綠)。

已核對:`tests/executor/fakes.py` 的 `Harness`(含 `query`/`stops`/`attempts` 這類直接戳資料庫的手法)、`tests/executor/test_f7_end_to_end.py`、`tests/executor/test_aggregate_limit.py`(S330–S343 已用同一批手法落地)、`src/rtb/executor/inbox_store.py`、`src/rtb/executor/attempt_store.py`、`src/rtb/sqlitekit.py`。

## F1 S362 的綁定測試在增量 4 這次交付範圍內寫不出來、也跑不了

severity: major
blocking: 是 — 這條合約掛在「增量 4 設計:可觀測」的 S360–S367 清單裡、帶著跟其他七條同樣格式的 `[test:test_approval_counts_cover_waiting_and_applied]`,但落點寫明查詢三的程式碼要等增量 3 才存在,現況也核對過收件表目前的 `Disposition` 列舉沒有「待核可」這個成員(`src/rtb/executor/inbox_store.py:50-59`,資料庫層 CHECK 限制列舉值,連用原始 SQL 塞測資都會被擋)、核可使用表根本還沒建。也就是說,若照這份設計直接送進增量 4 的實作(代碼審通常要求 S360–S367 全部有真跑的綁定測試,參照增量 1/2/3 的慣例),S362 這一條要嘛整條留白讓代碼審卡住,要嘛被迫在增量 4 裡先造一個增量 3 都還沒定案的「待核可」資料形狀去湊測試——這正好撞上增量 3 第 2 版要改結構(核可使用表帶租戶欄、停下紀錄加「比例過大」種類)的警告,湊出來的測試很可能在增量 3 落地時要整支重寫甚至證明是假的。

引句:「已核可放行數應等於核可使用表中符合篩選的列數(等增量 3 實作)」

引句:「查詢三跟增量 3 的核可模組同一篇(增量 3 開的家)」

這兩句放在一起讀,等於設計自己也知道查詢三現在生不出程式、也生不出測試,但合約清單(S360–S367)沒有把它跟其餘七條分開標成「本增量不交付、留待增量 3 補測試」,而是用跟其他條一模一樣的 `[S36x] ... [test:...]` 句式並排,容易被下一個 session 或代碼審誤讀成「這次要交出來的八條合約」。建議把 S362 從這份「增量 4 送實作」的合約清單移出(或明講「本增量只交付程式骨架與待增量 3 補上的測試,代碼審這條先跳過」),並在增量 3 的計劃裡補回這條測試的落地責任,避免代碼審卡在一條本增量做不到的驗收項上。

## S360、S361、S363、S365、S366、S367 逐一核對:可測、拿掉實作會真的翻紅

- S360(總曝險停下次數)、S361(表滿延後次數):都是對 `write_stops` 做 `COUNT(*)` 加篩選,跟既有 `test_aggregate_limit.py` 裡 `stops(h)` 這個直接戳 `write_stops` 的手法同構;S361 要守「同一份提案反覆延後只算一次」,現成場景已經在 `test_a_full_table_deferral_is_recorded_once_per_proposal`(`tests/executor/test_aggregate_limit.py:389-398`)驗過底層寫入語意只留一列,新查詢的測試只要在既有場景上多斷言一次計數,不必重新造情境。把查詢函式換成永遠回 0(或不篩選)的假實作,這兩支測試都會真的斷言失敗,不是套套邏輯。
- S363(額度使用率):`aggregate_used` 已有 S330–S343 一整批獨立測試守著算法本身,新測試只要驗證「回傳的已用等於同一時刻呼叫 `aggregate_used` 的結果、門檻讀設定檔、剩餘用減法封底」,拿掉「剩餘 = max(門檻-已用, 0)」這段封底邏輯,換成允許負值,斷言會真的紅。
- S365(F7 可解釋性端到端):`tests/executor/test_f7_end_to_end.py` 已經是同一個情境(3000 廣告、8 工作者、到門檻即停),S365 的測試只是在既有跑完的基礎上多查詢一、四、五三支,不必另起爐灶,且既有測試已證明並行下數字精確(`(NEW_BUDGET - BUDGET) * len(h.dsp.writes) <= AGGREGATE_LIMIT` 這類斷言),可以直接沿用同一批斷位方式核對查詢結果與 `h.dsp.writes`、`stops(h)` 算出的期望值逐一相等。
- S366(舊資料庫補索引):`inbox_store.py` 的 `_migrate_columns`/`_proposals_outdated` 已有同構的「手動建一個缺欄位的舊表、重新開 `InboxStore`、核對補齊」測試先例(`test_an_old_inbox_database_accepts_every_current_block_code`,`tests/executor/test_aggregate_limit.py:298-317`),而且 `write_stops` 的索引若照現有做法直接寫進 `SCHEMA` 字串(用 `CREATE INDEX IF NOT EXISTS`),`connect()` 每次開檔都會整段 `executescript`,不需要额外的遷移判斷分支——比 S366 文字暗示的「開啟時補上」更簡單,是好事、不是缺口,但也代表測試不必去驗一套複雜的遷移邏輯,只要驗「舊表(手動建、沒有索引)重新開啟後 `PRAGMA index_list` 有這個索引、資料列不變」即可,可測。
- S367(總曝險稽核明細):「沒給範圍時應拒絕」可以直接 `pytest.raises`;三段輸出(停下、通過、剩餘)都能用 `Harness` 現成的 `submit`/`process`/`query` 組出多租戶、多時間點的資料,再核對排序與內容,可測、不假綠。

## F2 S364「每一張表」沒說怎麼比對,若照現有查詢慣例寫死表清單,未來增量 3 加新表會悄悄漏守

severity: minor
blocking: 否

引句:「每一支查詢跑完,執行行程資料庫的每一張表應一列不多、一列不少、內容不變」

目前執行行程資料庫只有 `proposals`、`inbox_events`、`write_stops`、`attempts` 四張表(`src/rtb/executor/inbox_store.py:143-159`),這次寫死這四張表就能完全滿足合約字面。但增量 3 會在同一個資料庫加「核可使用表」,如果 S364 的綁定測試沿用既有測試裡常見的「列出我知道的表逐一 SELECT」寫法(而不是像 `_proposals_outdated` 那樣先查 `sqlite_master` 動態列出所有表名),增量 3 的新表加進來後,這支測試不會自動涵蓋它,「查詢不寫入」的守衛就悄悄漏了一張表而不會紅——這正是題目提醒的「一列不多一列不少怎麼比對全部表」。建議測試改成先 `SELECT name FROM sqlite_master WHERE type='table'` 動態列舉再逐表比對,不要在測試裡手寫表名清單。

## F3 查詢五的「通過的」清單排除舊格式列,但查詢四的「已用」把舊格式列算進去,S36x 沒有測試核對兩者對不對得起來

severity: minor
blocking: 否

引句:「通過的:嘗試紀錄裡這個租戶、第一列開始時間在範圍內、帶預留金額的每一把鍵(冪等鍵、廣告、預留金額、開始時間、目前狀態),依時間排序。」

「帶預留金額的每一把鍵」意味著查詢五只列出有 `reserved_amount` 的列;但 `aggregate_used`(`src/rtb/executor/attempt_store.py:366-372`,尤其第 370 行 `AND (f.tenant = ? OR f.tenant IS NULL)`)把沒有租戶欄的 Phase 6 之前舊列也保守算進「每一個租戶」的已用額度。也就是說,一個租戶如果既有新格式的預留、也有舊格式的歷史加預算,查詢四回的「已用」會比查詢五「通過的」清單裡各筆金額加總還大,稽核明細沒辦法完整說明那個差額從哪來——剛好卡在 F7 完成條件「稽核可說明…剩餘額度」這句上。S365 的 F7 端到端場景是全新資料、不含舊格式列,S367 的合約文字本身內部自洽,所以這不是字面矛盾,但目前 S360–S367 沒有一條測試在「同一租戶混有舊格式列」的情境下核對查詢四與查詢五的一致性,算是完成條件裡一個測不到的角落,建議留一條測試或在合約裡明講這個已知落差不必自洽。

## 結論

八條合約裡七條(S360、S361、S363–S367)可測、實作被拿掉會真的翻紅,而且都能沿用 `tests/executor/fakes.py` 的 `Harness` 與既有 Phase 6 測試檔的手法,不需要新造測試基礎設施。真正的缺口是 S362:它掛在本增量的合約清單裡卻天生做不出來、測不出來(F1,major)。另外兩點(S364 的全表比對方法、S367 與 S363 之間的舊列一致性)是可以在寫測試時順手補上的精度問題(F2、F3,minor)。
