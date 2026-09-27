severity: minor

# 代碼審 Phase 15 增量 2 第 2 輪:資安-opus

席位:資安-opus(外部審查、攻擊者視角,全新席)。範圍:r2-delta.patch、r1-資安-opus.md,以及 `rule_mining_check.py`、`rule_mining_eval.py`、`rule_mining_model.py` 和三支測試。

做法:
- 重現腳本都放在審查員自己的暫存目錄,repo 除了本報告沒有改動。
- 模型回覆一律用假的 `ask` 或測試用的假後端注入。錄製檔、帳本都在暫存目錄。
- 沒有呼叫 claude、沒有設 `RTB_MODEL_LIVE`、沒有碰 ~/.rtb。
- 三支 rule_mining 測試全過(19 passed,Python 3.14.6)。

## 驗收

F1:修好 — `_finite` 改成顯式堆疊。前輪原始輸入(`direction` 為平衡 `[`×500`]`×500)以及 1000、2000、2900 層,`parse_reply` 都回「只丟該條 unknown_direction」;頂層平衡 3000 層回 top_not_object。走假後端「即時加錄製 → 重播」,兩邊結果相同,都不崩。

F2:修好 — 兩個輸入都驗過:
- 輸入 A(說明欄 `"\ud800"`):`_note` 先驗字元類別(Cs),不會先 encode,回 bad_note。錄進去再重播,結果一樣。
- 輸入 B(錄製檔 `text` 被竄改成孤立代理字元):`_load` 量位元組時接住 UnicodeEncodeError,整份拒絕 unparsable。
- 孤立代理字元出現在鍵、方向、條件代碼時,也都走既有的 unknown_key、unknown_direction、bad_code,不崩。

## 新發現

### N1 錄製檔外殼塞超深巢狀,重播時 `load_recording` 丟 RecursionError,整批崩潰

severity: minor
blocking: 否
引句:「報告 F2 輸入 B:錄製檔的文字被竄改成孤立代理字元,重播整份拒絕、不崩」
查證 file: `src/rtb/modelrecording.py:269`(同型的還有 `src/rtb/modelrecording.py:136`、`:330`,都只接 `ValueError` 系)

**問題**
- 本輪修法守的是回覆文字(`text` 欄)。錄製檔的 JSON 外殼是 `load_recording` 先用 `json.loads` 整份讀進來。
- 這一步只接 `(OSError, ValueError)`。Python 3.14 的 `RecursionError` 不是 `ValueError`。
- 回覆文字受 6000 位元組限制,最深大約 3000 層。錄製檔沒有大小上限,十幾萬層就會讓 C 掃描器的堆疊保護丟 RecursionError。
- 例外穿出路徑:`load_recording` → `modelclient._replay` → `rule_mining_model.suggest`(只接 `ModelCallFailed`)→ `rule_mining_eval.run`。
- 模組自己的註解說錄製檔「存在版本庫裡、誰都改得到」,也寫明不符就丟「沒有錄製」。本輪也把錄製檔竄改(F2 輸入 B)列入防守範圍。這個輸入是同一個威脅模型下的漏網之魚。

**攻擊重現**
- 攻擊輸入:把對應鍵的錄製檔 `<key>.json` 換成下面內容(約 300 KB)。在合法錄製後面加一個 `"pad"` 欄,效果一樣,因為解析發生在欄位驗證之前。
  `{"pad":` + `[`×150000 + `]`×150000 + `}`
- 預期:`NoRecording`,也就是「錄製檔讀不懂」。執行器記「呼叫失敗」,不解析、不崩。刪掉錄製檔也會得到這個結果。
- 實際:
  - `rme.run(case, rme.gate_ask(config(...)))` 丟 `RecursionError: Stack overflow (used 16352 kB) while decoding a JSON array`,整支中斷。
  - 直接呼叫 `load_recording` 時,150000、200000、300000、500000 層全部丟 RecursionError。100000 層還解得過。
- 對照:外殼其他竄改都有被收住,不崩。
  - `latency_ms=10**400`:收成 transient。
  - `list_nanousd=10**30`、`input_tokens=10**30`:照常重播。
  - `text` 50 MB:回 reply_too_large。

**為什麼只給 minor、不擋**
- 這段是 Phase 13 共用的 `modelrecording`,不在本增量 delta 內。
- 能改錄製檔的人也改得到原始碼。效果只是讓批次當掉(可見、可查),不會偽造核對結果。

