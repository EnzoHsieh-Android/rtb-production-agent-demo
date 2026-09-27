severity: major

# 代碼審 Phase 15 增量 2 第 1 輪:資安-opus

席位:資安-opus(外部審查、攻擊者視角)。範圍:r1-snapshot-src.patch、r1-snapshot-tests.patch,加上閘道、花費帳、模型用戶端、邊界測試。
重現腳本都放在審查員自己的暫存目錄,repo 沒改動。所有模型回覆都用假的 `ask` 注入,沒有呼叫 claude、沒有設 `RTB_MODEL_LIVE`、沒有碰 ~/.rtb。

## 結論一覽

| # | 標題 | severity | blocking |
|---|------|----------|----------|
| F1 | 平衡深巢狀的回覆讓 `_finite` 遞迴爆掉,`parse_reply` 丟 RecursionError | major | 是 |
| F2 | 回覆裡的孤立代理字元(`\ud800`)讓 `_note`/`_load` 丟 UnicodeEncodeError | major | 是 |

①②③⑤⑥ 沒有可以被利用的洞,理由列在文末。

---

## F1 平衡深巢狀的回覆讓 `_finite` 遞迴爆掉,`parse_reply` 丟 RecursionError

severity: major
blocking: 是
引句:「    if not _finite(data):」
查證 file: `src/rtb/eval/rule_mining_check.py:135`(遞迴本體在 `src/rtb/eval/rule_mining_check.py:121`、`:123`)

**問題**
- 模組說明的合約是「解析錯(含超長整數、巢狀過深)→ 整份拒絕,記 1 筆無效提交」。
- 實際上 `json.loads` 在 6000 位元組以內都解得過去。Python 3.14 的 C 掃描器實測到 2900 層都沒丟例外。
- 之後的 `_finite(data)` 是 Python 遞迴,每一層要吃兩個堆疊框:`_finite` 本身,加上 `all(...)` 的產生器。
- 這一行在 `_load` 的 try 外面,RecursionError 沒有被接住,直接穿出 `parse_reply` 和 `rule_mining_eval.run`。

**攻擊重現**
- 攻擊輸入:模型回覆(1110 位元組,遠低於 6000 的上限)
  `{"version":1,"suggestions":[{"clauses":[],"direction":` + `[`×500 + `]`×500 + `,"support":1,"counterexample":0,"confidence_note":"a"}]}`
- 預期:`Parsed(rejected="unparsable")`,單一種子記「無效提交 1」。
- 實際:
  - `rule_mining_check.parse_reply` 丟 `RecursionError`。
  - 用 `prepare(15001)` 加假的 `ask` 走 `rule_mining_eval.run`,同樣整支崩潰。
  - 實測 400 層正常,500 到 2900 層全部崩潰。
  - 呼叫堆疊越深,門檻越低:增量 3 的命令列從更深的地方呼叫,更少層就會爆。
- 放大:
  - 即時加錄製模式下,這份回覆會照樣存成錄製檔。之後每次重播都穩定崩潰,這個種子等於永久跑不完,除非換展示編號重錄。
  - 已經付掉的即時呼叫成本也拿不回核對結果。
- 測試缺口:現有測試用的是 `"[" * 5000`。那是不平衡的 JSON,解析時就丟 ValueError,碰不到這條路徑。

**建議修法**
- `_finite` 可以直接刪掉:`parse_float=_float` 和 `parse_constant=_constant` 已經在解析當下擋掉 `inf`、`NaN`,整數本來就不會是非有限數。
- 如果要留,就把它移進 try、一起接 `RecursionError`,或改成用明確堆疊的迭代寫法。
- 補一條測試:平衡的 `[`×n`]`×n(n 取 500 和 2900)要回 `unparsable`。

---

## F2 回覆裡的孤立代理字元讓 `_note`/`_load` 丟 UnicodeEncodeError

severity: major
blocking: 是
引句:「            and len(value.encode("utf-8")) <= v.NOTE_BYTES」
查證 file: `src/rtb/eval/rule_mining_check.py:176`(整份層級同型問題在 `src/rtb/eval/rule_mining_check.py:128`)

**問題**
- JSON 允許 `"\ud800"` 這種逸出,解出來是 Python 的孤立代理字元。
- `_note` 在查控制字元之前,先做 `value.encode("utf-8")`。孤立代理字元在這一步會丟 `UnicodeEncodeError`。
- `UnicodeEncodeError` 雖然是 ValueError 的子類別,但 `_suggestion`、`_deduplicated`、`parse_reply` 都沒有接住它,所以直接崩潰,沒有記成「只丟該條 bad_note」。
- `_load` 開頭的 `len(text.encode("utf-8"))` 也在 try 外面。原始文字本身含孤立代理字元時同樣崩潰。

