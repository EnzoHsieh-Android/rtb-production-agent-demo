severity: major

# 架構對齊審查:增量 4 設計(真的網路呼叫、最小決策規則與 trace)

範圍:只審 r4-snapshot.md 的「## 增量 4 設計:真的網路呼叫、最小決策規則與 trace」整段。對照 httpkit.py、sqlitekit.py、dsp/server.py、executor/inbox_server.py、tests/dsp/conftest.py、analyzer/flow.py、analyzer/task_store.py、analyzer/ruff.toml、CLAUDE.md「每支檔有家」。

### 1. 三個新檔案沒有指定落點(Systems 節點),違反「每支檔有家」的既有做法

severity: major
blocking: 是 CLAUDE.md 的「每支檔有家」是提交前機械擋新違規的鐵則(不是風格偏好),增量 1、2、3 在計劃裡都各自交代了落點,增量 4 整段完全沒交代,照現有做法會在提交時被擋下,屬於流程能不能推進的問題。
引句:「真的 DSP 用戶端(讀現況與指標)、真的收件口用戶端(送提案)」

增量 4 新增 `dsp_client.py`、`inbox_client.py`、`policy.py` 三支程式檔,但整段設計(191~236 行)從頭到尾沒有出現任何「落點」「lands_in」「圖譜」字樣,交代這三支檔要記進哪一篇 Systems 節點。對照同一份文件裡其他三個增量的做法:增量 1 在文末「## 落點」節有一條 `lands_in`(file: `governance/review-reports/rtb-phase2任務流程/r4-snapshot.md:260`);增量 3 也有一條(file: `governance/review-reports/rtb-phase2任務流程/r4-snapshot.md:261`);增量 2 雖然沒有寫進「## 落點」節,但在自己的段落內明講「圖譜:新增兩篇 Systems 節點…」(file: `governance/review-reports/rtb-phase2任務流程/r4-snapshot.md:93`)。三個增量都有落點交代,唯獨增量 4 沒有,不是巧合式的疏漏,是這一版設計漏掉的一步。

目前唯一管 `src/rtb/analyzer/` 的 Systems 節點「分析行程流程與檢查點」的 `about_code` 只列了 `task_store.py` 與 `flow.py`,沒有三個新檔案(file: `docs/rtb-production-agent-demo-knowledge/Systems/分析行程流程與檢查點.md:8`)。三支新檔要不要都併入這篇既有節點、還是需要各自另開,設計裡沒有講,實作時第一步就會撞上「這支檔沒有家」而被擋。這不是風格問題,是 CLAUDE.md 明文的提交閘(file: `CLAUDE.md:57`)。

### 2. 兩個新用戶端要不要抽共用基礎,設計完全沒有討論,跟增量 2 對同一類問題的處理方式不一致

severity: major
blocking: 是 專案已經有明文的價值判準(共用基礎、避免長出第二套),增量 3 對「跟既有做法不同」的地方都主動寫了理由,增量 4 在對稱情境下卻完全沒有討論,若照現狀實作,兩支用戶端很可能各自重複同一套「建請求、設逾時、解 JSON、分類例外、量測耗時」邏輯,而沒有人在設計階段判斷過這是不是該抽的「第二套」。
引句:「標準函式庫 urllib.request,trace 是既有 SQLite 表格模式的延伸,不引入新依賴」

httpkit.py 是專案對「兩個行程各打自己的伺服器,但共用一套 Host 檢查、逾時、JSON 錯誤回應」問題給的答案,而且增量 2 特別開一節「共用基礎(避免長出第二套)」把這個判斷寫清楚(file: `governance/review-reports/rtb-phase2任務流程/r4-snapshot.md:88`)。增量 4 是對稱的另一半:分析行程現在要打兩個不同的伺服器(DSP、收件口),兩支用戶端很可能都需要同一套東西——用 urllib 建請求、共用逾時值、把連線失敗/逾時跟「已定義的狀態碼」分開處理、還有下面第 3 點提到的 tool_calls 紀錄。設計段落(204~208 行)只分別描述兩支用戶端各自的行為,完全沒有一句話評估「這兩支要不要共用」,也沒有像增量 3 對 tasks 表跟 DSP/收件口做法不同那樣寫一段解釋為什麼刻意分開(對照 file: `governance/review-reports/rtb-phase2任務流程/r4-snapshot.md:140`,增量 3 對「為什麼不跟 DSP、收件口一樣」有完整說理)。同一份計劃對同一類判斷,一次有交代、一次沒有,這是設計本身的缺口,不是我猜測實作會怎麼寫。

