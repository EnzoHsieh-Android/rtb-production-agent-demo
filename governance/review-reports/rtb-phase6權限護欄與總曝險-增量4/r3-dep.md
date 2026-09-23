severity: clean

範圍:只審凍結快照「## 增量 4 設計:可觀測」一節,鏡頭是相依與相容——增量 4 對增量 1 既有程式/測試的影響、對增量 3 的欄位相依、回退段是否屬實、跟增量 2 有沒有交集。第 3 輪末輪紀律:只報會擋的問題,已折平的舊問題不重報。

## 逐項核對結果(均與程式碼一致,沒有發現矛盾)

**aggregate_used 拆清單 + 開始一筆照舊呼叫它**:程式現況(`src/rtb/executor/attempt_store.py:355-380`)`aggregate_used(tx, tenant, now)` 目前是一支完整函式,還沒拆;`begin()`(同檔 313-352 行)在 334 行呼叫 `aggregate_used(tx, reservation.tenant, now)`。快照原文:

引句:「開始一筆時也照舊呼叫這個入口(既有並行測試靠攔截它:拆出來的清單函式只在它內部與查詢五用,不取代它」

比對 `tests/executor/test_aggregate_limit.py:198`,並行合約 [S336] 的 `_race_two_workers` 確實是 `monkeypatch.setattr(attempt_store, "aggregate_used", slow_used)`,直接掛在 `attempt_store` 模組屬性上,而 `slow_used` 內部呼叫 `real_used = attempt_store.aggregate_used`(被 patch 前的原函式)。只要重構後 `begin()` 仍是呼叫模組層級名稱 `aggregate_used`(不是把清單邏輯內嵌成 `begin()` 直接呼叫的另一支私有函式、或被 `_take`/`execution.py` 的呼叫路徑繞過),這支測試的攔截點就還在。快照這句話對現況的描述準確,且與 `execution.py:288-291` 的 `_aggregate_used` 包裝函式(呼叫 `attempt_store.aggregate_used` 並轉譯例外)並存不衝突——它只在 `TABLE_FULL` 例外分支用來重算 `used` 寫停下紀錄,不是額度檢查的入口。

**對增量 3 的依賴只綁四欄**:「已核可放行數:核可使用表的列數…只綁核可使用表的「租戶、任務、修訂、內容雜湊」四欄,其他欄位不假設」與文末「使用者裁定與相依」段的

引句:「查詢三只綁「租戶、任務、修訂、內容雜湊」四欄。查詢三的細節等第 2 版推上來再對齊」

兩處一致,沒有互相矛盾;而且本節明確把查詢三算作「隨增量 3 實作交付」、不在這個增量的合約清單裡驗([S362] 註明測試寫在增量 3 的核可測試檔)。查了程式現況:`Disposition` 列舉(`src/rtb/executor/inbox_store.py:50-56`,main 分支到 136d17a 增量 2 已合併仍如此)只有 `in_progress/handed_off/blocked/dead_letter` 四態,沒有「待核可」,核可使用表也還不存在——跟快照「增量 3 只有設計第 1 版」的前提一致,細節留待第 2 版是誠實的,不是漏審。

**回退段是否屬實**:

引句:「這幾支都是只讀函式,拿掉即回到現況;`aggregate_used` 拆成「清單加總和」兩步,回退時可以留著(結果不變)或合回去;四個索引留著無害(只加速查詢,不改寫入語意)」

核對:四支查詢與 `aggregate_used` 拆分都不寫資料([S364] 合約也釘住這點),四個新索引都是「不存在才建」的一般或部分索引(`CREATE INDEX IF NOT EXISTS`),不是 UNIQUE 約束、不改寫入路徑;`write_stops.kind` 欄位本身沒有資料庫層 CHECK 限制(不像 `disposition`/`block_code` 有 CHECK IN (...) 需要重建表才能加新值),所以索引留著不會卡到未來增量 3 新增「比例過大」種類時的寫入。回退段描述與程式現況相符,沒有找到不實之處。

**跟增量 2(phase6-inc2-impl)有沒有交集**:實測 main 分支(136d17a 已合併增量 2 護欄表格)的 `write_stops` 建表語句與 `StopKind` 列舉(`git show main:src/rtb/executor/inbox_store.py`)仍然只有 `AGGREGATE_LIMIT_REACHED` 與 `TABLE_FULL` 兩種,`capability_signer.py` 的 `load_tenants`/`Tenant.aggregate_limit` 簽章也沒變。這代表增量 2 的比例上限擋下(執行前檢查層)目前完全不寫 `write_stops`,查詢一(總曝險停下次數)、查詢二(表滿延後次數)、查詢五(總曝險稽核明細)都篩定種類,不會把比例擋下算進去,也不會被比例擋下污染;查詢四/查詢五共用的 `aggregate_used`/`_counted` 只認「加預算被記進嘗試紀錄」,比例擋下在執行前檢查就結案、根本不開嘗試,同樣不會進額度計算。三份 lands_in 落點(收件口、外部寫入嘗試紀錄、增量 3 的核可模組)裡都沒有指到增量 2 改的檔案(執行前檢查、簽發器),没有交集。

## 結論

在本輪指定的鏡頭下(增量 1 相容性、增量 3 欄位相依、回退真實性、增量 2 交集),快照裡的描述逐句對照程式碼(ff06a03 與更新的 main/136d17a 兩個版本)都成立,沒有找到會讓字面實作做錯行為的矛盾,沒有新的 blocking 發現。前兩輪的相依/相容意見(r1 相依席、r2 模組邊界相依席)已折入且在程式現況核對下仍然站得住。