**攻擊重現**
- 攻擊輸入 A(模型在即時回覆裡就打得出來,只是 6 個 ASCII 字元):
  `{"version":1,"suggestions":[{"clauses":[{"condition":"day_type","threshold":"day_type:weekday"}],"direction":"improve","support":1,"counterexample":0,"confidence_note":"\ud800"}]}`
  - 預期:該條記 `bad_note`,其餘照常。
  - 實際:`parse_reply` 和 `rule_mining_eval.run` 都丟 `UnicodeEncodeError`,整批中斷。
- 攻擊輸入 B(竄改錄製檔):把 `text` 欄寫成 `"\ud800"`。
  - `load_recording` 用 `json.loads` 讀進來,它的型別檢查照樣放行。
  - 預期:整份拒絕 `unparsable` 或 `reply_too_large`。
  - 實際:`_load` 第一行就丟 `UnicodeEncodeError`。
- 跟 F1 一樣,輸入 A 存成錄製後,重播會穩定崩潰。

**建議修法**
- `_load`:把量位元組這一步包進 try,`UnicodeEncodeError` 一律當 `unparsable`。或者一開始先用 `text.encode("utf-8", "strict")` 驗一次,失敗就整份拒絕。
- `_note`:先查字元類別,把 `Cs`(代理字元)跟 `Cc`、`Cf` 一起擋掉,再量位元組;或者 encode 失敗就回 False。
- 保險一點,`_suggestion` 對單條的任何 `ValueError` 或 `TypeError` 都收成 `Invalid`,不讓它往外穿。
- 補兩條測試:note 含 `\ud800` 要回 `bad_note`;原始文字含孤立代理字元要整份拒絕。

---

## 考慮過、沒有可利用的洞

### ① 冒用或借道
- 白名單只精確加了窄入口和探勘執行器,範圍如下:
  - `GATE_USERS` 只加 `rule_mining_model`。
  - `CALL_MODEL_USERS` 和 `SENDING_ENTRIES` 只加這兩支。
  - `CALLER_USERS["RULE_MINING"]` 只有窄入口,加上只算錄製鍵的 `rule_mining_recordings`。
- `rule_mining_recordings` 雖然綁了 `mc` 模組物件,但它不在 `CALL_MODEL_USERS`。任何 `.call_model` 或 `.open_gate` 屬性取用,都會被 `backend_offenders` 的名字掃描抓到。這跟既有的 `record`、`investigation_report` 同一種型態。
- 別的評估模組如果匯入窄入口或用 `suggest`/`gate_ask`,會被 `rule_mining_senders()` 抓到。
- 各層的 ruff 禁令(含 pyproject)都加了窄入口。ops 的 ruff 沒加,但 ops 本來也沒禁 `ai_judge`,由 AST 測試的 `SENDING_ENTRIES` 把關。

### ② 上限
- `RULE_MINING` 在 `CAPPED_CALLERS` 裡。
- `reserve` 在同一個 immediate 交易裡,重新讀已用額、照當下的價目重算預留、判上限、寫預留列,併行的預留會被序列化。
- 即時模式下給 `ledger` 會被 `GateRefused`,帳一定寫進帳號家目錄那一本,沒辦法把帳導到別處逃掉上限。

### ③ 即時模式觸發
- 窄入口不讀環境,`environ` 由呼叫端給。
- 即時要同時滿足:`RTB_MODEL_LIVE=1`、有展示編號、找得到 claude、啟用紀錄有效、價目表沒過期。
- 缺錄製時 `_replay` 丟 `NoRecording`,不會自動改走即時。

### ④ 自由文字外帶
- `Checked` 只帶程式重算的數字。
- `Invalid` 只帶經過語彙驗證的正規化鍵。
- `Reply.problem` 只放模型用戶端固定的訊息。
- 原始 `text` 只留在 `SeedRun.reply` 裡,增量 2 沒有任何輸出點。增量 3 寫報告時要守住不直接印它。
- 其他資源耗盡都擋住了:超長整數(大約 4300 位數以上,解析時丟 ValueError 被接住)、超大浮點數(`1e400` 會被 `_float` 擋)、超過 K 條、超過 6000 位元組。

### ⑤ 匯入閉包
- 實測探勘執行器的閉包,沒有到 `inbox_client`、`dsp_client`、`rtb.executor`、`rtb.dsp`。
- 閉包裡有 `analyzer.flow`、`policy`、`task_store`、`domain.proposal`,但它們是從增量 1 的 `rule_mining_baseline` 經 `eval.scoring` 進來的。這不是本增量新增,而且只是匯入,沒有呼叫。

### ⑥ 錄製檔竄改
- 竄改過的錄製檔,只要型別都對,仍然會被當成有效錄製(既有設計沒有簽章)。
- 不過這條路上的數字全部由程式在同一份探索集上重算,偽造的支持數或反例數會被判「無法核對」。
- 真正能打穿的竄改是 F1 和 F2 的崩潰輸入,已經列在上面。

新發現:F1、F2(其餘無)