**建議修法**
- `load_recording`、`_batch_of`、`recording_files` 讀檔那三處改成接 `(OSError, ValueError, RecursionError)`,統一轉成「沒有錄製」或「讀不懂」。
- 或者讀檔前先檢查大小。正常錄製檔的上限可以抓回覆上限加欄位開銷,例如 64 KB,超過一律當讀不懂。
- 在 `test_rule_mining_hostile_recordings_replay_without_crashing` 補一條:外殼 `[`×200000 要走「呼叫失敗」。

新發現:N1(minor、不擋);其餘無

## 考慮過、沒有可利用的洞

### 回覆內容的資源耗盡(都在 6000 位元組上限內實測,每份不到 1 ms)
- **超長字串**:5800 字的說明、900 個 `一` 逸出,都回 bad_note。
- **大量鍵或條目**:700 個鍵、1000 個重複鍵,超過 6000 位元組回 reply_too_large;1400 個 `{}`、1900 個 `[]` 回 too_many_suggestions。
- **深巢狀物件**:物件每層 6 位元組,500 層還在上限內,回 unknown_direction;1000 層以上會先超過位元組上限。
- **巢狀放在其他欄位**:放在 support、clauses、condition、suggestions 的平衡 2800 到 2900 層,分別回 bad_count、bad_clauses、bad_code、suggestion_not_object。
- **大整數**:
  - 640、641、4300 位,以及負 4300 位,都回 bad_count;4301 位回 unparsable;`version` 4300 位回 wrong_version。
  - `int(Decimal(...))` 轉 4300 位大約 0.6 ms。6000 位元組裡最多只放得下一個。
  - 另測九個 640 位整數,沒有放大效果。
- **浮點數**:4000 位小數回 bad_count;`1e99999999999999999999` 回 non_finite;極小指數會變成 0.0,回 bad_count。

### 控制字元與編碼
- 字串裡的原始 NUL、字串外的 NUL、BOM 前綴,都被 `json` 嚴格模式拒絕,回 unparsable。
- 逸出的 `\u0000` 在說明欄回 bad_note,在鍵名回 unknown_key。
- U+2028、非字元 U+FFFF、私用區、未指派字元可以通過說明欄檢查。不過說明不會被帶出任何輸出(`Checked` 不帶說明),本增量沒有可利用的面。增量 3 寫報告時仍要守住「不印原始說明」。

### 核對階段(`verify` 沒有防線包著)
- 窮舉三個種子 × 全部封閉條件 × 兩個方向 × 三組自報數字(照實、0/0、九位數),共 1008 份回覆全部走 `rme.run`。
- 0 次崩潰,結果分布:valid 298、count_mismatch 672、below_floor 38。
- 能進到重算的鍵都已經過語彙正規化,`directional` 在 `directed=0` 時回 None,不會除以零。

### 「意外就拒絕」防線吞例外
- 用 monkeypatch 注入例外實測:
  - `KeyboardInterrupt`、`SystemExit` 照樣往外丟,沒被吞。
  - `AttributeError`、`KeyError`、`AssertionError` 也照樣往外丟。
- 會被吞的是兩類:
  - 解析期的 `MemoryError` 和程式 bug 型的 `TypeError`,會變成 unparsable。
  - 逐條檢查裡的 `TypeError`、`ValueError`,會變成 unreadable_suggestion。
- 攻擊者觸發不到這兩道防線:
  - 6000 位元組的輸入造不出 MemoryError。
  - 逐條檢查的每一步都先做型別判斷(`isinstance`、`CODE.fullmatch`、對 tuple 用 `in`),實測各種型別都沒碰到防線。
- 所以這兩道防線屬於「可能把未來的 bug 藏成無效提交」的品質風險,不是可利用的洞。

### 殘餘觀察(不可利用,給增量 3 參考)
- 同一份 2900 層巢狀的回覆,在主執行緒回「只丟該條」(clause_count)。在堆疊 256 KB 或 64 KB 的子執行緒,C 掃描器會先丟 RecursionError,結果變成「整份拒絕」(unparsable)。
- 不會崩,但判定結果跟執行緒有關。增量 3 如果在子執行緒或執行緒池裡解析,錄製當下和重播時的判定可能不一致。
