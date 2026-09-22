severity: major

### 1. `TaskStore` 補欄位若因忙碌逾時失敗,洩漏出去的是裸 `DatabaseBusy`,不是姊妹模組統一的模組專屬 Busy 例外——三支模組其實還是三套寫法
severity: major
blocking: 是 這是「引入專案裡原本沒有的第二種做法」——三支模組的補欄位失敗處理包裝碼文字雖然一致,但對外可觀察到的例外型別並不一致,等於三套裡藏了三種行為,跟 r1 sarch-2 要求的「折成同一套寫法」目標不符
引句:「補欄位失敗時不留下沒人關的連線(三支資料庫模組同一寫法)」
說明:r2-delta 把 `CampaignStore.__init__`、`TaskStore.__init__` 都改成
```python
try:
    self._migrate_evidence_payload_column()
except BaseException:
    self._conn.close()  # 補欄位失敗時不留下沒人關的連線(三支資料庫模組同一寫法)
    raise
```
文字上跟 `InboxStore.__init__` 一致,但三支模組原本(也是 r1 sarch-2 要對齊的基準)對「補欄位期間鎖逾時」這件事的處理不只是關連線,還要把 `sqlitekit.DatabaseBusy` **轉成模組自己的 Busy 例外**,讓呼叫端能用同一套介面判斷「可以重試」:
- `InboxStore`:`except DatabaseBusy as exc: self._conn.close(); raise InboxBusy(str(exc)) from exc`(`src/rtb/executor/inbox_store.py:199-201`)
- `CampaignStore`:`_migrate_columns` 內部呼叫 `begin_immediate` 時已經 `except DatabaseBusy as exc: raise StoreBusy(str(exc)) from exc`(`src/rtb/dsp/store.py:200-203`),所以往外看仍是 `StoreBusy`
- `TaskStore`:`_migrate_evidence_payload_column` 直接用 `with immediate_transaction(self._conn):`(`src/rtb/analyzer/task_store.py:152`),中間完全沒有攔 `DatabaseBusy`;`__init__` 新加的 `except BaseException` 也只轉最上層的 `connect()` 逾時(`src/rtb/analyzer/task_store.py:137-138` 那段既有的 `except DatabaseBusy as exc: raise TaskStoreBusy`),補欄位那段的 `except BaseException`(`src/rtb/analyzer/task_store.py:141-143`)只關連線就原樣 `raise`,不會把 `DatabaseBusy` 轉成 `TaskStoreBusy`

行為斷言(已實跑實驗驗證,不改動 repo,只在暫存目錄操作):輸入——一份缺 `payload_json` 欄位的舊資料庫,另一條連線用 `BEGIN IMMEDIATE` 佔住寫鎖,再用 `busy_timeout_seconds=0.1` 建構 `TaskStore(path, 0.1)`;預期(比照 `CampaignStore`/`InboxStore` 的既有慣例)——應該收到 `task_store.TaskStoreBusy`;實際——收到裸的 `rtb.sqlitekit.DatabaseBusy`。實驗腳本存於 `/private/tmp/claude-501/-Users-enzo/36c33b75-fd17-4bff-aa49-20f97a550e58/scratchpad/exp/check_busy_leak.py`,輸出:
```
initial evidence cols (no payload_json = old db): [...無 payload_json...]
GOT raw DatabaseBusy (LEAK - inconsistent with CampaignStore/InboxStore): DatabaseBusy('database is locked')
```
影響:任何目前(或未來)依「捕捉 `TaskStoreBusy` 決定要不要重試」這條介面契約寫的呼叫端,遇到「補欄位期間資料庫忙碌」這個特定失敗原因時會抓不到,例外型別會跟另外兩支店不一致地穿透出去。新測試 `tests/test_migration_failure_closes_the_connection.py` 只用 `monkeypatch` 讓 migrate 方法直接丟 `RuntimeError`(`governance/review-reports/code-phase3-execution/r2-delta.patch` 內 `def fail(_self): raise RuntimeError("補欄位失敗")`),沒有覆蓋「補欄位期間鎖逾時」這條真實路徑,所以測試本身沒說謊、但沒把「三支模組同一寫法」這個聲稱的不變量測全,留下這個真實分支沒人守。
file: `src/rtb/analyzer/task_store.py:139-143`
file: `src/rtb/analyzer/task_store.py:152`
file: `src/rtb/dsp/store.py:198-208`
file: `src/rtb/executor/inbox_store.py:193-204`

### 2. 新測試檔放進 `tests/` 根目錄,跟既有「根目錄只放靜態檢查閘接線測試」的慣例不一致
severity: minor
blocking: 否 屬於測試檔案組織上的慣例落差,不影響行為正確性,也不是產出面的第二套實作或跨層直呼
引句:「diff --git a/tests/test_migration_failure_closes_the_connection.py b/tests/test_migration_failure_closes_the_connection.py」
說明:目前 `tests/` 根目錄下的 `.py` 只有兩支,`test_static_checks.py` 與 `test_static_wiring.py`,兩者都明確掛在「靜態檢查閘」這個系統節點下(`docs/rtb-production-agent-demo-knowledge/Systems/靜態檢查閘.md` 的 `about_code: tools/mypy_sarif.py` 與其 `TEST:` 行逐一點名這兩支),性質是「專案級工具接線是否真的生效」的後設測試,不是業務邏輯測試。新增的 `test_migration_failure_closes_the_connection.py` 測的是三支業務資料庫模組(`dsp/store.py`、`analyzer/task_store.py`、`executor/inbox_store.py`)建構子行為是否一致,性質上更接近「多個模組共用同一份底層機制、要驗證行為一致」——這種跨模組不變量測試專案已有慣例放在 `tests/kit/`,緊鄰它驗證的共用機制:例如 `tests/kit/test_shared_base.py` 專門驗證「抽出共用基礎之後 DSP 沒有留下第二份實作」,而這三支店共用的正是 `rtb.sqlitekit.connect`/`immediate_transaction`。把這支新測試放進 `tests/` 根目錄,等於讓根目錄多出一種跟既有慣例(根目錄=靜態檢查閘接線測試)不同的用途,形成兩種並存的「根目錄測試檔代表什麼」的理解。
file: `docs/rtb-production-agent-demo-knowledge/Systems/靜態檢查閘.md`
file: `tests/kit/test_shared_base.py:1`

---
補充:時鐘型別已檢查,`execution.py` 的 `clock: Callable[[], datetime]` 與 `runner.py` 的 `clock: Callable[[], datetime] = utc_now` 跟既有 `inbox_store.py:243`、`inbox_server.py:119` 的寫法逐字一致(型別、位置都對得上),`Clock` 這個多餘 `Protocol` 也已移除,這條 r1 發現的修法本身沒有問題。
