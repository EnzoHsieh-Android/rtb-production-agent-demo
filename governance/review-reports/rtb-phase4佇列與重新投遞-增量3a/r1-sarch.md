severity: major

### 1. 「真並行驗證」的會合機制字面上是單向訊號,驗不到 S130 需要的真正同時競爭
severity: major
blocking: 是 — 這支測試守的正是 Phase 4 最核心的保證(同一則訊息不會被兩個工作者同時處理、預算不會被改兩次),若機制本身測不到它要驗的東西,S130 這條合約等於沒有被真正驗證過,卻會被回報為通過。
引句:「用檔案當信號(一個寫檔、另一個等檔),不用睡固定秒數。」

說明:S130「當兩個執行迴圈同時取件,同一則訊息應只由一個處理」要驗的是「兩個工作者在同一瞬間搶同一則訊息」時 SQLite `BEGIN IMMEDIATE` 的互斥有沒有真的擋住重複交付。但設計寫的會合機制是「一個寫檔、另一個等檔」——這是單向訊號(A 完成後寫檔,B 讀到檔案才繼續),本質上是「B 等 A 先做完」,不是讓兩者在同一瞬間都卡在 `receive()` 前、再一起放行的柵欄(barrier)。

具體例子(輸入 → 預期 → 字面實作的落差):
- 輸入:收件表只有一則待處理訊息 M(廣告 c1);兩個執行迴圈 A、B 幾乎同時啟動。
- 設計期待:A、B 幾乎同時呼叫 `receive()`,只有一個真正搶到 M,驗證的是「同時搶」這個時間點上的資料庫互斥。
- 若照字面「一個寫檔、另一個等檔」實作:B 要等到 A 寫下訊號檔才會開始跑——也就是 A 早就已經把 M 處理完(甚至已確認)之後,B 才開始它的 `receive()`。這時候佇列裡已經沒有待處理的 M 了,B 這一輪很可能只是 idle、什麼都沒搶,S130 斷言的「DSP 共收到 1 次寫入」照樣會綠,但整個測試從沒真正製造出「兩者同時搶同一則訊息」這個情境——這跟增量 1 已經驗過的 [S97](單一行程內測「處置是處理中而且租約還沒到期的提案,取件應跳過」)在驗證力上沒有實質差異,只是換成了兩個真行程,增加了複雜度卻沒增加驗證力。

設計文字沒有說明「一個寫檔、另一個等檔」具體怎麼套用在 S130(需要雙方**同時**被放行)、S131(需要兩份提案被兩個工作者**同時**取出)這種要製造真正同時競爭的情境,跟 S129、S132(只需要「A 卡住等 DSP 回應」與「B 之後才啟動/接手」這種**先後**關係,單向訊號就夠)混在一起,用同一套語焉不詳的機制帶過,而「要驗的四件事見合約」也沒有逐件說明怎麼落地。

file: `/Users/enzo/rtb-production-agent-demo/tests/executor/test_crash_recovery.py:13`(增量 2 自己的文件寫明兩個子行程是「一先一後跑」,不是同時)

### 2. 「比照增量 2 的子行程做法」這個 PRIOR-ART 站不住,也沒交代為何捨棄本專案既有、更輕量的「並行測試」前例
severity: major
blocking: 是 — 理由同上,這是同一組合約(S130–S132)驗證力不足的根源之一:多開了一套全新、未經驗證的跨行程機制,卻放著專案裡已經證明可用、風險更低的做法不用。
引句:「比照增量 2 的子行程做法,但兩個執行迴圈**同時**活著。」

說明:增量 2 的子行程做法(test_crash_recovery.py)是「先讓一個行程死掉,再啟動另一個」,本質上是**循序**的兩次獨立呼叫,從沒驗證過兩個行程真的同時存活、同時對同一張表下手的情境。把它拿來當「真並行驗證」的 PRIOR-ART 並不成立——這是舊技法的外推,不是既有前例的沿用。

而本專案其實已經有「真的證明同時競爭」的既有做法,而且反覆用過四次:`tests/executor/test_attempt_store.py`(S14、S15,`_race()`:多執行緒各自開自己的 `InboxStore` 連線、`threading.Barrier` 逼真同時呼叫 `begin()`)、`tests/dsp/test_store.py:213-234`(`threading.Barrier` 逼 20 個執行緒同時 `execute()`)、`tests/dsp/test_server.py:203-220`(`threading.Barrier` 逼同時發 HTTP 請求)、`tests/analyzer/test_flow.py`(`threading.Barrier(20)` 逼真的同時讀同一列)。這些測試證明:要驗證「同一個 SQLite 檔案上兩個獨立連線同時互斥」,在**同一個行程**內起多執行緒、各自開自己的連線、用 `threading.Barrier` 同時放行,就是本專案一貫且已證明可靠的做法——而且 `Executor`/`InboxStore` 本身就是可以在同一行程內建多個獨立實例的物件(`tests/executor/fakes.py` 的 `Harness`、`Executor(store, dsp, signer, config_path, clock, owner)` 全部協作者可注入),S130、S131、S132 三件事都不像 S129 那樣需要「真的重啟一個行程」,理論上可以直接套用這個既有前例、完全不必開真的子行程。

專案自己的治理紀錄甚至已經點名過這類差異:`docs/rtb-production-agent-demo-knowledge/Systems/Mock-DSP.md:63` 記著「『同一把冪等鍵只套用一次』的並行測試全部是同一個行程裡的多執行緒……真正跨兩個行程時 SQLite 仍擋住雙寫,但輸家拿到未分類的 500 而不是可重試的 503」——這正是「該不該真的跨行程測」的既有討論,但 3a 的 PRIOR-ART 完全沒有引用它,也沒解釋為什麼這次選擇不同於既有前例的做法、範圍要擴大到哪幾件事(而不是只有真正需要重啟的 S129)。CLAUDE.md 本身要求「設計動筆前先問世界……預設借用既有設計,真沒輪子才自建」,這一段沒有做到這一步。

file: `/Users/enzo/rtb-production-agent-demo/tests/executor/test_attempt_store.py:378-397`
file: `/Users/enzo/rtb-production-agent-demo/tests/dsp/test_store.py:213-234`
file: `/Users/enzo/rtb-production-agent-demo/docs/rtb-production-agent-demo-knowledge/Systems/Mock-DSP.md:63`
