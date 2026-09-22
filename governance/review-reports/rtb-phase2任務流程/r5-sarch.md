severity: major

# 架構對齊審查(第 2 輪):增量 4 設計

範圍:只審 r5-snapshot.md 的「## 增量 4 設計:真的網路呼叫、最小決策規則與 trace」整段,承接 r4-sarch.md 第 1 輪的三條 major 已在這一版折入(落點、共用用戶端基礎、tool_calls 寫入者由 `instrumented.py` 統一經 `TaskStore` 寫入)。本輪對照重點:`httpclient.py` 的 `ClientHeader` 封閉列舉、`instrumented.py` 包裝層與 `flow.py` 既有 `Protocol` 介面設計、`SubmitRejectedPermanently` 的放置層。

本輪確認沒問題、不重複列成 finding:`ClientHeader(StrEnum)` 是沿用 `src/rtb/domain/*.py`(`TaskState`、`ActionType`、`EvidenceKind` 等,見 file: `src/rtb/domain/task_state.py:11`、file: `src/rtb/domain/proposal.py:36`)已建立的「型別檢查器可核對的封閉字串集合」慣例,用途是限制*自己程式碼*能傳入的標頭名稱(函式簽章逼你填),跟 `httpkit.py`/`executor/inbox_store.py` 的 `frozenset[str]`(見 file: `src/rtb/httpkit.py:191`、file: `src/rtb/executor/inbox_store.py:27`)是不同問題(那些是驗證外部不可信字串),兩者用不同工具不構成第二套做法。`instrumented.py` 包裝類別(`InstrumentedEvidenceSource`/`InstrumentedSubmit`)以 `__call__` 滿足 `EvidenceSource`/`Submit` 這兩個 `typing.Protocol`,跟 `flow.py` docstring 說明的「用 `Protocol` 是為了讓型別檢查器核對實作簽章」(file: `src/rtb/analyzer/flow.py:12`)完全相容——結構化型別本來就同時接受函式與帶 `__call__` 的類別,不衝突。

### 1. `SubmitRejectedPermanently` 定義在哪一層沒有交代清楚,而且兩種可能的答案中有一種會反轉既有的介面依賴方向

severity: major
blocking: 是 這不是文字沒寫全的小事:兩種讀法一種延續既有做法、一種會讓 `flow.py`(定義 `Submit` 這個 `Protocol` 的介面層)反過來 import 一個具體用戶端實作,破壞現有「介面不依賴實作」的方向,這正是架構對齊審查該在設計階段攔下的判斷,不能留給實作者自己選。
引句:「這是這個增量唯一需要修改增量 3 既有程式碼(flow.py)的地方,原因寫清楚,不是悄悄擴權」

既有做法:`Submit` 這個 `Protocol` 連同它目前僅有的兩個例外 `SubmitStale`、`SubmitBusy` 一起定義在 `src/rtb/analyzer/flow.py`(`class Submit(Protocol)` 見 file: `src/rtb/analyzer/flow.py:72`;兩個例外定義見 file: `src/rtb/analyzer/flow.py:64` 與 file: `src/rtb/analyzer/flow.py:68`)——介面與它的例外詞彙表放在同一支檔,`flow.py` 完全不 import 任何具體用戶端(目前也還沒有 `dsp_client.py`/`inbox_client.py` 可 import)。`_from_proposed` 直接用 `except SubmitStale:` / `except SubmitBusy:` 捕捉這兩個同檔定義的例外(file: `src/rtb/analyzer/flow.py:167-169`)。

增量 4 設計文字(191~263 行)對 `SubmitRejectedPermanently` 這個新例外只交代了「誰用它」跟「誰處理它」,沒交代「誰定義它」:
- `inbox_client.py` 那條只說它是「新增的 `SubmitRejectedPermanently`(見下)」(file: `governance/review-reports/rtb-phase2任務流程/r5-snapshot.md:213`),讀起來像是在 `inbox_client.py` 這支新檔裡新增。
- 「收件口拒絕代碼的分類」那節說 `flow.py` 的 `_from_proposed` 要新增一個處理分支,並且強調「這是這個增量唯一需要修改增量 3 既有程式碼(`flow.py`)的地方」(file: `governance/review-reports/rtb-phase2任務流程/r5-snapshot.md:231`)。

問題在於這兩句話合起來邏輯不自洽:如果例外類別本身也定義在 `flow.py`(跟 `SubmitStale`、`SubmitBusy` 同檔,延續既有的「介面與它的例外詞彙表同檔」慣例),那麼增量 4 對 `flow.py` 其實動了兩處(新增例外類別 + 新增 `except` 分支),跟「唯一…的地方」這句話字面上對不上。但如果反過來把例外類別定義在 `inbox_client.py`(照 213 行的讀法),`flow.py` 的 `_from_proposed` 要 `except SubmitRejectedPermanently:` 就必須 `import` 這個具體的收件口用戶端模組——這會讓定義 `Submit` 這個 `Protocol` 的介面層,反過來依賴它自己其中一種實作,跟現在「`flow.py` 對任何用戶端零依賴,靠 `Protocol` 結構化型別解耦」的既有設計方向相反,也會讓 `SubmitStale`/`SubmitBusy` 兩個例外在 `flow.py`、`SubmitRejectedPermanently` 一個在 `inbox_client.py`,同一個 `Submit` 協定的例外詞彙表被拆成兩處,沒有人在設計裡解釋過為什麼要拆開。

兩種讀法只有前者(定義在 `flow.py`)延續既有慣例,但設計文字目前的用詞(「唯一需要修改…的地方」)更像在暗示後者。這個岔路沒有被設計文字明確排除,應該在這輪補一句話講清楚:`SubmitRejectedPermanently` 跟 `SubmitStale`、`SubmitBusy` 一樣定義在 `flow.py`,`inbox_client.py` 只負責 `raise` 它(不定義它),`_from_proposed` 新增的 `except` 分支才是這個增量對 `flow.py` 的改動。
