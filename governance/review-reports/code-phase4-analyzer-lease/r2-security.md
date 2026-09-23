severity: clean

# 資安審查:分析行程任務租約(Phase 4 增量 3b)第 2 輪 snapshot

審查範圍:`src/rtb/analyzer/flow.py`、`src/rtb/analyzer/task_store.py`、`tests/analyzer/test_task_lease.py` 的租約新增(取得/放掉/圍籬),含第 2 輪修正(擁有者格式改為 `pid-thread-序號`、`acquire_lease` 補任務存在檢查、吞錯搬進 `release_lease_quietly`)。

## 1 不可信輸入流到危險操作

已看,無。

- 新增/改動的 SQL 全部走 `sqlite3` 參數化(`?` 佔位符):`INSERT INTO task_leases VALUES (?, ?, ?, ?)`(acquire_lease)、`INSERT INTO task_leases VALUES (?, ?, NULL, ?)`(`_append_release`)、`SELECT ... WHERE task_id = ?`(`_current_lease`、`acquire_lease` 的存在檢查),沒有任何 f-string/`%`/`.format()` 拼接進 SQL 字串。
- `owner`、`task_id`、`lease_seq`、`expires_at` 全部以 tuple 參數傳入,不會被當 SQL 片段解讀。
- 測試裡的 `subprocess.run([sys.executable, "-c", CHILD, str(path), str(calls), NOW.isoformat()], ...)`(test_task_lease.py S153)是列表參數、沒有 `shell=True`,`CHILD` 是測試檔內寫死的常數字串,不是從任何外部/不可信輸入組出來的,不構成注入面。
- `_iso(now + LEASE_DURATION)`、`_iso(now)` 只吃 `datetime`,不是字串拼接。

沒有攻擊路徑,不成 finding。

## 2 權限:可預測的擁有者(delta 重點)能不能被冒用繞過圍籬

已看,推論,不構成可用漏洞。

- Delta 把 `owner` 從 `uuid.uuid4().hex`(128-bit 隨機)改成 `f"{os.getpid()}-{threading.get_ident()}-{next(_OWNER_CALLS)}"`,`pid` 可從 `ps`/`/proc` 猜到、`_OWNER_CALLS` 每個行程從 0 起算——確實比 uuid4 更容易被「猜中」字串本身。
- 但圍籬(`_holds`/`_lease_allows`)比對的是 `(lease_seq, owner)` 這一組,`lease_seq` 是每個任務自己的租約序號(整數,單調遞增),不是靠 `owner` 的隨機性當保密憑證。要冒充目前持有者,除了猜對 `owner` 字串,還得猜對當下的 `lease_seq`——而 `lease_seq` 與 `owner` 本身在資料庫裡是明文可查(`SELECT lease_seq, owner FROM task_leases ...`),換言之從一開始 uuid4 也從未提供「保密」屬性:任何能拿到 `TaskStore`(同一支資料庫)的呼叫端,不管 owner 是 uuid 還是 pid 組字串,都能直接查表讀出目前真正的 `(lease_seq, owner)` 再自己組一個 `LeaseReceipt(task_id, lease_seq, owner)` 去呼叫 `release_lease`/`commit_step`——這條路徑在改動前後都存在,不是這次 delta 新開的洞。
- 唯一「不用查表、單靠猜」的情境是:攻擊者拿不到 DB 存取、只能憑 pid/tid/序號規律去盲猜。但這需要同時猜中「同一台機器上另一個行程的 pid」+「該行程當下的執行緒 id」+「它是第幾次呼叫」,而且圍籬還要求这份猜測去建構 `LeaseReceipt` 物件並直接呼叫 `TaskStore.release_lease`/`commit_step` 這兩個 Python API——這不是任何網路可達的入口,呼叫端必須已經是能 import `rtb.analyzer` 並拿到同一份 SQLite 連線/檔案的行程,等於已經在「同一 OS 使用者可直接動 DB 檔案」這條背景說明的既知限制範圍內。
- 威脅模型本來就寫明「防忘記,不防刻意繞過」,`owner` 從設計上就不是拿來擋刻意繞過的認證憑證(圍籬靠的是交易內的 SELECT-then-fence,不是密碼學保密);delta 改用可讀 id 是為了讓租約列看得出是哪個行程(比照執行側慣例),屬於可觀測性取捨,不是把原本有效的存取控制拆掉。