DSP 伺服器與收件口伺服器目前都是「HTTP 處理」與「資料存取」分開兩層(`DspHandler` 只呼叫 `CampaignStore`,見 file: `src/rtb/dsp/server.py:83`;`InboxHandler` 只呼叫 `InboxStore`,見 file: `src/rtb/executor/inbox_server.py:79`)。如果兩支新用戶端各自把「建請求、判斷逾時、分類錯誤」都寫一遍,就是在既有的「共用基礎」慣例旁邊,長出第二種「怎麼打外部服務」的做法。

### 3. tool_calls 表由誰寫、在哪個模組寫,設計沒有交代,可能讓 HTTP 用戶端跨進資料存取層

severity: major
blocking: 是 tool_calls 是分析行程自己資料庫裡的表,但沒有指定寫入者,若最終落在 dsp_client.py / inbox_client.py 裡各自開連線寫入,會打破「HTTP 用戶端只管協定、資料存取集中在 Store 類別」這條專案目前每一處都遵守的邊界。
引句:「寫入這張表不跟狀態推進同一個交易(呼叫本身在交易外,失敗要能記下來,不能因為這筆記錄失敗而讓整個檢查點卡住)」

`tasks`、`evidence` 兩張表目前只有一個寫入入口——`TaskStore.commit_step`,連 `flow.py` 的 `advance()` 都不直接碰 SQL,一定經過 `TaskStore`(file: `src/rtb/analyzer/task_store.py:164`;flow.py 只呼叫 `store.commit_step(...)`,見 file: `src/rtb/analyzer/flow.py:121`)。收件口的事件表也是同一個模式:`InboxStore` 是唯一寫入者,`InboxHandler` 只呼叫 `store.record_event(...)`,自己不碰 SQL(file: `src/rtb/executor/inbox_server.py:87`)。

增量 4 的設計(210~213 行)只說「每次對外呼叫…一筆」「寫入這張表不跟狀態推進同一個交易」,但完全沒說這個寫入動作是在 `dsp_client.py`/`inbox_client.py` 裡直接做(意味著這兩支「用戶端」模組要同時擁有 SQLite 連線,變成同時橫跨 HTTP 協定層與資料存取層),還是由 `flow.py`/`TaskStore` 在呼叫 `EvidenceSource`/`Submit` 前後量測、統一寫入(維持現有「Store 是唯一寫入者」的邊界)。這兩種寫法一個延續既有邊界、一個打破,設計卷證沒有選邊,而 S50 這條合約(file: `governance/review-reports/rtb-phase2任務流程/r4-snapshot.md:225`)又明確要求「每次對外呼叫」都要留下紀錄,逼著實作在動手時才被迫做這個架構判斷——這正是架構對齊審查該在設計階段就攔下來的缺口。

### 4. 證據內容雜湊的「正規化」規則有沒有沿用既有的正規化慣例,設計沒有講

severity: minor
blocking: 否 只是設計文字沒有明講是否沿用既有慣例,不是已經寫出一份新規則;實作時延用 `proposal.py` 的既有做法就不會有問題,不構成需要卡關的架構違反。
引句:「`content_hash` 是回應內容正規化後的 SHA-256」

專案已經有一套明確定義過的「正規化後取 SHA-256」慣例:先轉成基本型別、時間一律轉 UTC、以固定鍵排序、無多餘空白、不允許 NaN 的 JSON 序列化(file: `src/rtb/domain/proposal.py:294`,增量 2 也用同一句話重述過一次,見 file: `governance/review-reports/rtb-phase2任務流程/r4-snapshot.md:71`)。增量 4 對 `dsp_client` 的證據內容雜湊只寫「回應內容正規化後的 SHA-256」,沒有講這個「正規化」是不是同一套規則(鍵排序、無空白、禁 NaN),也沒有講是否重用 `proposal.py` 現成的正規化邏輯。DSP 回應的是任意 JSON(不是 `Proposal` 物件),`proposal.py` 的 `content_hash` 函式簽章是 `Proposal -> str`,不能直接套用,所以這裡至少需要一句話講清楚是要抽一個通用的「正規化 JSON 再雜湊」小函式共用,還是為 `dsp_client` 另外定義一套規則。目前設計文字留白,實作時如果悄悄用了跟 `proposal.py` 不同的正規化規則(例如沒有排序鍵或允許不同的空白),就會出現兩套「正規化雜湊」的定義,但這在目前的設計文字裡還沒有發生,只是缺一句交代。
