severity: major

# r1 架構對齊席(carch)報告

基準:設計計劃「程式結構原則」、CLAUDE.md、pyproject.toml 的 ruff 規則。專案無既有 Python,無「第二種做法」可對照既有程式,只對照計劃原則與本 diff 內部一致性。
實測:`ruff check src tests` 全過,`pytest tests -q` 25 passed。

## 分層總評
- store.py 沒有 HTTP、沒有狀態碼、沒有錯誤對應;只把 sqlite3.OperationalError 轉成 StoreBusy(儲存層自己的型別)。依賴方向正確。
- server.py 只做路由、標頭解析、錯誤對應與故障注入;業務判斷(版本比對、冪等、驗證)都在 store。
- errors.py 只放錯誤類別。
- 資料庫沒有預先切介面,DSP 客戶端介面本輪未出現(agent 端尚未實作),與「DSP 客戶端切介面、資料庫不預先切介面」一致。
- 函式長度與複雜度都在 ruff 上限內,最長的 `_dispatch`、`execute` 職責單一。

## findings

severity: major
blocking: 是 同一個「可重試與否、對應狀態碼」在 server.py 有兩條互不相通的做法,且 errors.py 的型別階層沒被用到,會讓新增錯誤型別時靜默降成 500。
- 位置:server.py 的 ERROR_TABLE、`_dispatch`、RequestRejected;errors.py 的 TransientError / PermanentError。
- 問題:錯誤對應有兩條路。DspError 走 ERROR_TABLE(以 `type(exc)` 精確查表),請求層問題與故障注入走 RequestRejected(自帶 status、code、retryable)。而「可重試」這件事同時存在於 errors.py 的型別階層(TransientError 就是可重試)與 ERROR_TABLE 的布林欄,兩處要同步。server 從不用 isinstance 判 TransientError,所以階層等於裝飾。
- 引句:「status, code, retryable = ERROR_TABLE.get(type(exc), (500, "dsp_error", False))」
- 重現(已跑):建 `class Sub(StoreBusy)` 或直接查 `TransientError`,`ERROR_TABLE.get(...)` 都回 fallback:
  - 指令:`/private/tmp/claude-501/-Users-enzo/36c33b75-fd17-4bff-aa49-20f97a550e58/scratchpad/exp.py`(以 .venv/bin/python 執行)
  - 輸出:`FALLBACK-500`、`FALLBACK-500`。也就是任何 StoreBusy 子類或將來新增的 TransientError 子類,被丟出時會回 500、retryable=false,和 errors.py 文件字串「暫時性錯誤:同一把冪等鍵可以安全重試」相反,呼叫端會把可安全重試的錯當永久錯。
- 建議:retryable 由 `isinstance(exc, TransientError)` 導出,狀態碼用沿 MRO 查表;RequestRejected 併進同一張表或同一套型別。

severity: minor
blocking: 否 命名與語意小瑕疵,不改變安全性。
- 位置:store.py 的 `get_operation_by_key` 與 `_replayed_result`。
- 問題:純查詢也回 `replayed=True`,因為 `_replayed_result` 寫死 True。GET /operations/{key} 的回應因此永遠說「這是重放」,命名不說明意圖。
- 引句:「return None if row is None else OperationResult(*row, replayed=True)」
- 重現(已跑):exp.py 最後一行,execute 一次後 `get_operation_by_key("k")` 輸出 `replayed=True`,但該操作從未被重放。

severity: minor
blocking: 否 純判斷放在儲存模組內,尚未違反依賴方向,但與「一個模組一件事」有落差。
- 位置:store.py 的 `_validate`、`_next_state`、`Operation.fingerprint`。
- 問題:計劃要求「純判斷和外部動作分開」。這幾個是純函式(可不碰資料庫測),很好,但與 SQL、連線管理、資料類別同放 store.py(約 200 行),模組同時承擔領域規則與持久化。Mock DSP 屬外部系統,目前可接受;若之後 DSP 動作增加(不只預算與暫停),狀態轉換不是計劃說的「狀態機是資料」的表,而是 if 分支。
- 引句:「elif op.action != "pause_campaign":」
- 建議:動作種類增加時把 `_validate` 與 `_next_state` 抽成動作表(動作 -> 驗證、轉換),放到獨立模組。本輪不必動。

severity: minor
blocking: 否 server 內混入少量請求成形規則。
- 位置:server.py 的 `_operation`。
- 問題:請求 body 轉成 Operation 時在 server 裡依 action 決定 params 保留哪些欄位,並把缺少的 expected_version 預設成 0。「哪些欄位屬於哪個動作」是動作規則,與 store 的 `_validate` 各持一半;預設 0 會把「沒帶版本」轉成 VersionConflict 而非驗證錯誤。
- 引句:「return Operation(campaign_id, action, params, body.get("expected_version", 0), key)」
- 建議:params 成形歸到與 `_validate` 同一處;缺 expected_version 明確回 400。

severity: minor
blocking: 否 測試耦合私有成員與已失效的 noqa。
- 位置:tests/dsp/test_store.py。
- 問題:失敗回滾測試用 `monkeypatch.setattr(store, "_record_idempotency", ...)` 綁死私有方法名,重構就紅;`# noqa: BLE001` 但 pyproject 的 select 沒開 BLE,是無效標記。
- 引句:「monkeypatch.setattr(store, "_record_idempotency", explode)」
- 建議:改用會在同一交易內失敗的公開手段(例如重複的 operation 觸發 UNIQUE 違反)或保留但註明為刻意白箱;移除無效 noqa。

severity: minor
blocking: 否 規則設定與計劃宣稱不符,但目前沒有領域層可套用。
- 位置:pyproject.toml 的 `select` 含 "TID",但沒有 `[tool.ruff.lint.flake8-tidy-imports]` 的 banned-api。
- 問題:計劃說「依賴方向往內由 ruff 禁用匯入規則檢查」。現在只選了 TID 卻沒有禁用清單,等於空轉;未來領域層出現時若沒補清單,就沒有機械守衛。
- 引句:「select = ["E", "F", "I", "C90", "PLR0911", "PLR0912", "PLR0913", "PLR0915", "TID"]」
- 建議:領域層建立那一輪同時加 banned-api(sqlite3、socket、http.client、urllib 等),並註記 REVISIT。

## 逐檔
- errors.py:除上述型別階層未被 server 使用外,已讀,無其他 finding。
- store.py:見上;無 HTTP 或錯誤對應混入,已讀,無跨層 finding。
- server.py:見上;無業務規則跨層(僅 params 成形小項)。
- tests/dsp/conftest.py:行程管理類別與 fixture 同檔,測試檔只用 `start_dsp` 與 `dsp.request`,分層合理。已讀,無 finding。
- tests/dsp/test_server.py:已讀,無 finding。
- tests/dsp/test_store.py:見上(私有成員與 noqa)。
- .gitignore、.lumos/*、requirements-dev.txt、Mock-DSP.md:已讀,無 finding。

最嚴重等級 major,blocking 共 1 條。
