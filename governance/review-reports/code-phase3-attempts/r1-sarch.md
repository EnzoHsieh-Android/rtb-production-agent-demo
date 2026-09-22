severity: minor

### 1. 非法轉換丟例外的責任沒有沿用 task_state 那條「網域層定義、store 層直接重用」的路
severity: minor
blocking: 否 理由:沒有跨層呼叫,也沒有違反「嘗試紀錄不自己開連線與交易」的設計審裁定,純粹是例外類別歸屬層級跟被指名對照的 task_store.commit_step 不同,不影響交易邊界、依賴方向或現有呼叫端行為。
引句:「def can_transition(current: object, target: object) -> bool:」

說明:`src/rtb/domain/task_state.py` 同時輸出 `can_transition`(布林判斷)與 `transition`(不合法時丟出網域層自己定義的 `IllegalTransition(ValueError)`);`src/rtb/analyzer/task_store.py` 的 `commit_step` 對非法轉換就是直接 `raise IllegalTransition(...)`——重用網域層那個例外,沒有在 store 層另開一個。`src/rtb/domain/attempt.py` 只給了 `can_transition`(上面引句那一行),沒有對應的 `transition()`,也沒有輸出任何可重用的網域例外;於是 `attempt_store.py` 自己定義了 `class IllegalAttemptTransition(AttemptRejected):`(file: `src/rtb/executor/attempt_store.py:65`),base 是純 `Exception` 而非 `ValueError`,在 `transition()`、`record_verification_timeout()`、`resolve()` 三處各自 `raise IllegalAttemptTransition(...)`。這個「store 層自建 `XxxRejected(Exception)` 階層」的做法跟同package的 `inbox_store.py`(`InboxRejected(Exception)` 一族)一致,所以不算引入全新架構;但跟被指名對照的 `task_store.commit_step`「不合法轉換是網域層的問題、store 直接重用網域例外」這條既有路徑不同,值得記一筆備查,不算擋門檻的問題。

其餘逐項已查過,沒有發現:
- 分層與依賴方向:`attempt_store.py` 只匯入 `rtb.domain.attempt`、`rtb.domain.proposal` 與 `sqlite3`(僅作型別標註),不匯入 `rtb.sqlitekit`,也沒有 `connect`/`begin_immediate`/`immediate_transaction`/`BEGIN`/`COMMIT` 字樣——凍結 patch 裡新增的 `tests/executor/test_attempt_store.py::test_the_attempt_store_neither_reaches_the_network_nor_opens_its_own_connection` 用 AST 解析原始碼機械擋住這點,跟計劃裁定(嘗試紀錄不自己開連線與交易)一致;`inbox_store.py` 新增的 `transaction()` 是唯一交易入口,内部沿用既有 `immediate_transaction`/`DatabaseBusy → InboxBusy` 轉譯,跟既有 `accept()` 的做法一致。
- 回傳 None 還是丟例外:`_current()` 在序號不是最新時回 `None`(不寫入),被 `transition`/`record_verification_timeout`/`resolve` 共用,語意上對應 `task_store.commit_step` 序號不符時回 `False` 的既有做法(型別不同但語意一致,屬合理擴充,非二套做法);`begin()` 對「同鍵已存在」回傳 `Begun(created=False)`、對「廣告被鎖」「全表滿」丟例外,直接對應 `inbox_store.accept()` 既有的「內容相同就回放、內容衝突或超限就丟例外」設計。
- 自造既有工具函式:`operation_key()` 的正規化 JSON(`sort_keys`、`separators`、`ensure_ascii`、`allow_nan=False`)跟 `proposal.content_hash()` 手法相同,是重複既有那套做法而非另立新法;`_iso()` 在 `task_store.py`、`inbox_store.py` 已經各自私有定義一份,`attempt_store.py` 照樣各自定義,屬既有(雖不理想但一致)的慣例延續,不是新增的分歧;`snapshot()` 用網域層 `parse_proposal()` 重建提案(而非像 `task_store._proposal_from_json` 直接建構繞過驗證入口),更貼近重用既有安全入口,沒有另造還原邏輯。
- 領域層匯入白名單:`attempt.py` 只用 `hashlib`、`json`、`enum.StrEnum`、`types.MappingProxyType`、`rtb.domain.proposal`,全在既有 `PURE_STDLIB_ALLOWLIST` 或網域內部匯入範圍,`tests/domain/test_metrics.py` 的遞迴掃描測試不需改動就能涵蓋到新檔案。
