severity: major

## F1 查詢五放進嘗試紀錄模組,但要讀的其中一半資料是收件口模組自己的表

severity: major
blocking: 是 — 違反設計自己訂的「放在資料所在那張表的模組」原則,且逆向打破現有單向依賴(收件口模組匯入嘗試紀錄模組,反向從未發生),照 spec 字面實作只有兩條路都會出錯:嘗試紀錄模組對它不擁有 schema 的表下 SQL,或反向匯入收件口模組造成循環匯入。

引句:「每支都是模組層函式,收交易,只讀不寫,放在資料所在那張表的模組(跟 Phase 5 一樣)」

引句:「查詢五 總曝險稽核明細(嘗試紀錄模組,使用者 2026-09-23 裁定加):同一租戶、同一時間範圍,回三樣東西」

查詢五要回的第一樣「被停下的」明講是「停下紀錄表這個租戶在範圍內的每一列」,而 `write_stops` 這張表是收件口模組建的:

引句:「停下紀錄表(`write_stops`,收件口模組建表)」

我對照程式碼確認了兩件事:

1. `write_stops` 的 `CREATE TABLE` 與所有讀寫(`record_stop`)都在 `src/rtb/executor/inbox_store.py`(`file: src/rtb/executor/inbox_store.py:153`、`file: src/rtb/executor/inbox_store.py:618`);`src/rtb/executor/attempt_store.py` 裡沒有任何一處出現 `write_stops`。
2. 現有依賴方向只有一條:`inbox_store.py` 匯入 `attempt_store`(`file: src/rtb/executor/inbox_store.py:37`),`attempt_store.py` 完全沒有匯入 `inbox_store`(通篇 import 只到 `rtb.domain.*`,`file: src/rtb/executor/attempt_store.py:18-38`)。這和 `attempt_store.py` 檔頭註解自述的角色一致——它是被收件口模組的交易入口驅動的純儲存層,不認識收件口那邊的表。

查詢一、二(總曝險停下次數、表滿延後次數)同樣讀 `write_stops`,設計正確放在收件口模組,這點跟資料所在一致,沒有問題。但查詢五要把同一張表的資料,連同 `attempts` 表的資料一起放進嘗試紀錄模組回傳,若照字面在 `attempt_store.py` 裡直接對 `write_stops` 下 SQL,就是嘗試紀錄模組讀一張它不擁有、且目前完全不認識 schema 的表,和查詢一、二訂下的規矩互相矛盾;若改成嘗試紀錄模組匯入收件口模組來借用查詢邏輯,則會和既有的 `inbox_store → attempt_store` 單向依賴打架,形成循環匯入。落點段落也把查詢五連同查詢四一起寫進同一篇筆記,沒有處理這個跨模組問題:

引句:「查詢五寫進 [[Systems/外部寫入嘗試紀錄]]」

這不是風格偏好——是兩個模組間第一次出現需要互讀對方表的情境,合約 S367 的測試如果照這個放法寫,測試本身就得讓 `attempt_store.py` 直接碰 `write_stops`,把違規焊進程式碼。建議寫成一支獨立的、不屬於任一張表模組的稽核組裝函式(在更上層,例如查詢五自己拆成先呼叫收件口模組的查詢一/二式列表函式取被停下的列,再呼叫嘗試紀錄模組取通過的列與查詢四的剩餘額度,由上層組裝),或者明講「查詢五放在收件口模組,呼叫嘗試紀錄模組的既有函式」(遵照現有 `inbox_store → attempt_store` 的既定方向,不是反過來)。

## F2 查詢四要讀租戶設定檔,但簽名「只吃租戶與現在」沒有留路徑取得設定檔位置,且會把讀設定檔的能力越級塞進儲存層

