severity: clean

本輪只判「第 1 輪折入之後,設計有沒有照既有模組邊界與做法走、有沒有引入第二種做法」,鏡頭限定在五處:信封流水編號與去重、重放條件與待核可對照、操作人格式檢查、重跑路徑保留新原因、分析端接進「現在」的簽章慣例。逐一對照 repo(/Users/enzo/rtb-3b)程式碼後,五處都是照既有做法延伸或明確引用既有函式的行為,沒有發現引入第二種做法或跨層直呼。以下是逐項核對記錄(非 blocking 觀察)。

**1. 信封流水編號與「不去重、另寫一列」**

引句:「兩次死信的內容雜湊、冪等鍵、投遞次數都一樣,只靠流水編號分得開」

這點乍看像是跟既有「只增不改」表的慣例分岔:`write_stops`(`file: src/rtb/executor/inbox_store.py:167-172,681-698`)與 `approval_uses`(`file: src/rtb/executor/inbox_store.py:182-187`)兩張現有的只增不改表都用 `UNIQUE` 欄位組 + `INSERT OR IGNORE` 做內容去重(`record_stop` 明白寫「同一份提案同一種類只記一列」,`file: src/rtb/executor/inbox_store.py:684-698`)。但 repo 裡還有第三種既有先例可對照:`attempts` 表用 `PRIMARY KEY (key, seq)`(`file: src/rtb/executor/attempt_store.py:46-52`),同一把鍵可以有很多列,靠 `seq` 分開、不做內容去重,查詢用 `WHERE key = ? AND seq = 1` 之類的方式找特定一列(`file: src/rtb/executor/inbox_store.py:731` 的 `attempts f WHERE f.task_id = p.task_id AND f.seq = 1`)。信封表的「同一份提案多次死信、靠流水編號分列、不設唯一限制」跟 `attempts` 這種「身分 + 序號、允許多列」的既有模式是同一種做法,不是新發明的第三種;而且是不是該去重這件事,r1 審計已經處理過(審計修正紀錄:「信封表有流水編號、重放後再死信另寫一列」),本輪沒有新的落差。

**2. 重放條件「同任務沒有更新的修訂」與同交易條件式寫入對照待核可處理**

引句:「查條件與寫回在同一個交易裡做(收件口既有的條件式單列寫入,跟待核可放回同一種做法)」

核對 `settle_awaiting`(`file: src/rtb/executor/inbox_store.py:745-765`):它用 `UPDATE ... WHERE task_id = ? AND revision = ? AND content_hash = ? AND {AWAITING}` 這種「先讀後寫」在同一條 SQL 裡完成、靠 `cursor.rowcount == 1` 判斷有沒有搶到,兩個工作者同時處理只有一個成功,跟計劃描述的重放交易寫法一致。

引句:「分析端已改送新修訂時,放回舊的會讓已被取代的舊決策插隊寫進 DSP」

這條規則直接對得上執行端既有的待核可放回邏輯:`file: src/rtb/executor/execution.py:536-537` 的 `elif self.store.has_newer_revision(tx, proposal.task_id, proposal.revision): outcome = AwaitingOutcome.SUPERSEDED  # 分析端已改送新修訂:舊的放回會插隊`,連程式註解的措辭都與計劃文字幾乎一致。計劃也點名沿用同一支 `has_newer_revision`(`file: src/rtb/executor/inbox_store.py:739-743`),沒有另開一套判斷邏輯。

**3. 操作人格式檢查對照核可工具**

引句:「操作人照核可工具驗核可人的同一支識別格式檢查,不合格式就拒絕、不寫稽核」

核可工具 `approval.issue` 用 `is_id(approver)` 檢查核可人格式(`file: src/rtb/executor/approval.py:79-80`),`is_id` 定義在共用模組 `file: src/rtb/domain/_checks.py:25-26`。計劃描述的「操作人照同一支識別格式檢查」就是指重用這支 `is_id`,沒有另外定義一套格式規則,是同一層級共用模組的延伸使用,不是跨層直呼。

**4. 重跑路徑保留新原因對照既有把版本已變保留下來的函式**

引句:「收件口確認的擋下原因要帶上新原因」

既有函式 `_version_changed_or_none(live, checked)`(`file: src/rtb/executor/execution.py:326-331`)正是計劃引用的「比照 Phase 5 [S310]」對象,其 docstring 已經寫明「使用者 2026-09-23 裁定把 Phase 5 [S310] 擴到憑證過期後重讀與對帳查不到兩條路徑」,計劃要求兩個新原因(政策已變、決策已過時)在這兩條重跑路徑一樣保留,是延伸同一支函式的既有邏輯(把單一比對的 `checked is BlockCode.VERSION_CHANGED` 擴成涵蓋三個原因的集合),沒有另開一條平行的重跑保留路徑。

**5. 分析端「把現在接進處理收件口回覆那一步」對照既有步驟函式簽章慣例**

引句:「由分析端用自己的時鐘比對提案快照的到期時間」

核對 `flow.py` 的狀態機:`_STEPS` 表裡登記的每一支步驟函式一律是 `(store, row, collaborators, now)` 四個參數的簽章,即使用不到也用底線前綴留著(例:`_from_received(_store, _row, _c, _now)`、`_from_proposed(_store, row, c, _now)`,`file: src/rtb/analyzer/flow.py:223-226,263-265`)。處理收件口回應的現況是 `_from_handed_off(store, row, c, _now)` 呼叫 `_from_inbox_answer(answer)`(`file: src/rtb/analyzer/flow.py:298-317,329`),`_now` 目前確實沒往下傳、`_from_inbox_answer` 目前只吃 `answer` 一個參數,計劃說「目前沒拿到現在,要把它接進去」跟程式現況相符。計劃講的做法(「由分析端用自己的時鐘」,不新開系統時鐘讀取)符合整個模組的既有慣例——`now` 一路由 `advance()` 往下傳、決策層不自己讀系統時鐘(`file: src/rtb/analyzer/flow.py:67-68,74-77` 的 `EvidenceSource`/`Decide` 協作介面文件字串已經講明這條規則)。把 `now`(以及 `row.proposal.decision_expires_at`,`_from_handed_off` 已經斷言 `row.proposal is not None`)往下接進 `_from_inbox_answer`,是延伸既有的參數穿透慣例,不是另開一套讀時間的方式,也不用額外查表——`row` 當下已經帶著判斷所需的到期時間。

沒有發現跨層直呼(例如分析端直接開執行端的收件口模組、或重放指令繞過收件口方法直接下 SQL)。這五處看到的都是延伸既有函式/慣例,不是引入第二種做法。
