severity: major

# 架構對齊審查:增量 3 設計(分析行程的流程與檢查點)

審材:`governance/review-reports/rtb-phase2任務流程/r2-snapshot.md` 的「## 增量 3 設計:分析行程的流程與檢查點」整段(第 125-174 行)。
對照:`src/rtb/dsp/`、`src/rtb/executor/`、`src/rtb/domain/`、`src/rtb/httpkit.py`、`src/rtb/sqlitekit.py`、CLAUDE.md、`docs/rtb-production-agent-demo-knowledge/Systems/*.md`。

## 整體判斷(先講結論)

三個可替換介面用「函式型別」表示(`(task) -> ...`),這點跟 dsp/executor 既有用 `Callable` 當建構參數的做法一致,沒有問題。真正跟既有做法分岔的是兩處:`Submit` 介面把 dsp/executor 一律用例外階層表達的「失敗」改寫成純回傳值(找到第 1 條);`tasks` 歷史表讓只增的表自己兼任「現況」來源,跟 dsp、executor 兩表分工的做法不同(第 2 條)。另外增量 3 沒交代新套件 `src/rtb/analyzer/` 未來的 Systems 落點(第 3 條,不阻塞)。

---

### 1. Submit 介面把執行行程的例外語意改寫成純結果型別

dsp(`store.py`)與 executor(`inbox_store.py`)對失敗一律走「例外階層」:dsp 有 `TransientError`/`PermanentError` 兩層基底,executor 有 `InboxRejected`/`InboxBusy`;成功才回傳資料類別(`OperationResult`、`Accepted`),失敗一律丟例外,由伺服器層的 `map_exception` 對照表轉成 HTTP 回應。這是全專案唯一一套「失敗語意」的表達方式。

增量 3 的 `Submit` 介面卻把收件口原本用例外表示的「忙碌可重試」(`InboxBusy`)、「過期或衝突」(`ProposalExpired`/`ContentConflict` 等)直接收進一個三選一回傳值 `Accepted | Stale | Busy`,完全不丟例外。同一批介面裡的 `EvidenceSource` 卻只給了成功時的回傳型別 `tuple[Evidence, ...]`,沒有對稱地說明失敗要用例外還是也走結果型別([S25] 只講「呼叫失敗」,沒定型別)。這批新介面的錯誤處理風格因此變得自成一套、也不統一,跟 dsp/executor 已經一致採用的「例外階層 + 對照表」不一樣。

severity: major
blocking: 是 這是在既有兩個行程都用「例外階層」表達失敗的地方,新長出第三種(純回傳值、零例外)的錯誤處理風格,屬於「第二種做法」。
引句:「對應收件口的三類結果」

參考:
- file: `src/rtb/dsp/errors.py:8-21`(DspError/TransientError/PermanentError 階層)
- file: `src/rtb/executor/inbox_store.py:43-93`(InboxRejected/InboxBusy 階層)
- file: `src/rtb/executor/inbox_store.py:96-103`(Accepted 只用在成功路徑,失敗一律例外)

---

### 2. tasks 表用只增日誌本身當現況來源,跟兩個既有行程的兩表分工不同

dsp 用兩張表分工:可變的 `campaigns` 存現況(用 `UPDATE` 就地改)、只增的 `operations` 存歷史。executor 用一張會 `UPDATE` 的 `proposals` 表(狀態就地轉成 `superseded`/`expired`)加一張有界、會 `DELETE` 輪替的 `inbox_events`。兩邊都有「一張表被 `UPDATE` 或 `DELETE`」。

增量 3 的 `tasks` 表反過來:[S36] 明講歷史表「不得出現 UPDATE 或 DELETE 敘述」,現況直接靠「序號最大的那一列」查出來,沒有另一張可變的現況列。這是專案裡第一次讓「只增的歷史表」本身兼任現況查詢的依據。設計文字只解釋了這樣做對「檢查點=這一列已安全寫入」有什麼好處,沒有對照或說明為什麼不沿用 dsp/executor 已經在用、而且同樣能達成「寫入即檢查點」效果的「可變現況列 + 只增歷史表」兩表分工。

