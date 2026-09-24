severity: major
以下是完整報告全文(已依格式規則撰寫):

---

severity: major

### 1. 唯讀稽核金鑰共用 X-Capability 標頭,把「簽章憑證」與「原樣共用密鑰」兩種憑證語意混在同一個標頭裡

severity: major
blocking: 是

引句:「金鑰放在能力憑證那個標頭,原樣比對、固定時間。」
引句:「不沿用寫入憑證的驗證:那套聲明綁一個廣告、一把冪等鍵、一個寫入動作,又是用簽發金鑰簽的」
引句:「headers = None if audit_key is None else {ClientHeader.CAPABILITY: audit_key}」

觸發情境:維運套件(`slo.py`/`sli.py`)呼叫 `side_effects.get_json()` 讀 DSP 的兩支列操作端點(`/operations/since/...`、`/operations/after/...`)時,把 `RTB_DSP_AUDIT_KEY` 的原始密鑰字串直接塞進 `ClientHeader.CAPABILITY`(即 `X-Capability`)標頭送出;DSP 端 `DspHandler._require_audit_key()`(`governance/review-reports/code-phase9-inc3/r2-delta-src.patch` 第 131-140 行,對應 `src/rtb/dsp/server.py:166-181`)也是從同一個 `CAPABILITY_HEADER` 讀出這段原始字串,直接用 `hmac.compare_digest` 跟伺服器保存的原始金鑰位元組比對。

會出什麼錯的行為:這個專案對「憑證怎麼驗」只有一套既有慣例——`src/rtb/capabilitykit.py`(模組文件開頭:「寫入能力憑證的格式機制…正規化、簽章、拆解」)定義的簽發/驗證協定:憑證是「聲明 JSON 正規化後 base64url + HMAC 簽章 base64url,中間點連接」的結構化 token,經 `decode()`(`src/rtb/capabilitykit.py:110-120`)先驗簽章、再解析成聲明,附帶時間窗與範圍檢查,並用型別化例外(`TokenRejected`/`CapabilityMissing`/`CapabilityInvalid`…)分辨失敗原因;`src/rtb/dsp/capability.py:96-115` 的 `verified_claims()` 正是這套協定的呼叫入口。人工核可金鑰(`APPROVAL_KEY_ENV`)雖然是另一把獨立金鑰,但走的也是同一套「簽一張 token」機制(`src/rtb/executor/approve.py` 呼叫 `approval.issue()` 簽章),不是原樣比對。這次新加的稽核金鑰卻是完全不同的第三種機制:沒有聲明結構、沒有時間窗、沒有簽章——就是把共用密鑰原樣塞進標頭、伺服器原樣常數時間比對,連 `presented.encode("utf-8", "surrogateescape")` 這種處理任意位元組的寫法都是全專案唯一一處(`decode()` 只處理限定字元集的 base64url 段,不需要這種寫法)。更關鍵的是,它把這個全新機制套在同一個 `X-Capability` 標頭上,而這個標頭名稱在 `src/rtb/httpclient.py:24-30` 的 `ClientHeader.CAPABILITY` 定義處明白寫著「寫入能力憑證。字串照抄共用格式模組的標頭名稱」——也就是說,這個標頭的既有契約就是「裡面裝的一定是 capabilitykit 簽出的 token」。現在同一個標頭在兩支列操作端點上卻裝著結構完全不同的原始共用密鑰,任何依既有契約假設「看到 X-Capability 就能丟給 `decode()`/`verified_claims()` 解析」的讀者或未來程式(例如共用的請求記錄、標頭稽核、或日後要在同一支 handler 上加別的驗證邏輯)都會踩到這個隱藏的語意分歧;`_require_audit_key()` 的註解本身也承認這是刻意的例外(「不沿用寫入憑證的驗證」),但只解釋了為何不能重用簽發金鑰,並未說明為何要沿用同一個標頭名稱,而不是另開一個專屬標頭(`ClientHeader` 本來就是「要加新的就在這裡加一個成員」的封閉列舉,加一個新成員毫無架構障礙)。這是引入第二種憑證做法、又借用第一種做法專屬的標頭語意,屬於架構對齊要擋的「引入第二種做法」。

建議修法:在 `ClientHeader` 新增一個獨立成員(例如 `AUDIT_KEY = "X-Dsp-Audit-Key"`),`side_effects.get_json()` 與 `DspHandler._require_audit_key()` 都改用這個新標頭,讓「簽章能力憑證」與「唯讀稽核共用密鑰」在標頭層級就分開,不共用 `X-Capability` 這個已有明確契約的名字;既有的直接比對(不走 `decode()`)可以保留,因為那部分本來就有正當理由(維運套件不該拿到能簽寫入憑證的金鑰),問題只在於標頭名稱的語意混用。

---

## 其他檢查結果(非另立發現)

- **`read_key` 讀法**:`AUDIT_KEY_ENV` 一律經 `capabilitykit.read_key(environ, AUDIT_KEY_ENV)` 讀(`src/rtb/dsp/server.py` 啟動程式、`src/rtb/ops/slo.py:271` 的 `run()`),簽名與既有 `APPROVAL_KEY_ENV` 用法(`src/rtb/executor/approve.py:45`、`src/rtb/executor/runner.py:156`)一致,只有啟動程式讀環境變數,沒有另立讀法。
- **穩定欄與結束代碼 5**:`side_effects.Tally.stable`、`sli._tally`/`end_to_end_handoff`、`slo.period_tally`/`SloStatus.stable`、`EXIT_UNSTABLE = 5` 這條線,跟增量 1、2(`trace.py:129` 的 `stable` 欄、`metrics.py:70` 的 `EXIT_UNSTABLE = 5`)用的是同一套「三輪不同回最後一輪、標不穩定、結束代碼 5」慣例,`slo.py` 註解也明寫「沿用指標的固定結束代碼」,沒有另立一套。
- **逐條隔離例外**:`slo.py:evaluate()` 新增的「一條讀不到就記例外、其他五條照算」(`except Exception as exc`)雖然是 `ops` 套件裡第一次出現這種逐條隔離,但跟全專案既有的「安全邊界最後一道保底,捕捉例外把型別記進結果、不讓例外穿出去」慣例(`src/rtb/domain/proposal.py:277-279`「安全邊界的最後一道保底,錯誤型別記在結果裡」)是同一種模式的延伸,不是另立一套。
- **提交時間 UTC 格式化**:`dsp/store.py` 新增的 `commit_text()` 用 `value.astimezone(UTC).isoformat()`,跟同檔案裡從 Phase 1(`34eb749`)就存在、至今未改的 `_utc_now()`(同樣 `.isoformat()`)是同一種寫法,只是把既有格式抽成具名函式供 `_not_before_last()`/`operation_cursor_query()` 共用,沒有另外發明格式。全專案其他模組(`metrics.py`、`trace.py`、`side_effects.py`、`attempt_store.py`、`inbox_store.py`、`task_store.py`)另有一套 `strftime("%Y-%m-%dT%H:%M:%S.%fZ")` 慣例,但那是 DSP 以外「內部模組」之間共用的格式;DSP 作為刻意不依賴內部慣例的外部系統模擬器,從一開始就自成一套 `.isoformat()` 寫法,`commit_text()` 延續的正是 DSP 自己既有的寫法,不是新引入的第二套。
