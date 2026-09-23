severity: minor

# 架構對齊審查:code-phase7-inc5 / r1-snapshot.patch

範圍:`src/rtb/executor/inbox_server.py` 抽出 `_parse_or_reject`、新增 `_log_invalid_proposal`(`sys.stderr.write` 寫一行);`tests/executor/test_inbox_reject_log.py`。
對照:`ea1740e` 版本、`src/rtb/httpkit.py`、`src/rtb/dsp/server.py`、全庫 `sys.stderr`/`print`/`logging` 用法、`~/.claude/skills/python-idioms/SKILL.md`、知識圖譜 `Systems/提案收件口.md`。

**圖譜核對(必要,先做)**:`Systems/提案收件口.md` 裡已有一條寫齊 `[since:2026-09-23]` `[retire:…]` 的 `RULE:`,逐字描述這次改動——「格式不合法的提案被拒時…回應之前在標準錯誤寫一行『invalid_proposal errors=<錯誤類別>』…出處:`[[Projects/RTB_Phase7提示注入與信任邊界_計劃]]` 增量 5(2026-09-23 使用者裁定選 b)」,並列了與這次新測試同名的三個 `[test:]`。也就是說「在收件口這層加一行標準錯誤紀錄」本身是**已裁定的設計**,不是這次審查要重新質疑的架構決定;本審查只判「落地方式」跟既有寫法合不合。程式碼與這條 RULE 一致,沒有矛盾要裁。

---

## 一、分層與依賴方向

- 紀錄邏輯(`_log_invalid_proposal`)寫在收件口這層(`inbox_server.py`),不是共用伺服器層(`httpkit.py`)。`httpkit.py` 只認「預期外例外」(未捕捉到的 500),用 `traceback.print_exc(file=sys.stderr)`(`src/rtb/httpkit.py:172`、`src/rtb/httpkit.py:174`、`src/rtb/httpkit.py:88`);它完全不懂 `parse_proposal` 的錯誤分類(`unknown_field`、`too_large`、欄位級 `:missing`/`:invalid` 這些是收件口/領域層的詞彙,見 `src/rtb/domain/proposal.py:211`、`src/rtb/domain/proposal.py:234`、`src/rtb/domain/proposal.py:239`)。把「拒收原因分類」的知識放在懂這套分類的那一層,是對的方向——沒有跨層直呼,`_log_invalid_proposal` 也沒有反過來被 `httpkit.py` 呼叫。
- 對照 `src/rtb/dsp/server.py`:它完全沒有對 4xx 拒收做任何標準錯誤紀錄(`DspHandler` 只靠 `ERROR_TABLE`/`map_exception` 回應,見 `src/rtb/dsp/server.py:93`-`src/rtb/dsp/server.py:99`),代表「收件口這層自己多記一行拒收紀錄」是 Phase 7 這次新加的行為,但這正是圖譜那條 RULE 裁定的範圍(只對收件口的提案本文拒收記,DSP 的操作拒收不在這次裁定內),不是「兩邊本該一致卻不一致」。
- `_parse_or_reject` 把 `self.read_json()` 的呼叫從 `handle_request` 搬進新方法,呼叫順序(Host→路由→故障標頭→Origin→Content-Type→讀本文→解析→收件交易)沒有變動,對照 `ea1740e` 版本的 `handle_request` 逐行確認過,順序完全一致。

判定:對齊,無跨層直呼。

## 二、命名與錯誤處理

- `sys.stderr.write(f"...\n")` 這個寫法本身就是專案既有慣例,不是新起一套:同檔舊版啟動失敗訊息(`拒絕啟動:{error}`,`src/rtb/executor/inbox_server.py:173`)、`src/rtb/executor/runner.py:71`、`src/rtb/executor/runner.py:76`、`src/rtb/executor/runner.py:131`、`src/rtb/executor/runner.py:137`、`src/rtb/executor/runner.py:144`、`src/rtb/executor/runner.py:148`、`src/rtb/executor/runner.py:153` 全部是同一種「`sys.stderr.write(f"...\n")` 固定格式行」。新函式跟同一個 `executor` 套件裡最常見的寫法一致。
- 唯一跟這批既有呼叫不同的一點:新函式多呼叫了一次 `sys.stderr.flush()`(`src/rtb/executor/inbox_server.py:67`)。上面列的 `runner.py` 與舊版 `inbox_server.py` 的 `sys.stderr.write` 呼叫都沒有接 `.flush()`。這是本次唯一抓到的落地方式落差。
- 例外處理:`_parse_or_reject` 裡 `except RequestRejected as rejection: _log_invalid_proposal(...); raise` 是「接住做副作用、原樣往上丟」,不吞例外、不改型別。這個寫法在專案裡是既有慣例,對照 `src/rtb/httpclient.py:73`、`src/rtb/httpclient.py:76`-`src/rtb/httpclient.py:77`、`src/rtb/sqlitekit.py:36`、`src/rtb/sqlitekit.py:39`、`src/rtb/analyzer/flow.py:155`、`src/rtb/analyzer/instrumented.py:40`、`src/rtb/analyzer/instrumented.py:87`、`src/rtb/dsp/store.py:232`、`src/rtb/dsp/store.py:267` 都是同一個模式(側寫/釋放資源後裸 `raise`)。命名 `_parse_or_reject`、`_log_invalid_proposal` 也符合既有的私有輔助方法/函式命名(底線開頭、動詞片語),跟 `DspHandler._route`、`_commit`、`_apply_fault_before_commit` 同一套風格。