severity: major
blocking: 是 這是在既有兩個行程都用「可變現況列 + 只增歷史」兩表分工的地方,新長出第三種(單表、零 UPDATE/DELETE、現況靠查詢)的持久化做法,屬於「第二種做法」,且設計文字沒有交代為何不沿用既有分工。
引句:「每次狀態推進都是新增一列,不更新既有列」

參考:
- file: `src/rtb/dsp/store.py:292-295`(UPDATE campaigns,現況用可變列)
- file: `src/rtb/dsp/store.py:296-312`(INSERT operations,history 表從不 UPDATE/DELETE,但不是現況來源)
- file: `src/rtb/executor/inbox_store.py:180-183`(UPDATE proposals SET state = 'expired')
- file: `src/rtb/executor/inbox_store.py:197-201`(UPDATE proposals SET state = 'superseded')
- file: `src/rtb/executor/inbox_store.py:223-227`(DELETE proposals,_purge_finished_tasks)

---

### 3. 分析行程套件沒有指定落點(lands_in)

計劃檔頭的 `lands_in` 只列了「任務流程領域模型」「共用行程基礎」「提案收件口」三篇;對照這三篇 Systems 節點的 `responsibility`/`about_code`,沒有一篇管 `src/rtb/analyzer/` 底下未來的檔案。正文的「## 落點」一節也只交代增量 1(任務流程領域模型),沒提到增量 3 新增的分析行程套件將來要有哪篇 Systems 節點當家。CLAUDE.md 的「每支檔有家」要求計劃寫 `lands_in`,增量 2 的「共用基礎」一節也明確示範了這個做法(當時直接寫「新增兩篇 Systems 節點」)。增量 3 提到要新開 `src/rtb/analyzer/` 這個第三個行程套件,卻沒有比照辦理。

severity: minor
blocking: 否 沒有引入新的技術做法或跨層直呼,只是遺漏「新模組要交代落點」這個既有慣例,補齊即可,不影響設計本身的架構是否成立。
引句:「分析行程套件(`src/rtb/analyzer/`)不得匯入 `rtb.dsp` 或 `rtb.executor`」

參考:
- file: `governance/review-reports/rtb-phase2任務流程/r2-snapshot.md:9-12`(lands_in 列表,未含 analyzer)
- file: `governance/review-reports/rtb-phase2任務流程/r2-snapshot.md:193-195`(## 落點只提增量 1)
- file: `docs/rtb-production-agent-demo-knowledge/Systems/任務流程領域模型.md:6-12`(responsibility/about_code 不含 analyzer)
- file: `docs/rtb-production-agent-demo-knowledge/Systems/共用行程基礎.md:6,9-10`(responsibility/about_code 不含 analyzer)
- file: `docs/rtb-production-agent-demo-knowledge/Systems/提案收件口.md:6,9-10`(responsibility/about_code 不含 analyzer)

---

## 沒有問題的部分(順手記下,避免下一輪重查)

- 三個介面用函式型別(`Callable`/`(x) -> y` 形式)表示,跟 dsp `CampaignStore.__init__` 的 `clock: Callable[[], str]`、executor `InboxStore.accept` 的 `clock`/`before_commit` 同一種依賴注入風格,沒有另長出 Protocol 或抽象基類的第二套做法。
- `advance()` 的狀態轉換一律透過 `task_state.transition()` 計算、非法轉換丟 `IllegalTransition`,直接重用領域層既有的例外與狀態機,沒有另刻一份轉換邏輯。
- `tasks`/`evidence` 的寫入沿用 `sqlitekit.immediate_transaction`,沒有另寫一套交易樣板。
- `src/rtb/analyzer/` 不得匯入 `rtb.dsp`/`rtb.executor` 並比照加 ruff 設定,跟 `src/rtb/dsp/ruff.toml`、`src/rtb/executor/ruff.toml` 既有的互不匯入規則對稱。
- 增量 3 的介面全是測試假物件,真的 DSP/收件口用戶端留到增量 4,這一版沒有跨層直呼。
