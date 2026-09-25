severity: major

## 發現 1:模擬 DSP 給出合乎白名單的極端金額時，收據字串超過 128 字，蒐證那一步每次都失敗，任務永遠卡住
severity: major
blocking: 是

**位置**
- 逐日、過去調整兩支端點的白名單，花費與營收欄只用 `is_finite_or_none` 檢查。這個檢查沒有上限，也沒有下限(file: `src/rtb/domain/_checks.py:39`)。
- 收據的變化百分比先用分數精確算，再轉成全長整數字串。長度沒有上限:
  引句:「    return f"{sign}{abs(tenths) // 10}.{abs(tenths) % 10}"」
- 收據是「可信證據」。可信證據的字串值要符合 `ID_PATTERN`,也就是 `[A-Za-z0-9._:-]{1,128}`(file: `src/rtb/domain/_checks.py:13`、file: `src/rtb/domain/evidence.py:120`)。超過 128 字，建構 Evidence 時會丟 ValueError。
- 丟出的位置是開了 AI 時的證據來源:
  引句:「            receipts.append(inv.receipt_evidence(task.task_id, task.seq, option, read.raw,」
- 流程層的蒐證步把例外當成「這次沒拿到證據，不寫入、留在原地」。所以不會記成 [S1132] 說的 invalid 沒有結果收據，而是這一步永遠寫不進去。
  - 任務卡在「蒐集證據」,永遠結不了案。
  - 每次重試都重讀 DSP,每次讀取各新增一筆 tool_calls,這張表無上限長大。
- 本地驗證「欄位不合格記 invalid、不讓整步失敗」的合約在這裡失效。

**例子**
- 輸入:
  - 廣告 c1 的 1 小時配速偏低。
  - 逐日第 1–3 天營收 120.0,第 4–7 天營收 `1e-300`(合法的有限浮點數，模擬平台的 store 也照收)。
  - AI 第 1 輪選了 `check_daily_trend`。
- 預期:收據的 `revenue_change` 寫 `na`,或這個查詢記成 invalid。下一輪照常問 AI 或退回規則。
- 實際:`revenue_change` 長 304 字，Evidence 驗證失敗。狀態一直是 `collecting_evidence`。

**重現**
- 在 /tmp 複製 src,用真的 `CampaignStore`、`seed.seed_platform_history`、`DspServer` 種上面的資料。
- AI 決策函式用假的:續租後回 `QueryMore` 加一列 query 紀錄。
- `flow.advance(..., ai_decide=judge)` 用 `instrumented.investigation_source` 當證據來源，連推 8 次。
- 實測輸出:
  - 第 3 步以後一直是 `collecting_evidence`,5 次重試全都沒有進展。
  - tool_calls 已 17 列，還在長。
  - `receipt_evidence` 單獨呼叫丟出 `ValueError 證據欄位不合法:payload`。
- 同樣的情況也會出現在過去調整的 `adjN_revenue_change`,例如調整前營收 `1e-300`、調整後 1.0。

**修法方向**(擇一)
- 收據格式化超過字元上限時寫 `na`。
- 或讓證據來源在建收據失敗時改記一筆 `NoResult.INVALID` 收據。

## 發現 2:模型已付費、但提交時資料庫忙碌，下一次會重問模型。這種重付不受輪數上限約束，分析端調查又不計花費上限
severity: minor
blocking: 否

**位置**
- 輪數只從已提交的調查紀錄算:
  引句:「        rounds = tuple(record for _seq, record in store.investigation_rounds(row.task_id))」
- 流程是:續租 → 呼叫模型(已付費)→ `commit_step`。如果 `commit_step` 在 `immediate_transaction` 等鎖超過 5 秒丟出 `DatabaseBusy`:
  - 這一輪的紀錄沒寫進去。
  - advance 放掉租約後把例外往外丟，runner 退避後重來。
  - 重來時 `progress()` 看到的還是同樣的輪數，所以再付一次同一輪的錢。
- 花費帳的 `CAPPED_CALLERS` 不含分析端調查(file: `src/rtb/modelledger.py:30` 附近,patch 裡是上下文行)。本地上限不會攔。
- 只要「續租之後到提交之前」這段時間裡持續有別的寫入者握著 analyzer.db 的寫鎖，即時模式的付費次數就沒有上限。
- 設計 R3-1 已經處理了「拿舊收據提交落空而重付」,但沒處理「提交丟例外」這條路。

**例子**
- 輸入:AI 那一步每次提交都撞到忙碌(例如同一個資料庫上另一個工作者或外部工具長時間握寫鎖)。
- 預期:一件工作一生最多 3 次模型呼叫。
- 實際:同一輪可以被付費任意多次。

**重現**
- 在 /tmp 用 TaskStore 的子類別，讓帶 `investigation` 的 `commit_step` 前 5 次丟 `DatabaseBusy`。
- 假的 AI 決策函式每次呼叫記一次「付費」。
- 實測輸出:
  - 前 5 次都是 DatabaseBusy,第 6 次才寫進去。
  - `paid model calls for round 1: 6  committed rounds: 1`。

**評級理由**
- 觸發要有持續超過 5 秒的寫鎖競爭(analyzer.db 開 WAL,讀者不擋)。
- 錄製模式是 0 元。
- 所以列 minor。

**修法方向**
- 在模型呼叫之前先提交一列「本輪已開始」的占位紀錄。
- 或把模型呼叫也算進每件工作的呼叫次數，並寫在續租同一個交易裡。

## 已查過、沒找到能利用的洞(不列為發現)

**提示注入**
- 廣告名稱沒濾換行，可以偽造「資料結束>>>」,讓後面的文字看起來像收據或允許清單。
- 但回答要經選項允許清單加逐項收據核對。已確認名稱翻不動金額、廣告、動作:金額一律由 `build_proposal` 照公式算。
- 名稱只能翻動提不提案，這落在協調者代使用者裁定改寫後的宣稱範圍內。
- `status` 在 dsp_client 已限定只能是 active 或 paused,塞不進收據。
- 在 Python 3.14 上,1000 到 100000 層巢狀 JSON 都不會觸發 RecursionError,所以沒有「非 OffMenu 例外轉 FAILED」這條路。

**舉證核對**
- 參照只收 base 與已查過的選項;值要逐字等於收據上的值;排除 na、raw_rows、沒有結果的收據。
- 偽造的收據值過不了核對。
- 但引用「status=active」這類跟結論無關的欄位也能過。這是設計只核對「值存在」,不是實作漏洞。

**模擬 DSP 新端點**
- 只准 GET,沒種回 404,廣告不存在回 CampaignNotFound。
- 用戶端逐列白名單，頂層廣告編號要跟任務一致，多欄、少欄都整包不收。
- `seed_past_operations` 沒有對外路由。

**子行程環境與金鑰**
- 分析端角色拿不到任何金鑰(`_ROLE_KEYS` 沒有它)。
- claude 子行程只帶 PATH、HOME、USER、LANG,帶 `--tools ""`、`--safe-mode`、`--max-budget-usd`。
- 即時模式不准換帳檔。

**輪數上限**
- 只要每一輪都提交成功，上限就守得住:
  - 查詢紀錄滿 2 列後只剩結論。
  - 退回或下過結論後一律記為 AI 已用過，改由程式規則決定。
  - 接續任務受 MAX_GENERATION=3 限制。
- 停止訊號中途被打斷的重付是設計接受的代價。

## 看過的改動檔(整份 r1-snapshot.patch)

**claims**
- claims/aggregate-blast-radius.json
- claims/concurrency.json
- claims/idempotency-unknown-outcome.json
- claims/permission-guardrail.json
- claims/prompt-injection.json

**docs/rtb-production-agent-demo-knowledge/Projects**
- RTB_Phase10評估與Jev決策點_計劃.md
- RTB_Phase11證據清單與驗證器_計劃.md
- RTB_Phase12一鍵展示與HTML報告_計劃.md
- RTB_Phase13AI參與決策_計劃.md

**docs/rtb-production-agent-demo-knowledge/Systems**
- Mock-DSP.md
- 一鍵展示.md
- 任務流程領域模型.md
- 分析行程流程與檢查點.md
- 展示頁面.md
- 模型用戶端.md
- 確定性指標計算.md

**根目錄**
- pyproject.toml

**src/rtb/analyzer**
- ai_judge.py
- dsp_client.py
- flow.py
- instrumented.py
- investigation.py
- modelgate.py
- policy.py
- runner.py
- task_store.py

**src/rtb/demo**
- driver.py
- faults/ruff.toml
- flow.py
- flow_svg.py
- launcher/__init__.py
- launcher/ruff.toml
- observe.py
- ruff.toml

**src/rtb/domain**
- evidence.py
- metrics.py
- ruff.toml

**src/rtb/dsp**
- errors.py
- ruff.toml
- seed.py
- server.py
- store.py

**src/rtb 其他**
- src/rtb/executor/ruff.toml
- src/rtb/modelclaude.py
- src/rtb/modelledger.py
- src/rtb/stepbudget.py

**tests/analyzer**
- test_ai_judge.py
- test_investigation_e2e.py
- test_investigation_flow.py
- test_investigation_reads.py
- test_narrate.py
- test_runner.py
- test_worth_check.py

**tests 其他**
- tests/demo/test_flow.py
- tests/demo/test_launcher.py
- tests/demo/test_observe.py
- tests/demo/test_page.py
- tests/domain/test_metrics.py
- tests/dsp/test_investigation_data.py
- tests/eval/test_model_candidate.py
- tests/test_spawn_boundary.py

實驗都在 /tmp/資安-opus-p13i2 做，沒有呼叫真模型，也沒有寫 ~/.rtb。做完已刪掉那個目錄，也確認沒有殘留行程。

2 條，blocking 1。