判定:除了 `.flush()` 這一點,命名與錯誤處理跟既有寫法一致。

## 三、第二種做法

- 全庫 grep(`grep -rn "sys\.stderr\|logging\.\|print("  src/`)確認:**專案完全沒有 `import logging`、沒有任何 `logging.*` 呼叫**,標準錯誤輸出一律是 `sys.stderr.write`(固定格式行)或 `traceback.print_exc(file=sys.stderr)`(未預期例外)兩種既有寫法,`print(...)` 只用在啟動就緒訊號(`PORT=...`、`READY`)。這次新函式用的是第一種既有寫法,不是繞過或新開一套 logging 機制,也沒有漏用一個「本來就有但這次沒用」的工具——因為專案裡根本沒有 `logging` 模組可用。
- `python-idioms` skill 的 R9 建議「日誌用 logging,不用 print」,但 skill 本身聲明「框架選擇…不在此裁——查該專案圖譜」,而且 R9 針對的是 `print`/f-string 組字串這類反例,不是這個專案已經全面採用、且合約層(`Systems/提案收件口.md`、`Systems/共用行程基礎.md`)都認可的「固定格式 `sys.stderr.write` 行」慣例。這裡以圖譜與既有程式碼(專案自己的做法)為準,不當作對齊問題。

判定:沒有引入第二種做法。

---

## 作者表態查核:py-memory

作者聲稱 `satisfied`,證據 `src/rtb/httpkit.py:212`(讀本文前就有大小上限;這次只是把原本那行讀本文搬進小方法)。核對結果:**屬實**。

- `src/rtb/httpkit.py:211`-`src/rtb/httpkit.py:212`:`read_json` 在真正呼叫 `self.read_exactly(int(raw_length))` 讀本文之前,先檢查 `Content-Length`,超過 `self.max_body_bytes`(`MAX_BODY_BYTES = 64 * 1024`,`src/rtb/httpkit.py:21`)就直接 `raise RequestRejected(413, "body_too_large")`,不會把超額本文讀進記憶體。這行邏輯在 `ea1740e` 舊版與這次新版完全沒動,`_parse_or_reject` 只是把「呼叫 `self.read_json()`」這一步從 `handle_request` 搬進去,沒有新增讀本文的路徑,也沒有繞過這道上限。
- 額外驗了一層作者沒提但相關的邊界:`_log_invalid_proposal` 寫進標準錯誤的那行字串,其長度也是有界的——來源只有兩種:(a) `_parse_or_reject` 讀本文失敗時的 `rejection.code`,是固定詞(`body_too_large`/`chunked_not_supported`/`invalid_content_length`/`invalid_json`/`request_timeout`/`incomplete_body`);(b) `parse_proposal` 的 `errors`,而 `parse_proposal` 自己對本文總大小有 `MAX_PAYLOAD_BYTES = 16 * 1024`(`src/rtb/domain/proposal.py:25`)的檢查,超過就回固定字串 `f"too_large:{total}"`(`src/rtb/domain/proposal.py:211`),且 `_TAILED_ERRORS`(patch 新增,`src/rtb/executor/inbox_server.py` 頂部)把 `unknown_field:<鍵名>`、`too_large:<位元組數>`、`unexpected_failure:<例外型別名>` 這三種帶攻擊者可控或無界尾巴的類別,在寫入標準錯誤前都截斷成冒號前的固定詞。所以這行紀錄本身也不會被攻擊者送的內容撐大——這點補強了作者的 py-memory 表態,不是矛盾。

⚠ 交編排者:除了 `.flush()` 這個極小的落地差異外,沒有抓到其他分層、命名、錯誤處理或第二做法的問題;圖譜那條 `RULE:` 已經把「加這行紀錄」本身的架構決定確認過(裁定選 b),本審查沒有再往回翻案的依據。

---

## F1 多呼叫了一次 `sys.stderr.flush()`,同套件其他 `sys.stderr.write` 呼叫都沒有這一步

severity: minor
blocking: 否 — 只是落地方式跟同檔、同套件既有呼叫點不完全一致(多一行 `.flush()`),不影響分層方向、不是另起一套紀錄機制;stderr 預設非緩衝,多餘但無害。
引句:「sys.stderr.write(f"invalid_proposal errors={','.join(classes)}\n")
    sys.stderr.flush()」

---

不對齊共 1 條,其中 major 0 條。
