severity: major

## F1 「拆開 aggregate_used」沒有承諾保留開始一筆(begin)呼叫的入口名稱,S336 的並行測試靠 monkeypatch 攔截這個名字才測得到競態

severity: major
blocking: 是 — 若拆分後 `begin()`(`attempt_store.py:334`)改成直接呼叫拆開後的「列出」與「加總」兩支之一(而不是繼續呼叫仍叫 `aggregate_used` 的入口),既有 [S336] 並行測試(`tests/executor/test_aggregate_limit.py:215` 的 `monkeypatch.setattr(attempt_store, "aggregate_used", slow_used)`)會攔截不到 `begin()` 內部真正呼叫的函式,測試照樣綠但不再真的測到競態延遲注入,變成偽陰性(該擋的沒擋住也測不出來)。

引句:「已用就是那份清單的加總,兩者不可能對不上」

我對照程式碼確認:`src/rtb/executor/attempt_store.py:334` 的 `begin()` 目前是用裸名 `aggregate_used(tx, reservation.tenant, now)` 呼叫同模組的全域函式,Python 對裸名的查找是呼叫當下才解析同模組全域命名空間,所以 `monkeypatch.setattr(attempt_store, "aggregate_used", slow_used)` 能攔截到它,[S336] 的競態測試正是靠這個機制在「第一個工作者算完額度、還沒提交」時插入延遲(`test_aggregate_limit.py:201-206` 的 `slow_used`)。本節「查詢五」段落(`r2-snapshot.md:336`)只交代了要把 `aggregate_used` 拆成「列出目前計入的每一筆」與「加總」兩步,並保證兩步的結果與現在一致,但**沒有明寫「`begin()` 的呼叫點繼續呼叫名為 `aggregate_used` 的入口,不直接呼叫拆開後的兩支之一或把邏輯內縮進 begin()」**。這件事不是查詢四的問題(查詢四本文有明寫「已用直接用 aggregate_used」),但查詢五段落本身的措辭是「拆成...兩步」,沒有排除「拆完之後 begin() 改成分別呼叫這兩步、原本那個公開名字被拿掉或變成只給查詢四用的別名」這種讀法;若實作者照這個讀法把 begin() 改寫成直接呼叫列出函式再自己加總(例如為了省一次函式呼叫或圖清楚),S330/S336 的行為不會變(額度判斷邏輯本身沒變),但 S336 這支綁定測試會從「真的驗證交易內互斥」退化成「什麼都沒驗證但照樣通過」,而且沒有任何機制會提醒——這正是這個專案在意的「偽證據」類問題。
建議:在查詢五段落明寫一句「拆開後 `begin()` 仍呼叫這個公開名字不變的入口(現有簽名 `aggregate_used(tx, tenant, now) -> int`),不得把加總邏輯內縮進 `begin()` 或改叫拆開後的兩支函式之一,以維持 [S330]/[S336] 既有測試(含 monkeypatch 攔截點)不必跟著改」。

---

## 已核對、沒有問題的部分

- **依賴方向確實只有收件口→嘗試紀錄,沒有反向依賴**:實際核對兩支檔案的匯入,`src/rtb/executor/inbox_store.py:37` 有 `from rtb.executor import attempt_store`,而 `src/rtb/executor/attempt_store.py` 全檔沒有任何一行匯入 `inbox_store`(其匯入只到 `rtb.domain.*`)。查詢五放在收件口模組、內部呼叫嘗試紀錄模組的計入規則(拆開後的「列出」函式),符合這個既有的單向依賴,不會造成循環匯入,也符合本節「不新增反向依賴」的宣稱。

- **查詢四「門檻由呼叫端傳入」這件事本身,round 1 架構席已經抓到並在這版折入,現在的寫法是自洽的**:`r1-arch.md` 指出前一版把「查詢四要比照 aggregate_used 只吃租戶與現在」跟「門檻要讀設定檔」混在一句話裡會做錯;這版(`r2-snapshot.md:332`)已經拆開,明寫「嘗試紀錄模組不讀租戶設定檔,呼叫端用簽發模組既有的 `load_tenants` 取得(那裡帶著設定檔的安全檢查,不另寫一份讀法)」。核對 `src/rtb/executor/capability_signer.py:114` 的 `load_tenants(path)` 內部呼叫 `_read_config_securely`(同檔 63-88 行)做了防符號連結開檔、擁有者與寫入權限檢查,是全專案唯一讀這份設定檔的安全路徑;明寫「不另寫一份讀法」等於直接擋住「呼叫端自己另開一份不安全讀法」這個風險。現況也確實沒有第三方讀法:全專案只有 `capability_signer.py` 自己的 `_tenant_of`(114 行同檔 153-154)呼叫 `load_tenants`,`inbox_store.py`、`attempt_store.py` 都沒有另開讀設定檔的程式碼。
  - 「呼叫端是誰」目前確實沒有具名(沒有 CLI 或管理工具),但這跟既有 Phase 5 指標(`version_conflict_count`、`task_store.replan_counts`)同樣只有測試在呼叫的現況一致,本節「實務隱患」段落也承認「現在只有人工與測試會呼叫」,不是本節獨有的缺口,不算新問題。
  - 剩下的小缺口是:`load_tenants` 回傳的是全部租戶的 tuple,要拿到「指定租戶的門檻」得由呼叫端自己用名字過濾(現有程式沒有「依名稱查單一租戶」的公開函式,只有依廣告編號查的私有 `_tenant_of`);但因為設定檔本身保證同名租戶唯一(`_parse_tenants` 用 dict 讀,`owners` 也檢查了廣告不重複歸屬),這個過濾寫起來沒有歧義,不構成「讀錯設定檔」的風險,只是文件沒有把這一步的寫法明寫出來(措辭層級,不影響行為)。

- **索引補建的做法跟既有程式一致**:核對 `src/rtb/executor/attempt_store.py:45-58` 與 `src/rtb/executor/inbox_store.py:143-166`,兩邊的表結構都是寫在 `SCHEMA` 字串裡用 `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`,經 `connect()` 對整份 `schema` 跑 `executescript`,天生冪等;現有索引(`attempts_first_rows`、`attempts_verified_by_time` 等)就是這樣補的。本節「索引」段落沿用同一寫法(「不存在才建」),不需要走 `inbox_store.py:359` 那套只給改欄位/CHECK 限制用的重建流程,做法與既有慣例一致,measurement 與 round 1 架構、效能席的結論相符,這版文字沒有變動、也沒有引入新落差。

- **對增量 3 第 2 版的需求(核可使用表要帶任務、修訂、內容雜湊或直接帶廣告)寫得夠清楚,能讓增量 3 的作者照著做**:round 1 的 `r1-dep.md` F2 指出前一版只假設「核可使用表能依廣告篩」卻沒交代怎麼做到;這版(`r2-snapshot.md:331` 與 `386`)已經比照「待核可數」那段的 join 寫法,明講「依廣告篩要核可使用表帶任務、修訂、內容雜湊(接停下紀錄取廣告)或直接帶廣告」,並在「使用者裁定與相依」段落重申一次、指名是「設計審第 1 輪相依席」的要求、且隨增量 3 實作交付。兩種做法(join 停下紀錄表 vs. 直接存廣告)留給增量 3 第 2 版的作者依實作方便選,這是合理的開放度,不是遺漏——目標狀態與理由都寫清楚了,不像 round 1 那樣是「呼叫端假設一個做不到的能力」。
