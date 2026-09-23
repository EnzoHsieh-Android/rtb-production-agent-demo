severity: clean

本輪(第 2 輪)只核對第 1 輪改過的四點,有沒有引入第二種做法或跨層:查詢五移到收件口模組、查詢四門檻由呼叫端傳入、`aggregate_used` 拆成兩步、四個索引(對照既有部分索引寫法)。逐點對程式碼(ff06a03)查證,結果一致,沒有發現跨層或第二種做法。

查詢五放進收件口模組沒有反過來新增依賴:`src/rtb/executor/inbox_store.py:37` 本來就 `from rtb.executor import attempt_store`,而 `attempt_store.py` 沒有 import 回 `inbox_store`。快照原文「跨兩張表的放在已經依賴另一邊的那個模組(收件口模組本來就依賴嘗試紀錄模組,反過來沒有),不新增反向依賴」跟程式現狀對得上。

查詢四門檻由呼叫端傳入的做法,跟現有「總曝險預留」那條路徑同一個模式:`src/rtb/executor/execution.py:409` 呼叫 `capability_signer.load_tenants` 取得 `grant.aggregate_limit`,`execution.py:432-433` 組成 `attempt_store.Reservation(...aggregate_limit)`,`attempt_store.py:335` 的 `begin` 只用 `reservation.limit` 做比較,自己不讀租戶設定檔。查詢四的設計(嘗試紀錄模組不讀設定檔、門檻由呼叫端傳)是延用這個既有模式,不是另開一條路。快照原文「嘗試紀錄模組不讀租戶設定檔,呼叫端用簽發模組既有的 `load_tenants` 取得(那裡帶著設定檔的安全檢查,不另寫一份讀法」屬實。

`aggregate_used` 拆成兩步的敘述掛在查詢四段落下,即歸屬嘗試紀錄模組本身的內部重構(現況是 `attempt_store.py:355-380` 單一函式邊撈邊用 `_counted` 加總),拆開後查詢五要用的「目前計入清單」由嘗試紀錄模組對外多開一個函式,查詢五(收件口模組)呼叫它而不是自己對 `attempts` 表另寫 SQL——快照原文「計入規則跟 `aggregate_used` 共用同一支判斷,不另寫一份」正是防第二套實作的說法,跟既有依賴方向(收件口→嘗試紀錄)一致,不是跨層讀表。

四個索引跟既有部分索引寫法對得上:`attempt_store.py:53` 既有 `attempts_first_rows ON attempts (campaign_id) WHERE seq = 1` 是部分索引,設計裡「嘗試紀錄:第一列按『租戶、開始時間』的部分索引...跟既有『第一列按廣告』的部分索索引同一種做法」屬實可查證。停下紀錄表三個索引是一般(非部分)索引,這張表沒有「只挑第一列」這種既有部分索引要處理的場景,用一般索引不是引入第二種風格,是同一套「按查詢用得到的欄位建索引」原則套用到不同表結構。

沒有發現第二種做法或跨層問題,本輪不重報第 1 輪已折平的項目。
