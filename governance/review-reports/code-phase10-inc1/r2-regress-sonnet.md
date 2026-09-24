severity: major
以下為完整報告全文(依 p10-fmt.md 格式,將原樣存檔):

severity: major

## 收貨方式
讀 `governance/review-reports/code-phase10-inc1/r1-intake.md`、三份 r1 報告、`r2-delta.patch`(bfd3cfd..61947ef 的 src 與 tests),逐條對照現在的 `src/rtb/domain/_checks.py`、`src/rtb/domain/worth.py`、`src/rtb/analyzer/policy.py`、`src/rtb/analyzer/dsp_client.py`、`src/rtb/domain/proposal.py`。另外在 `/tmp/p10i1-probe` 用 `git archive bfd3cfd` 拉出修正前的完整 src 樹,寫探針腳本逐欄比對白名單行為、逐項測 `ValidatedCells` 私有哨兵能不能被繞過(不動這個工作樹)。全套測試:`PYTHONPATH=src /Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest`,**1782 passed**,無紅燈(比 r1 報告的 1825 少,查過是 `tests/analyzer/test_worth_check.py` 的 [S715] 測試把「6 欄 × 9 個壞值」的 parametrize 組合改成「6 欄 × 迴圈跑 `EDGE_VALUES`」的單一測試函式,collected 項目數本來就會變少,不是漏測)。

## 逐條驗收(第 1 輪 6 條,合併成 4 件事)

**correct-1 / x1-1 / arch-1(同一件:六欄共用一條判準,跟 DSP 白名單逐欄不同;且靠比較副作用擋 NaN/Inf)—— 已修好。**
`_checks.py` 新增 `is_int_between`(`src/rtb/domain/_checks.py:27`)、`is_count_or_none`(`:32`)、`is_finite_or_none`(`:37`,明確呼叫 `math.isfinite`,不再靠「跟上限比較、NaN/Inf 比較恆假」的副作用)。`dsp_client.py` 的 `STATE_FIELDS`/`METRICS_FIELDS`(`src/rtb/analyzer/dsp_client.py:54-67`)與 `worth.py` 的 `WorthInput.__post_init__`(`src/rtb/domain/worth.py:53-66`)現在逐欄呼叫**同一支**函式(budget 用 `is_int_between(0, …)`、曝光/點擊/轉換用 `is_count_or_none`、花費/營收用 `is_finite_or_none`),不是各自維護的第二套判準。用探針把 bfd3cfd 版與現版的 `STATE_FIELDS`/`METRICS_FIELDS` 對 25 組邊界值(含 `1e20`、`MAX_INT±1`、`10**400`、`nan`/`inf`、布林、字串)逐欄跑過,**兩版白名單的 accept/reject 結果完全一致**——這支檔本身的既有行為沒有被這次重構動到。

**arch-2(`explain()` 的 `timeout_seconds` 有預設值 0.0,牴觸「逾時沒有預設值」的既有規則)—— 已修好。**
`explain()` 簽名拿掉 `candidate`/`allowed` 的預設值(`src/rtb/analyzer/policy.py:193`),逾時改包進新的 `CandidateCall(judge, timeout_seconds)`(`:98-101`),同樣沒有預設值。`decide()` 明確傳 `candidate=None, allowed=ValidatedCells.NONE`(`:242`)。全 `src/` 搜過,`policy.explain`/`policy.route` 生產環境唯一呼叫端就是 `decide()` 自己,沒有其他地方會踩到舊的隱含 0.0 逾時。

**correct-2(`except Exception` 連 `MemoryError`、`RecursionError` 也吞,minor)—— 已修好。**
`_candidate_answer()` 現在在 `except TimeoutError` 之前先攔 `except (MemoryError, RecursionError): raise`(`src/rtb/analyzer/policy.py:148-149`),原封不動往外丟,不會落進下面的 `except Exception`。有專屬測試 `test_fatal_errors_from_the_candidate_are_not_swallowed` 覆蓋。

**x1-2(`ValidatedCells` 是公開資料類別,任何呼叫者可直接建構非空清單)—— 未完全修好,見下方發現 1。**
直接 `ValidatedCells(frozenset({...}))` 確實已被擋(探針實測會丟 `ValueError`),但防線本身有漏洞,見下。

## 找新回歸

