severity: minor

## 發現 1:模型回傳深巢狀的 JSON 時丟 RecursionError,沒被當成選項外答案。模型已付費,任務轉成 FAILED,沒有退回程式規則
severity: minor
blocking: 否

**位置**
- `parse_answer` 讀 JSON 的地方只接兩種例外:
  引句:「except (ValueError, TypeError) as bad:」
- 查詢串列去重時,會對每個元素做 `str()`:
  引句:「or len(set(map(str, raw))) != len(raw)):」
  如果元素是巢狀很深的串列,`repr` 丟 RecursionError。巢狀更深時,`json.loads` 本身也會丟。
- `Judge._ask` 只接 OffMenu(file: `src/rtb/analyzer/ai_judge.py:124`)。RecursionError 一路穿到流程層,被通用例外接住:
  引句:「except Exception as exc:  # 對已到手的證據做純計算的那一半出錯:同決策丟例外,轉 FAILED」
- 後果:
  - 呼叫次數已記、模型已付費。
  - 任務落在終態 FAILED,沒有調查紀錄。
  - 這違反「任何失敗都改由程式規則決定」。

**例子**
- 輸入:模型回 `{"choice":[` + `[`×70000 + `]`×70000 + `],"reason":"x","evidence":[]}`,約 140 KB。
- 預期:判成選項外答案,退回原因是 off_menu,由程式規則決定。
- 實際(實測):
  - `state: failed`,`model calls: 1`,`call_count: 1`,`rounds: ()`。
  - error_detail 是 `RecursionError('Stack overflow … while getting the repr …')`。
- 門檻:
  - Python 3.14 上,`str` 大約在巢狀 6.1 萬層開始失敗(約 123 KB),`json.loads` 大約在 11.6 萬層。
  - 模型輸出上限是 32k token,另外最多自動續寫 3 次。要攻擊者用廣告名稱注入,讓模型吐出十幾萬個括號才觸發得到,所以列為 minor。

**重現**
- 複製到 /tmp,用 `tests.analyzer.test_investigation_review_r2` 的 `_to_analyzing`、`_ai`。
- 用 `Model(上面那段文字)` 包成 `ai_judge.Judge`,推一步,再看 `store.latest("t1")`。

**修法方向**
- `parse_answer` 的第一個 except 加上 `RecursionError`,再把 `_choice` 的去重改成先檢查型別。
- 或在 `_ask` 呼叫 `parse_answer` 的地方,改接 `(inv.OffMenu, RecursionError)` 並判成選項外。

## 發現 2:r2 在續租之後、送出之前多了一個寫入交易(record_model_call),它的等鎖沒算進一步的時間預算,停止寬限的餘裕從 5 秒變成 0
severity: minor
blocking: 否

**位置**
- 記次改到最後一次停止檢查之後,另開一個 `BEGIN IMMEDIATE`,最壞會等滿 `BUSY_TIMEOUT_SECONDS`:
  引句:「count = store.record_model_call(lease.current, clock, limit)」
- 時間預算還是 r1 的算法,沒有這一段等鎖:
  引句:「結算最多三次的等鎖、提交等鎖(15 + 10 + 20 + 5 = 50)。續租自己的等鎖在讀時鐘之前,不佔新租約。」
  引句:「return BUSY_TIMEOUT_SECONDS + ai_step_worst_seconds()」
  (file: `src/rtb/stepbudget.py:30`、`src/rtb/stepbudget.py:41`)
- 最壞的時間序:
  - 停止訊號剛好在 `_ask` 的最後一次停止檢查之後送到,之後不會再看停止旗標。
  - 之後依序是記次等鎖約 5.x 秒、模型 15 秒、等行程群組 10 秒、花費帳 20 秒、提交 5 秒,合計約 55.x 秒。
  - 啟動器的寬限是 55 秒,超過一點就會硬殺。計劃寫的後果是孤兒 claude 行程繼續花額度、預留不結算。
  - r2 之前,最後一次檢查之後的最壞是 50 秒,有 5 秒餘裕。
- 租約這邊還守得住:續租讀時鐘之後最壞約 55.x 秒,小於 60 秒。只是設計寫的「續租後最壞 50」已不成立,守它的測試 `ai_step_worst_seconds() == 50.0`(file: `tests/analyzer/test_investigation_e2e.py:227`)量的是舊時序。

**例子**
- 輸入:另一條連線持有 `BEGIN IMMEDIATE`,這時呼叫 `record_model_call`。
- 預期:等鎖時間算在一步的預算裡。
- 實際:實測等了 5.4 秒才回 None;`ai_step_worst 50.0 grace 55.0` 都沒變。

**重現**
- 在 /tmp 的複本用真的 `TaskStore`:取租約、續租。
- 另一條 sqlite3 連線先下 `BEGIN IMMEDIATE`,再量 `store.record_model_call(lease, clock, 3)` 花的時間。

