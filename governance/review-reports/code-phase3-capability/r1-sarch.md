severity: minor

### 1. `_plain_int` 在 dsp 套件內重造了已存在的整數檢查
severity: minor
blocking: 否（純內部重複，不跨層、不是新做法，只是同一包裡多了一份一模一樣的私有函式，風險低、修起來也小）
引句:「def _plain_int(value: object) -> TypeGuard[int]:」
說明:`src/rtb/dsp/capability.py` 新增的 `_plain_int`(判斷「是整數但不是布林」)跟 `src/rtb/dsp/store.py` 既有的 `_is_plain_int` 邏輯完全相同(`isinstance(value, int) and not isinstance(value, bool)`)。專案的既有規則是「領域層的共用小檢查(整數不含布林)只有 `_checks.py` 一份;DSP 刻意不依賴領域層,所以自己另有一份」——這條是說 DSP 對外(對領域層)可以有自己一份,但沒有交代 DSP **自己內部**(store.py 與 capability.py 之間)也要各刻一份。既然兩支檔同屬 `rtb.dsp`,較貼近既有慣例的做法是把這個檢查放在一個 DSP 內共用的地方(或直接沿用 `store.py` 已有的那份),而不是在新檔案裡重新定義一次同名同邏輯的私有函式。
file: `src/rtb/dsp/store.py:112`

其餘部分(共用層 `capabilitykit.py` 的格式/簽章、`dsp/capability.py` 與 `server.py._authorized_write` 的驗證順序與 `ERROR_TABLE`/例外階層掛法、`store.py._migrate_tenant_column` 與 `task_store.py._migrate_evidence_payload_column` 的補欄位手法、`executor/capability_signer.py` 對 `domain._checks`/`domain.proposal` 的引用方向)都對照過對應檔案,依賴方向、例外階層(`CapabilityError` 掛在既有 `PermanentError` 下,不是另立一套)、以及「憑證只證明範圍不證明業務決策」的分工都跟專案既有寫法一致,沒有發現跨層直呼或第二種做法。