誰、哪個入口、送什麼、拿到什麼(推論的假設攻擊路徑,寫清楚為何評不到 blocking):
- 誰:能在同一台機器 import `rtb.analyzer.task_store` 並開同一份 SQLite 檔案的另一個 Python 行程(已經滿足「同 OS 使用者可動 DB」的既知限制)。
- 入口:直接呼叫 `TaskStore.release_lease(receipt, now)`,其中 `receipt` 用猜到或查到的 `(task_id, lease_seq, owner)` 組出。
- 送什麼:一個提早組出的 `LeaseReceipt`。
- 拿到什麼:讓目前持有者的租約提早被標記放掉,使第三方能在原持有者還在跑外部呼叫時搶到新租約——這正是 `release_lease` 文件裡明講、且已由 `_holds()` 圍籬 + 測試 S159 (`test_a_late_release_from_a_replaced_holder_writes_nothing`) 針對「舊持有者遲來的放掉」防守的情境;但那條測試防的是「舊 receipt 在被接手後才放」,不是「攻擊者用查到/猜到的當前 receipt 主動搶先放」。理論上存在,但需要的存取層級(同機同 DB 的可執行程式碼)已經超出這份威脅模型要防的範圍,而且不需要靠 owner 的隨機性也做得到(改前用 uuid4 一樣做得到,因為值本身查得到)。

blocking: 否 — 需要的存取層級落在背景說明列為既知限制的範圍內(同 OS 使用者/同資料庫可執行程式碼),且此路徑在改動前(uuid4)就已存在,delta 並未新增攻擊面,只是讓合法除錯情境下的擁有者好讀。

引句:「租約擁有者:照執行側「行程編號開頭」的寫法看得出是哪個行程,再加執行緒與呼叫序號,讓
同一個行程裡的每一次推進都是不同的擁有者」

## 3 密鑰與個資

已看,無。

- `os.getpid()`、`threading.get_ident()` 寫進 `task_leases.owner` 欄位:這是行程編號與執行緒 id,不是密鑰、不是個資,且只落在分析行程自己的本地 SQLite(`docs` 描述的分析側 DB),不會被送到任何 log 聚合或外部服務;背景也講明分析行程不持有寫入金鑰。R16(秘密不進 log)不適用,這裡連 log 都沒寫,是持久化到自家表。

## 4 加密與隨機數

已看,無新增風險。

- `_OWNER_CALLS = itertools.count()` 只是遞增計數器,`owner` 字串不再需要密碼學隨機性,因為(如第 2 類分析)它從未真正作為保密憑證使用——圍籬靠的是交易內 SELECT 後比對,不是猜不到 owner 這件事本身。沒有自製加密、沒有偽隨機數被誤用在安全敏感位置。

## 5 執行邊界

已看,無。

- 沒有新的網路呼叫、沒有新的子行程在生產路徑(`subprocess` 只出現在測試,且是模擬「行程猝死」的受控情境,`os._exit(9)` 也侷限在子行程腳本內)。`LEASE_DURATION = timedelta(seconds=60)` 是常數,不受外部輸入影響,不構成邊界繞過或資源耗盡的可利用路徑(DoS 類本來就排除在報告範圍外)。

## 6 行動端

已看,無。此次改動只在分析行程(Python/SQLite)後端邏輯,沒有觸及任何行動端程式碼。

## 新依賴

已看,無。`itertools`、`os`、`threading` 皆為 Python 標準函式庫,delta 移除了 `uuid`、`sqlite3`(從 `flow.py` 頂層 import,改搬進 `task_store.py` 的 `release_lease_quietly` 內部),沒有引入任何第三方套件。

## 與 lumos 固定席筆記對照

- `Systems/共用行程基礎.md` 的 ★INVARIANT★(「分析行程送不出故障注入標頭」「分析行程只能透過共用 HTTP 用戶端碰網路」)與本次 diff 無關——租約邏輯不碰網路層,未觸及該不變量,無衝突。
- `Systems/外部寫入嘗試紀錄.md`、`Systems/分析行程流程與檢查點.md`、`Systems/提案收件口.md` 的並行/冪等相關 ★INVARIANT★ 與本次「同一時間只有一個持有者花錢」的設計方向一致,未發現牴觸;租約表本身延續「只增不改」的既有慣例(delta 裡的測試 `test_opening_an_old_task_database_adds_the_lease_table_without_touching_history` 有斷言原始碼不含 `UPDATE `/`DELETE `),與圖譜描述的既有寫入紀律相符。
- 未發現筆記與程式碼對不上的地方。

## 結論

本輪(含 delta:owner 格式改為 pid-thread-序號、`acquire_lease` 補任務存在檢查、吞錯搬進 `release_lease_quietly`)沒有找到可被外部/不受信任輸入觸發的注入或繞過路徑。唯一值得記一筆的是「owner 可預測」,但推論後判定不構成新增的可利用漏洞(理由詳見類別 2),且落在文件明講的既知限制範圍內,故整體評級 clean,無 blocking 項目。
