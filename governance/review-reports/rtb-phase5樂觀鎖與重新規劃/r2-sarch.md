severity: major

### 1. 新增的 DSP 操作紀錄查詢沒有納入既有「外部呼叫一律經 instrumented.py 記 tool_calls」的慣例,「衝突可查:軌跡」的完成條件對這條新路徑不成立

severity: major
blocking: 是 這條完成條件是交接文件 Phase 5 明列的四個完成條件之一,而且設計自己在「衝突可查」一節列舉軌跡來源時,漏列了這個新呼叫本身,不是含糊其詞,是具體遺漏。
引句:「分析端的 DSP 用戶端新增這一個唯讀查詢(DSP 已經有這個端點),照分析端讀 DSP 既有的白名單與大小上限做」
說明:專案既有明文慣例(架構對齊範圍內的「呼叫紀錄」做法)是:`dsp_client.py`、`inbox_client.py` 只管「打 HTTP、轉成 Evidence/Accepted」,完全不碰 `TaskStore`;誰要記 `tool_calls`,一律由 `instrumented.py` 包一層(`InstrumentedEvidenceSource`、`InstrumentedSubmit`,或像 `dsp_client.fetch` 那樣用 `on_call` 鉤子讓每個端點各自記一筆)。這條慣例有正式 RULE 記在 `Systems/分析行程流程與檢查點.md`(`[since:2026-09-22]`),`flow.py` 本身「完全不知道 tool_calls 這件事」。
快照只交代新查詢要「照分析端讀 DSP 既有的白名單與大小上限做」,隻字未提要照抄既有的第三件事——呼叫紀錄。而快照自己在「衝突可查:軌跡、指標、稽核」一節列舉的軌跡來源只有:原任務擋下那一列的處置與原因、接續關係表、執行端嘗試紀錄三樣,完全沒提到這個新的 DSP 唯讀查詢本身要被記進 `tool_calls`。
- 輸入 → 預期:任務 t1 進入 HANDED_OFF,呼叫端第一次呼叫 `advance(t1)` 觸發新步驟,DSP 的操作紀錄端點連續逾時 3 次(3 輪各自呼叫)。依「衝突可查:軌跡」的完成條件與既有慣例,`trace_for(t1)` 的 `tool_calls` 應該比照 `dsp_client.fetch` 現況查詢的做法,看到 3 筆這個新端點的失敗紀錄(可算進之後的可觀測性與重試次數判斷)。
- 實際(照字面實作):設計文字裡這個新查詢只被要求「照白名單與大小上限做」,沒有像三個既有介面一樣被要求「比照 `instrumented.py` 記 tool_calls」;字面上最直接的實作是在 `dsp_client.py` 或新模組裡新增一支函式直接打 HTTP、回傳結果,不會自動經過 `instrumented.py` 那一層包裝(那是額外一步,不在「讀白名單」字面涵蓋範圍內)。`trace_for(t1)` 的 `tool_calls` 對這 3 次查詢完全沒有紀錄;要稽核「這個任務為什麼一直沒有推進、到底查過幾次 DSP」時,唯一看得到的只剩任務歷史表的狀態沒變,查不到嘗試次數與失敗原因,「衝突可查(軌跡)」這條完成條件對這個路徑不成立。
file: `/Users/enzo/rtb-production-agent-demo/src/rtb/analyzer/instrumented.py:1`(module docstring:「`flow.py` 不知道、也不需要知道 tool_calls 這件事」,以及 `InstrumentedEvidenceSource`/`dsp_evidence_source`/`InstrumentedSubmit` 三個既有包裝範例)
file: `/Users/enzo/rtb-production-agent-demo/docs/rtb-production-agent-demo-knowledge/Systems/分析行程流程與檢查點.md:40`(既有 RULE:「`dsp_client.py`、`inbox_client.py` 不碰 `TaskStore`,只做「打 HTTP、轉成 Evidence/Accepted」;要記錄 tool_calls 的一律是 `instrumented.py`」[since:2026-09-22])
file: `/Users/enzo/rtb-production-agent-demo/src/rtb/analyzer/task_store.py:392`(`trace_for`:軌跡由 `tasks`+`evidence`+`tool_calls` 三張表拼成,沒寫進 `tool_calls` 就查不到)