severity: major
blocking: 是 — 現有程式裡讀租戶設定檔(含防符號連結、擁有者與權限檢查)只有簽發模組 `capability_signer.py` 一處在做,由執行迴圈(`execution.py`)呼叫簽發;嘗試紀錄模組目前完全沒有檔案 I/O。設計把「讀租戶設定檔」的職責派給嘗試紀錄模組,卻同時把函式輸入限定成「只吃租戶與現在」,兩句話互相矛盾——沒有設定檔路徑就讀不到門檻,若照字面實作,函式若不是拿不到門檻就是要嘗試紀錄模組另外去猜/硬編路徑,兩者都是錯的行為。

引句:「門檻讀租戶設定檔。只吃租戶與「現在」」

背景段落也確認現況是簽發模組專責讀這份設定檔:

引句:「門檻在租戶設定檔的 `aggregate_limit`(缺這欄當 0),由簽發模組的 `load_tenants` 讀」

我對照程式碼確認:

- `load_tenants(path)` 定義在 `src/rtb/executor/capability_signer.py:114`,內部呼叫的 `_read_config_securely` 做了防符號連結開檔、擁有者與寫入權限檢查(`file: src/rtb/executor/capability_signer.py:63-88`)——這是簽發模組自己的信任邊界,不是隨便一支查詢函式可以繞過或重做一份的邏輯。
- 全專案匯入 `capability_signer` 的只有 `src/rtb/executor/runner.py` 和 `src/rtb/executor/execution.py`(執行迴圈/啟動層),`inbox_store.py` 與 `attempt_store.py` 都沒有匯入它;`load_tenants` 目前也只被 `capability_signer.py` 自己的 `_tenant_of` 呼叫(`file: src/rtb/executor/capability_signer.py:153-154`),外部沒有第二個呼叫點。
- `aggregate_used(tx, tenant, now)` 的既有簽名(`file: src/rtb/executor/attempt_store.py:355`)確實只吃租戶與現在,設計文字明講查詢四要比照——但「已用」全部資料在 `attempts` 表裡本來就查得到,「門檻」卻是外部設定檔,兩者資料來源層級不同,不能套用同一種「只吃租戶與現在」的簽名。

若嘗試紀錄模組要自己讀設定檔取得門檻,等於把簽發模組專責的「security-sensitive 設定檔讀取」複製或越級搬進儲存層,而且需要新增一個設定檔路徑參數才讀得到——這就不是「只吃租戶與現在」。比較貼近現有分層的做法是:查詢四只回「已用」由嘗試紀錄模組自己算,「門檻」由呼叫端(已經持有簽發模組讀出的設定,例如執行迴圈或觀測層)傳進來,或者查詢四整支往上移到一個不屬於任一張表模組、但可以同時呼叫嘗試紀錄模組與簽發模組的呼叫端。目前設計把兩件事混在一句話裡,照字面實作會做出錯的行為。

以下是確認一致、沒有落點問題的觀察:

查詢一、查詢二設計上放在收件口模組,和 `write_stops` 表的建表、寫入(`record_stop`)都在 `src/rtb/executor/inbox_store.py` 一致,資料與查詢邏輯同模組,沒有跨層。

鎖的做法(沿用既有 `transaction()` 寫入交易、不另開唯讀交易)和現有程式的既有做法一致:`version_conflict_count`(`file: src/rtb/executor/attempt_store.py:275`)與 `campaigns_with_unresolved`、`unresolved_keys` 等既有唯讀查詢,拿到的都是同一個 `ExecutorTransaction`(由收件口模組的交易入口發出的寫入交易),沒有另外開唯讀連線或唯讀交易;`task_store.replan_counts`(`file: src/rtb/analyzer/task_store.py:539`)同樣是「同模組的唯讀統計」直接用既有連線查。設計文字「專案目前只有一種交易入口,另開一種是第二套做法」符合現況,不是另立第二套做法。

查詢三(人工核可)因增量 3 尚未實作、模組尚未開檔,不在本次判斷範圍內,依派工說明不當作本節的錯。