- **DSP 白名單行為**:如上,25 組邊界值 × 兩個欄位集合逐一比對 bfd3cfd 版與現版,無差異。
- **`MAX_INT` 搬家**:定義搬到 `src/rtb/domain/_checks.py:14`,`proposal.py` 用 `from rtb.domain._checks import MAX_INT as MAX_INT` 原樣重新匯出(`src/rtb/domain/proposal.py:22`),匯入方向是 `_checks` ← `proposal`/`dsp_client`/`worth`,沒有回頭匯入 `_checks`,沒有循環匯入(全套測試與獨立 import 探針都順利跑完)。
- **`CandidateCall`/必填關鍵字改動後 `decide()` 對外行為([S700])**:`decide()` 呼叫方式沒變,`_judge`/`route` 在「沒有候選」路徑的邏輯原封不動,frozen-policy 回歸測試(`test_extracting_the_worth_increase_check_keeps_every_decision_identical`)全綠,外部行為不變。
- **`MemoryError`/`RecursionError` 往外丟**:確認正確,見上。
- **`ValidatedCells` 私有哨兵能不能被無意中繞過**:**可以**,見下方發現 1。

### 1. `ValidatedCells` 的私有哨兵可以用 `dataclasses.replace` 繞過,拿到未經核准的候選清單
severity: major
blocking: 是
引句:「if self.cells and self._issuer is not _ISSUED:」
file: `src/rtb/analyzer/policy.py:86`
file: `src/rtb/analyzer/policy.py:81`
file: `src/rtb/analyzer/policy.py:93-95`

觸發情境:任何呼叫者手上有一個**合法簽發**的非空 `ValidatedCells`(例如增量 2 的採用函式核准了 `{PAUSED}` 這一格),然後用 Python 標準庫的 `dataclasses.replace(validated, cells=frozenset(WorthCell))` 想「在既有物件上加一格」或做其他調整。

會出什麼錯的行為:`__post_init__` 的守門只檢查 `self._issuer is not _ISSUED`(`policy.py:86`),不檢查 `cells` 本身是不是跟簽發時一致。`_issuer` 欄位(`policy.py:81`)沒有指定 `init=False`,`dataclasses.replace()` 在沒有明講要換掉它時會沿用原物件的欄位值——也就是說,只要原物件的 `_issuer` 已經是 `_ISSUED`,`replace()` 換掉 `cells` 之後 `_issuer` 照樣是 `_ISSUED`,守門條件仍然成立,新物件**直接放行**,完全不會再經過 `_issue_validated_cells`(`policy.py:93-95`)。我在 `/tmp/p10i1-probe/validated_cells_bypass.py` 實測:先用 `policy._issue_validated_cells(frozenset({WorthCell.PAUSED}))` 拿到一個只核准 `PAUSED` 的合法物件,再用 `dataclasses.replace(legit, cells=frozenset(WorthCell))`,拿到的新物件 `cells` 是全部 4 格,沒有丟任何例外。相對地,直接對 `ValidatedCells.NONE`(`_issuer=None`)做同樣的 `replace` 會正確被擋(因為 `_issuer` 不是 `_ISSUED`),說明這條防線只防得住「從空的/未簽發的物件憑空造出非空清單」,防不住「拿一個已簽發的物件去偷換它的 `cells`」。這正好是 x1-2 原本要防的事——增量 2 接上真候選後,任何拿得到一個合法 `ValidatedCells`(哪怕只核准了一格)的呼叫端,都能用一行標準庫呼叫把它偷換成全部格,讓候選在完全沒被實測過的評分格上被呼叫、結果又不會退回現行規則。

建議修法:讓 `_issuer` 不參與 `dataclasses.replace()` 的「沿用」語意——最直接是把 `__post_init__` 的檢查改成同時比對簽發時記錄的 `cells` 內容本身(例如簽發時把 `cells` 的雜湊或凍結副本一併存進 `_issuer`,`__post_init__` 檢查 `hash(self.cells) == 綁定在 self._issuer 上的值`),或者乾脆不要用「一個物件帶著哨兵」這種能被 `replace` 偷換欄位的模式,改成建構後就把整個物件登記進模組層級的一個 `WeakSet`/身分集合,凡是不是這個集合成員的 `ValidatedCells` 一律視為未驗證(`route()`/`explain()` 呼叫時額外查一次這個集合,而不是只看物件自己的欄位)。

## 測試
`cd /Users/enzo/rtb-p10i1 && PYTHONPATH=src /Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest` → **1782 passed**,無紅燈。工作樹全程未修改,實驗一律在 `/tmp/p10i1-probe` 進行。