**修法方向**
- 在 `ai_step_worst_seconds` 加一次 `BUSY_TIMEOUT_SECONDS`(變成 55),寬限變成 60。
- 或讓 record_model_call 用較短的等鎖時間。

## 第 2 輪修正的驗收(攻擊者角度,不列為發現)

**付費上限(record_model_call)**
- 記次跟「仍持有租約」的核對在同一個 `immediate_transaction` 裡;call_seq 是 `count+1`,主鍵是 (task_id, call_seq)。
- 兩個工作者不會同時記,因為只有租約持有者寫得進去。
- 到上限時回 limit+1、什麼都不寫,由程式規則決定;這筆退回紀錄如果提交失敗,重來也不會再付費。
- 忙碌或失去租約時回 None,轉成 RenewalSkipped:不呼叫模型、不記次。
- 其他資料庫錯誤包成 _StoreFailed 往外丟,這一步不寫,也不付費。
- 停止:兩次停止檢查都在記次之前,停止不會吃掉次數。
- 模型呼叫中途被接手(可能發生,因為 `_holds` 不看到期時間,模型最壞 50 秒):兩次付費都有記到,一生總數仍不超過 3。
- 找不到「記了次數卻沒付費、而且攻擊者能反覆觸發」的路徑。

**寫進 SQLite 的模型字串**
- 理由要過 `storable` 加黑名單(Cs、Co、Cn、Cc、Cf、Zl、Zp)。
- 證據引用的 ref、field、value 要完全等於收據上的 ASCII 值。
- choice 只收列舉值;model_source 和 fallback 都是程式產生的。
- cited_json 最多 5 項,每個值不超過 128 字,碰不到 2000 字的截斷。
- 沒找到其他會讓提交丟例外的模型輸出,除了發現 1 那條,但它是在提交之前就轉成 FAILED。

**模擬 DSP 的攻擊輸入**
- 逐列檢查拿掉 `_passes` 外殼之後,每支檢查都逐一用 None、布林、串列、物件、NaN、10**400、負數、字串測過,沒有一支丟例外。
- `_aware` 用 20 萬組隨機字串加極端時區做模糊測試,沒有丟例外。
- NaN 和 Infinity 字面值被 `is_finite_or_none` 和 `is_count_or_none` 擋下。
- 超過 4300 位的整數在 `json.loads` 就丟 ValueError,記成 invalid。
- `_sum` 改用 Fraction 之後:
  - 用 2 萬組極端值(`5e-324`、`1.79e308`、`10**308`、`MAX_INT`)對逐日和較長時間窗做模糊測試,沒有例外漏出。
  - 單次最慢 0.3 毫秒。
- 1 天窗大於 7 天窗時,`contradictory` 把整份收據記成 invalid,原始回應也不存。
- 另外看到一件事:單日點擊多於曝光會被整段加總蓋過。例如第 1 天點擊 500、曝光 100,`click_rate_change` 仍是可引用的 `148.8`。這是 r2 已經明文收斂的裁定(「判定看整段加總、不看單日」),所以不列為發現。
- 深巢狀的 DSP 本文會丟 RecursionError,但它跟 5xx 一樣會重試,而且基本讀取本來就是這樣,屬於既有行為。

**資料區跳脫(代理對寫法)**
- 用 10 萬組隨機字串做模糊測試,範圍包括 C0/C1 控制字元、U+2028/2029、U+202E、BOM、孤立代理、U+E0041、U+1D173、U+10FFFF、未指定字元。
- 結果全部都是一行、UTF-8 編得出來,`json.loads` 還原成原字串(代理對正規化後相同)。
- 唯一會撞成同一段的輸入是「兩個相鄰的孤立代理」對上「對應的 BMP 以外字元」。但 DSP 本文經 `json.loads` 時,相鄰代理一定會合併,所以這條路走不到。

## 看過的改動檔(r3-delta.patch 全部)
- claims/aggregate-blast-radius.json
- claims/concurrency.json
- claims/permission-guardrail.json
- claims/prompt-injection.json
- docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase13AI參與決策_計劃.md
- docs/rtb-production-agent-demo-knowledge/Systems/任務流程領域模型.md
- docs/rtb-production-agent-demo-knowledge/Systems/分析行程流程與檢查點.md
- docs/rtb-production-agent-demo-knowledge/Systems/確定性指標計算.md
- src/rtb/analyzer/ai_judge.py
- src/rtb/analyzer/dsp_client.py
- src/rtb/analyzer/flow.py
- src/rtb/analyzer/instrumented.py
- src/rtb/analyzer/investigation.py
- src/rtb/analyzer/task_store.py
- src/rtb/domain/evidence.py
- src/rtb/domain/metrics.py
- tests/analyzer/test_ai_judge.py
- tests/analyzer/test_investigation_review.py
- tests/analyzer/test_investigation_review_r2.py

2 條,blocking 0。
