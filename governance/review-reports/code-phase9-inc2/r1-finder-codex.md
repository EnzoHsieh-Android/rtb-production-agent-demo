severity: major

### 1. 重放跨窗後，既有窗口的執行延遲會消失
severity: major
blocking: 是
引句:「for ident, final in w.part.finals.items():」
file: `src/rtb/ops/metrics.py:583`
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase9可觀測與SLO_計劃.md:158`

觸發情境：提案在窗口 A 內第一次取件後進死信；查詢時死信是全域最後終點，因此窗口 A 有一筆執行端處理延遲。之後它在窗口 B 被重放並成功，`last_terminal_event` 改成窗口 B 的成功事件。

錯誤行為：再次查詢窗口 A 時，`_execution` 使用查詢當下的全域最後終點，因該終點不在窗口 A 而跳過整筆樣本。已結束的窗口因此會在重放後少掉執行延遲，違反延遲依「該段結束時間」歸窗的通則；規格只有最終結果率與端到端延遲允許隨查詢當下結果改變。

建議修法：執行端處理延遲不要共用 `finals` 的全域最終結果語意。應從窗內終點事件建立固定的處理段，依各段終點時間歸窗；死信後若重放，另以死信等待扣除人工等待時間，但不得回頭刪除已歸入過去窗口的完成段。補一支測試：先查死信所在窗口，再加入窗外重放成功事件，斷言原窗口的執行延遲與樣本數不變。

### 2. 窗外的後續終點版本會污染窗內程式版本值域
severity: major
blocking: 是
引句:「seen += [(f.program_version, f.at) for f in part.finals.values()]」
file: `src/rtb/ops/metrics.py:707`
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase9可觀測與SLO_計劃.md:181`

觸發情境：窗口內出現 21 種程式版本，其中 `v02` 原本屬於應保留的最新 20 種；某個窗口內終點事件後來在窗口外被重放，產生更晚的全域終點版本 `future`。

錯誤行為：`part.finals` 可能包含窗口外的全域最後終點，這些版本仍被加入 `_resolver` 的 `seen`。實際以 21 個窗內版本加一個窗外 `future` 呼叫 `_resolver`，`v02` 會由原本的 `v02` 被錯誤併成 `other`。因此同一個過去窗口的 DSP 呼叫、執行延遲或版本衝突切片，會被窗口外的新終點改寫，違反「程式版本按窗內最後出現時間保留最新 20 種」。

建議修法：建立版本值域時只納入時間位於 `[since, until)` 且實際會貢獻該窗口樣本的紀錄；至少應以 `w.inside(f.at)` 過濾 `part.finals`，更穩妥的是由各指標的窗內輸入先統一蒐集版本。補測試讓窗內恰有 21 種版本，再加入窗外重放終點，斷言窗內各版本的保留／合併結果不變。

### 3. 命令列接受無時區時間，之後以未處理例外崩潰
severity: major
blocking: 是
引句:「parser.add_argument("--since", type=datetime.fromisoformat)」
file: `src/rtb/ops/metrics.py:809`

觸發情境：呼叫者傳入合法 ISO 字串但沒有 UTC offset，例如 `--since 2026-09-24T00:00:00 --until 2026-09-24T01:00:00`；或只讓起、迄其中一個帶時區。`--now` 也有相同問題。

錯誤行為：`datetime.fromisoformat` 會接受無時區值。兩端都無時區時 `_check_window` 先通過，稍後執行端窗口讀取因時間不帶時區拋出未捕捉的 `ValueError`；混用有／無時區時則直接在相減或比較時拋出未捕捉的 `TypeError`。快照也可能在計算年齡或額度時崩潰。命令列因此輸出 traceback，而不是穩定拒絕錯誤輸入，且行為會隨資料是否為空而不同。

建議修法：為 `--since`、`--until`、`--now` 共用一個解析器，解析後立即驗證 `tzinfo` 與 `utcoffset()`，無時區就交由 `argparse` 報參數錯誤；公開的 `collect_window`、`collect_snapshot` 入口也應做相同驗證。補齊兩端無時區、混合時區，以及非 UTC offset 正規化後仍能正確落入半開窗口的測試。