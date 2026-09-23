severity: minor

## 一、分層與依賴方向

跨兩側(分析+執行)的端到端測試放在 `tests/analyzer/` 有先例可循,不是新做法:
同一起事故(F5)的上一個增量已經這樣放——`tests/analyzer/test_trust_boundary.py:1-4` 標題就寫
「Phase 7 增量 3 的 S210、S211、S212(事故 F5)」,且該檔第 96 行 `from tests.executor.fakes import
proposal, write_config` 已示範分析側測試匯入執行側 fakes。反向匯入也有先例:
`tests/executor/test_executor_boundaries.py:10` `from tests.analyzer.test_boundaries import
NETWORK_MODULES`。`tests/analyzer/test_boundaries.py` 本身(S52)也早就同時起 `DspServer` 與
`InboxServer` 跑端到端。所以本檔放 `tests/analyzer/test_f5_end_to_end.py` 且匯入
`tests.executor.fakes.write_config`,分層與依賴方向跟既有慣例一致,沒有跨層直呼生產程式碼
(仍是測試對測試的匯入)。

唯一的差異是匯入的**寫法**:既有的 `test_trust_boundary.py:87-96` 把 `from tests.executor.fakes
import proposal, write_config` 寫成**函式內區域匯入**,並在旁邊加註解交代這是刻意跨邊界;新檔
`tests/analyzer/test_f5_end_to_end.py:28` 把 `write_config` 提到**檔案開頭當模組級匯入**,少了
「這是刻意跨邊界」的標記。結構上仍是同一種做法,只是省略了既有慣例裡的說明性寫法,判為 minor。

## 二、命名與錯誤處理

伺服器啟停與 `try/finally` 關 socket 的巢狀結構跟 `tests/analyzer/test_boundaries.py:36-52`
(S52,同一位置就寫了代碼審第 1 輪修正過的教訓)大致一致,但有一處系統性偏差:

既有寫法把「起執行緒」放在 `try:` **裡面**,讓建構之後唯一可能失敗的動作(`threading.Thread(...).start()`)
也被同一個 `finally` 保護:
```
tests/analyzer/test_boundaries.py:41-46
    dsp = DspServer(...)
    try:
        threading.Thread(target=dsp.serve_forever, args=(0.02,), daemon=True).start()
        inbox = InboxServer(...)
        try:
            threading.Thread(target=inbox.serve_forever, args=(0.02,), daemon=True).start()
```
新檔把兩層的 `threading.Thread(...).start()` 都挪到對應 `try:` **之前**:
```
tests/analyzer/test_f5_end_to_end.py:49-54
    dsp = DspServer(dsp_db, fault_injection=False, hang_seconds=0.2, delay_seconds=0.0,
                    capability_key=TEST_KEY)
    threading.Thread(target=dsp.serve_forever, args=(0.02,), daemon=True).start()
    try:
        inbox = InboxServer(tmp_path / "inbox.db", fault_injection=False)
        threading.Thread(target=inbox.serve_forever, args=(0.02,), daemon=True).start()
        try:
```
`test_boundaries.py:39-40` 的註解明講這正是代碼審第 1 輪抓出來的問題類型(「第二個建構或啟動失敗,
第一個已經開的監聽 socket 也要關掉」,包含「啟動失敗」)。新檔把 `.start()` 挪回保護傘外面,結構
(巢狀 try/finally、關閉順序)仍對,但這一行的錯誤處理跟該檔已經修正過的慣例不一致,屬命名/錯誤
處理層級的偏差,不是另立一套結構。

## 三、有沒有第二種做法

沒發現重造既有夾具的情形。`_execute`(`test_f5_end_to_end.py:99-104`)直接組
`Executor(InboxStore, DspClient, CapabilitySigner, write_config, _now)`,用的都是
`tests/executor/fakes.py` 與 `tests/executor/test_execution_e2e.py` 既有的同一批元件
(`DspClient`、`CapabilitySigner`、`write_config`),沒有另寫一套。之所以沒有直接套用
`test_execution_e2e.py` 的 `World` 夾具,是因為 `World` 內部自建並管理一台 `PlannedDsp`
(帶故障注入排程),而本檔需要分析側與執行側共用同一台已經在跑、且不開故障注入的 `DspServer`
實例(`_walk` 把同一個 `dsp` 物件傳給 `_analyze` 與 `_execute`)——`World` 的介面不支援接手外部
已啟動的伺服器,兩者需求不同,不套用不算重造。判斷依據見上面兩問引用的程式碼行。

## 三問小結

不對齊共 2 條,其中 major 0 條。

## F1 匯入寫法比既有慣例少了「刻意跨邊界」的區域匯入標記

severity: minor
blocking: 否 — 結構與先例一致(見一),純屬省略既有檔案裡的說明性寫法,不影響分層方向判斷。
引句:「from tests.executor.fakes import write_config」

## F2 執行緒啟動被移出 try/finally 保護傘,重犯過去代碼審已修過的問題類型

severity: minor
blocking: 否 — 不是新結構、也沒有引入第二種做法,只是這一行的錯誤處理跟 `test_boundaries.py`
既有慣例(及其代碼審教訓)不一致;兩層都犯了同一種偏差,建議推送前跟作者確認是否要挪回 `try:` 內。
引句:「threading.Thread(target=dsp.serve_forever, args=(0.02,), daemon=True).start()」

⚠ 交編排者:F2 是否要升級成 blocking,取決於這個 repo 對「執行緒建立失敗導致 socket 洩漏」這類低機率
資源洩漏在測試碼裡容不容忍——我沒有查到明文規則(RULE:)或 lints 强制這件事,只有 `test_boundaries.py`
裡一則帶脈絡的行內註解記錄了代碼審第 1 輪的教訓,屬於「有出處但沒有 `[since:]`/`[retire:]`/`[confirmed:]`
齊全的 RULE:」,依 CLAUDE.md 判準只能當線索、不能單獨拿來擋審查,故我判為 minor 且不擋;若編排者認為
「同一個教訓在同一份事故的後續增量裡重犯」應該視為對齊問題升級,建議改判 blocking。
