severity: major

### 1. `Clock` 用 `Protocol` 包一個單一無參數呼叫,跟專案「何時該用 Protocol」的既有分界不一致
severity: major
blocking: 是 這是「引入專案裡原本沒有的第二種做法」,而且直接牴觸對照檔 `flow.py` 自己寫明的分界規則
引句:「class Clock(Protocol):」
說明:`execution.py` 把時鐘型別定義成
```python
class Clock(Protocol):
    def __call__(self) -> datetime: ...
```
但整個專案裡「時鐘」這種零參數、回傳單一值的簡單協作者,一律用裸的 `Callable[[], datetime]`(或 `Callable[[], float]`)當型別註記,從來不用具名的 `Protocol` 類別包裝——`inbox_server.py`、`inbox_store.py`、`dsp/server.py`、`dsp/capability.py` 四支既有檔全部如此。更關鍵的是,`execution.py` 自己在檔頭聲稱「寫法比照分析行程的流程」,而它比照的對象 `flow.py` 的檔頭docstring 明白寫著這條分界規則本身:三個介面(`EvidenceSource`/`Decide`/`Submit`)之所以用 `Protocol` 是因為「它們各自有具名的多個參數與語意(不是單純「一個函式」)」,並且明講「現有 `Callable` 用法(單一動作的簡單回呼)不受影響」。`Clock` 正是「單一動作的簡單回呼」那一類,依這條規則應該維持 `Callable[[], datetime]`,而不是新開一個只有一個 `__call__` 方法的 `Protocol` 類別。`Signer`、`DspPort` 兩個 Protocol 都有多個具名方法,符合既有分界,唯獨 `Clock` 不符合。
file: `src/rtb/analyzer/flow.py:12-15`
file: `src/rtb/executor/inbox_server.py:119`
file: `src/rtb/executor/inbox_store.py:244`
file: `src/rtb/dsp/server.py:215`
file: `src/rtb/dsp/capability.py:95`

### 2. `InboxStore.__init__` 在補欄位失敗時主動關連線,跟另外兩支資料庫模組的既有建構子寫法不一致
severity: major
blocking: 是 同一個「連線後補欄位」步驟,三支資料庫模組各寫一套失敗處理,不是共用同一套做法
引句:「補欄位失敗時不留下沒人關的連線」
說明:新的 `InboxStore.__init__` 把 `self._migrate_columns()` 包進 `try/except`,`DatabaseBusy` 與其他任何例外都主動 `self._conn.close()` 再往外丟。但專案裡另外兩支「連線建表後補欄位」的建構子——`CampaignStore.__init__`(`dsp/store.py`)與 `TaskStore.__init__`(`analyzer/task_store.py`)——都是接完 `connect()` 後直接呼叫 `self._migrate_columns()`/`self._migrate_evidence_payload_column()`,外面完全沒有 `try/except`,補欄位那一步一旦丟出例外,連線就沒人關。三處做的是同一件事(連線 → 依既有補欄位慣例檢查缺欄位 → 拿鎖補),但只有這次新增的這支多包了一層例外安全處理,形成三套不同的失敗處理寫法並存。設計文件的「實作時決定」清單裡沒有提到這個差異,看起來是實作時額外加的,不是設計審已經核過的項目。
file: `src/rtb/dsp/store.py:178-190`
file: `src/rtb/analyzer/task_store.py:133-139`

---

補充說明:本次審查已用 `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase3外部寫入安全_計劃.md` 的「增量 3 設計:執行一筆」核對過幾個乍看像架構分歧、實際上已經過設計審(架構對齊席)明文核准並寫進「實作時決定」的地方,因此不列為發現:
- `RunnerLock` 不走 `sqlitekit.connect()`、只重用 `begin_immediate`、直接 `sqlite3.connect`——文件「實作時決定」段落明講原因(共用連線函式會切 WAL,鎖檔重建時附屬檔對不上),且與程式碼內註解逐字吻合。
- `InboxStore._migrate_columns` 用「每次連線檢查、缺才在交易內補、拿到鎖後再查一次」的雙重檢查——文件明講這是沿用既有補欄位做法,且與 `dsp/store.py` 的雙重檢查模式一致。
- `execution.py` 沒有 import 任何 `rtb.dsp.*` 內部模組,只經共用 HTTP 用戶端呼叫 DSP,符合合約 S56(執行迴圈不得匯入 DSP 內部模組)。
- `DspClient`(執行行程)用 class、`analyzer/dsp_client.py` 用工廠函式回傳 closure——兩者對應的 Protocol 形狀不同(`DspPort` 是三個具名方法、`EvidenceSource` 是單一 `__call__`),各自的寫法都符合上述「多方法用 Protocol/class、單一簡單回呼用 Callable」的既有分界,不算兩種做法。
