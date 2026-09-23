severity: major

## F1 操作人格式驗證跨層直呼 domain 私有模組,沒有走既有「業務模組驗、CLI 接例外」的分工
severity: major
blocking: 是 — 引入第二種驗證做法且跨層直呼 domain 私有模組(`_checks`),破壞既有分工

引句:「操作人的格式由重放管理工具檢查(比照核可人由核可模組檢查、收件口只存;收件口模組不做欄位驗證)」
引句:「from rtb.domain._checks import is_id」

既有做法(`src/rtb/executor/approve.py`,同一批「管理工具」裡唯一的對照對象):CLI 本身不驗格式,
把 approver 傳給業務模組 `rtb.executor.approval.issue()`,由它內部呼叫 `is_id(approver)`
(`src/rtb/executor/approval.py:79`)、不合格式就丟 `ApprovalRefused`,CLI 只負責接這個業務例外
再印訊息、回退出碼(`approve.py` 的 `_sign()`:`except approval.ApprovalRefused as refused:`)。
這支 CLI 完全沒有 import `rtb.domain._checks`——全專案除了 domain 自己的模組與
executor/analyzer 的業務模組(`attempt_store.py`、`capability_signer.py`、`approval.py`、
`inbox_store.py`、`task_store.py`、`policy.py`…)之外,`replay.py` 是唯一一支「管理工具/CLI 入口」
直接 import 這個底線帶下劃線、屬於 domain 內部的 `_checks` 模組(`src/rtb/executor/replay.py:16`)。

`replay.py` 的做法反過來:CLI (`run()`)自己呼叫 `is_id(args.operator)`
(`src/rtb/executor/replay.py:39`)判斷格式,合格才呼叫 `store.replay(...)`;
`InboxStore.replay()` 本身完全不驗操作人格式,只是原樣存進稽核表(它的 docstring 也承認「收件口
模組不做欄位驗證」)。這等於把原本在 approve.py 這一側「業務模組驗、CLI 只接例外」的單一做法,
換成「CLI 直接下探 domain 私有原語自己判」的第二種做法——不是換了個等價寫法,是換了層:被驗證
的規則(合法識別字格式)原本只有 domain 私有模組與它的業務層呼叫者知道,現在多了一個 CLI 入口
自己也認得並直接呼叫這個私有模組。docstring 裡寫的「比照核可人由核可模組檢查」名不副實——比照的
是「有沒有驗格式」這件事本身,沒有比照「驗在哪一層」。

## F2 決策新鮮度重跑檢查用「guard 直接丟例外」繞過既有的「guard 回布林、由 `_write` 統一翻譯成例外」單一機制
severity: major
blocking: 是 — 同一個 `guard` 參數契約下,新增了第二種讓呼叫方拿到失敗原因的路徑

引句:「raise _DecisionStale(row.key)」

`_write()` 的 `guard` 參數型別是 `Callable[[attempt_store.ExecutorTransaction], bool] | None`
(`src/rtb/executor/execution.py:798` 附近),既有唯一機制是:guard 回 `False`,`_write` 自己在
同一處統一翻成 `raise _ApprovalSuperseded(row.key)`(`execution.py:813-814`,這段沒被這次改
動碰到,是既有、沿用到現在的翻譯點)——呼叫方（`_after_expiry`、`_reconcile_not_found`）永遠只
要在 `_write()` 外面接一種例外。這次在 `_in_flight_again` 的 `still_latest` 閉包裡新增了決策
新鮮度判斷,卻不是讓它回 `False` 交給 `_write` 翻譯,而是直接在閉包裡 `raise _DecisionStale(row.key)`
(`execution.py:641-643`),跳過 `_write` 那個唯一的翻譯點,自己開一條新的例外通道。結果同一個
`guard` 型別契約(宣告回 `bool`)底下,現在有兩種讓呼叫方知道「為什麼沒能繼續」的方式:一種是
回 `False` 給 `_write` 轉譯,一種是自己直接丟;呼叫端也因此要多接一種例外
(`except _DecisionStale:` 出現在 `execution.py:860` 與 `execution.py:1029` 兩處重跑路徑)。

以下是確認過沒問題、跟既有做法一致的觀察:`flow.py` 把單一 `_VERSION_CHANGED` 判斷擴成
`_REPLAN_ON_BLOCK` 字典查表,寫法跟同檔既有的 `_KNOWN_STATES`/`_BLOCK_CODES` 用凍結集合/映射
表描述「合法值→下一步」的慣例一致,沒有另開一套判斷邏輯。`guardrails.py` 新增的
`decision_stale(proposal, now)` 跟既有 `increase_too_large(proposal, budget)` 一樣是不碰
DSP、資料庫、設定檔的純判斷函式,常數 `DECISION_FRESHNESS` 的宣告方式、附使用者裁定日期的註解
都跟 `MAX_INCREASE_NUMERATOR` 等既有常數同一個寫法。`inbox_store.py` 的 `dead_letters`/
`dead_letter_ops` 兩張表,以及 `_record_dead_letter`/`_audit_dead_letter`(私有、不帶 `tx`
參數、假設呼叫端已經在交易裡)跟既有 `_finish`/`_settle_existing`/`_write_failure` 是同一種
「私有輔助、直接用 `self._conn`、假設外層已開交易」寫法;`replay()` 自己開
`immediate_transaction` 並把 `DatabaseBusy` 轉成 `InboxBusy`,跟既有 `add_approval()`/
`find_proposal()` 那一段「核可管理工具用:各自一個交易」的寫法一致,不是另開一套交易管理。
執行端 `_stale`/`_too_late` 的「先在 `_run()` 前段開一個獨立交易預判、再在 `_take()` 那個
真正開始一筆的交易裡用同一個 tx 重判一次」的兩段式,跟既有 `_approvals()`(預判)/`_superseded()`
(在 `_take` 的交易裡重判)是同一個既有模式,不是新做法。`replay.py` 的 argparse 寫法、
`EXIT_REFUSED`/`EXIT_BUSY` 退出碼、`try/except InboxBusy/finally: store.close()` 結構都
比照 `approve.py`,一致。

沒有查到筆記與程式碼在架構這個鏡頭下互相矛盾的地方;上面兩條發現都是程式碼內部(新程式碼互相之間
或新程式碼與同檔既有程式碼之間)的分工不一致,不涉及圖譜筆記與程式碼的取捨判準。
