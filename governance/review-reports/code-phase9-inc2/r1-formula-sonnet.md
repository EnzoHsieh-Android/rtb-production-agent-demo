severity: major

### 1. execution_seconds 用「全域最後終點事件」而非「窗內實際終點事件」歸窗,違反模組自己宣告的「事件型窗過去不變」保證
severity: major
blocking: 是
引句:「執行端處理:第一次取件 → 最終終點,扣掉其間的人工核可等待與死信等待;依最終終點歸窗。」

觸發情境:一份提案在窗一 [T, T+60分) 內第一次取件後死信(終點事件之一),此時尚未重放。之後(可能還在同一窗,也可能到了更晚的窗)才重放成功、寫出第二個終點事件(交給執行)。

錯誤行為:`_execution`(`src/rtb/ops/metrics.py:580-597`)算 execution_seconds 用的 `final` 是 `w.part.finals[ident]`——`_read_executor`(`src/rtb/ops/metrics.py:342-367`)用終點部分索引查出的「全域最後一個終點事件」,不是「窗內實際發生」的那個終點事件;再用 `w.inside(final.at)` 判斷這份提案算不算進本窗。這個「全域最後」會隨後續重放改變,於是同一個已經過去、已經算過一次的窗,重新查一次,execution_seconds 的樣本會整筆消失或數字改變——這正是模組文件自己聲明「其餘是事件型,窗過去就不變」要保證、且只有「最終結果率」與「端到端」被明文排除的行為;execution_seconds 沒被排除卻共用了同一套「查詢當下最終結果」機制。

手算驗證(用專案自身測試治具 `tests/ops/rows.py` 實跑,非臆測):t2 於 T+1 分取件、T+10 分死信(都在窗一 [T,T+60分) 內)。查詢 A(重放前查窗一):`execution_seconds.max` = 540 秒,count=1。之後追加 t2 於 T+70 分重放放回、T+71 分再取件、T+75 分交給執行(成功終點落在窗二)。查詢 B(重放後,原封不動地再查同一個窗一 [T,T+60分)):execution_seconds 四個分位數樣本全部變成 `Status.NO_SAMPLES`、count=0——窗一裡真實發生過的 9 分鐘執行時間憑空消失。

建議修法:execution_seconds 應比照 `_terminal_event_rate` 的做法,直接對「窗內實際出現的每一個終點事件」逐一計算(用 `w.part.events` 裡 `kind in TERMINAL_KINDS` 的事件本身當終點),而不是回頭查 `finals` 的全域最後一筆,才符合「延遲依那一段的結束時間歸窗」與「事件型窗過去不變」的通則。

### 2. collect_window/CLI 不驗證 since/until/now 帶時區,操作者少打時區字尾會讓整支指令列以未捕捉例外當機
severity: major
blocking: 是
引句:「return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")」

觸發情境:操作者執行 `python -m rtb.ops.metrics --since 2026-09-22T12:00:00 --until 2026-09-22T13:00:00 ...`(時間戳沒帶 `Z`/`+00:00`,`argparse` 用的 `type=datetime.fromisoformat` 完全接受、回傳 naive datetime)。

錯誤行為:`metrics.py` 自己的 `_iso()`(`src/rtb/ops/metrics.py:193-195`)與 `_check_window()`(`src/rtb/ops/metrics.py:720-724`)都不檢查 `since`/`until`/`now` 是否帶時區;兩個 naive datetime 互相比較不會報錯,窗長檢查因此誤判過關,直到 `_read_executor` 呼叫 `inbox.lifecycle_events_between` 才因底層 `attempt_store.iso()`(`src/rtb/executor/attempt_store.py:378-391`,那裡特地加了 `_require_aware`,註解明講「沒帶時區就拒絕(不然會被當成本機時間,範圍篩錯)」)丟出 `ValueError: 時間必須帶時區`。這個例外不在 `run()`(`src/rtb/ops/metrics.py:818-847`)任何一個 `except` 分支裡,會以未捕捉例外的原始 Python traceback 整個炸出 CLI,不是規格要求的「固定結束代碼與訊息」。

手算驗證(已實跑):直接呼叫 `m.run(["--since","2026-09-22T12:00:00","--until","2026-09-22T13:00:00",...])`,得到 `UNCAUGHT EXCEPTION escaped run(): ValueError 時間必須帶時區`,不是任何一個 `EXIT_*` 代碼。

建議修法:在 `_parse()` 或 `_check_window()` 一開始就檢查 `since`/`until`/`now` 是否帶時區(仿照 `attempt_store._require_aware`),沒帶時區時在 `run()` 裡以固定結束代碼與訊息回報,不要讓例外一路裸奔到呼叫端。

### 3. terminal_event_rate 的政策版本標籤拿「目前部署常數」跟事件「寫入當下的政策版本」比,同一個過去的窗會因日後改版而改變標籤
severity: major
blocking: 是
引句:「return CURRENT if value == POLICY_VERSION else OTHER」

觸發情境:窗一裡有一個終點事件,寫入當時政策版本欄位是「當時的目前版本」(例如 `demo-pacing-v1`)。之後系統改版,`POLICY_VERSION` 常數升成 `demo-pacing-v2`(Phase 8 本就設計政策版本會變,還有 `POLICY_VERSION_CHANGED` 這個擋下原因專門處理)。

錯誤行為:`_Resolver.policy()`(`src/rtb/ops/metrics.py:244-246`)比較的是 `value == POLICY_VERSION`——這個 `POLICY_VERSION` 是從 `rtb.domain.proposal` 匯入的模組級常數,代表「查詢當下」部署的政策版本,不是「事件寫入當下」的政策版本。結果:同一個已經過去、已經關閉的窗,只因系統之後改版,重新查一次,`terminal_event_rate` 同一批事件的 `policy_version` 標籤會從「目前」變成「其他」——這正是 [S632]「終點事件率應依事件時間歸窗,同一個過去的窗之後再查結果不變」明文要保證、模組文件自己也寫「其餘是事件型,窗過去就不變」的東西。

手算驗證(已實跑):事件在窗一寫入時,政策版本欄位 = 當時的 `POLICY_VERSION`("demo-pacing-v1")。改版前查窗一:`terminal_event_rate` 的 `policy_version` 標籤集合 = `['current']`。把 `m.POLICY_VERSION` 換成 `"demo-pacing-v2"`(模擬部署升版)後,原封不動地重查同一個窗一:標籤集合變成 `['other']`。

建議修法:政策版本要不要算「目前」,應該用寫入那一列時就固定下來的資訊判斷(比照 `program_version` 那樣把「當時是不是目前版本」的判斷結果快照存下),不能在查詢當下才回頭跟活的模組常數比較。
