preflight-4: ran

## ① 未定義的詞

- 位置:第 46 行「快取以解析後的絕對路徑為鍵」。
  問題:「解析後的絕對路徑」沒說是用什麼方法解析——`Path.resolve()`/`os.path.realpath()` 會跟隨路徑中每一段的符號連結,`os.path.abspath()` 則不會。這份設計花了整整一段強調「不跟隨符號連結」是安全讀檔的鐵則(第 5、43 行),快取鍵的解析方式若跟這個原則不同調,至少該講清楚是哪一種,免得實作時順手選了會跟隨連結的那個。
  程式實際:目前 `capability_signer.py` 沒有任何路徑解析呼叫(`_read_config_securely` 直接用呼叫端傳入的 `path`,`os.open(path.parent, ...)` 本來就會讓作業系統照正常規則解析 `path.parent` 裡的中繼目錄,所以「不跟隨符號連結」目前其實只守住檔案本身這一段,不含父目錄鏈——這件事現況本來就如此,不是快取新增的破口)。
  建議修法:在這行後面補一句寫死用哪個函式(建議 `os.path.abspath`,不觸碰中繼目錄的符號連結語意,維持跟現況一致),或明講「快取鍵只是識別身分用,取法不影響安全檢查的結果」。

## ② 壞引用

無。逐一核對:
- `[S340]` → `docs/.../Projects/RTB_Phase6權限護欄與總曝險_計劃.md` 有此節點,存在。
- `[[Issues/F7端到端在CI上偶爾超過60秒]]` → 檔案存在,內容(294 萬列、15.5 秒、3000 廣告、900 萬次、7.8 秒等數字)跟這份設計第 22-24 行完全對得上,不是編造的。
- `lands_in` 的兩個節點(`Systems/外部寫入嘗試紀錄`、`Systems/寫入能力憑證`)都存在且 `about_code` 有掛。
- 「phase9-inc4 084ef22」→ `git show 084ef22` 存在(`test: 稽核守衛九張表共用一套判定,認得 format 與百分比組字串`)。
- `[S680]`–`[S686]` 是這份設計新提的合約編號,尚未出現在別處也合理(還沒過設計審、還沒建節點)。

## ③ 範圍自相矛盾

無。檢查過「設定檔 64 KB 上限、不隨歷史長」跟「整體成本接近平方成長」是否互斥:兩者分工清楚——加總的成本隨窗內預留數(歷史)成長是平方成長的主因,設定檔驗證的成本只跟設定檔大小成正比、跟歷史無關,只是「線性地」被重複做了 3000 次,兩段敘述不衝突。「不改逐筆明細與可觀測查詢」與後面新算法設計之間也沒有踩到彼此。

## ④ 機械宣稱驗語意

**命中 1:「已用額度的唯一入口」(第 29 行)**

- 修改前(設計現況原句):「已用額度的唯一入口是嘗試紀錄模組的「已用額度」函式,它把「逐筆明細」函式回的每一列加總」
- 程式實際:`src/rtb/executor/observability.py:128-142` 的 `aggregate_audit`(總曝險稽核明細,事故 F7 用的那個查詢)自己呼叫 `attempt_store.aggregate_holdings(...)` 拿到逐筆明細後,直接 `used = sum(row.amount for row in holding)` 自己加總,**沒有經過 `aggregate_used`**。也就是說「已用額度」這個數字目前至少有兩條路徑算出來:`aggregate_used`(給開始一筆判門檻、放行判斷用)和 `aggregate_audit` 裡這行手動加總(給稽核明細用),只是兩邊用的都是同一份 `aggregate_holdings` 明細,數字碰巧會一致,但「唯一入口」這個講法不成立。
  對這份設計的影響:不算致命,因為①設計本身聲明「逐筆明細函式不動」(第 40 行),`aggregate_holdings` 維持 Python 逐列加總,`aggregate_audit` 這條路徑不受影響;②`aggregate_used` 改成資料庫端加總後,只要跟 `aggregate_holdings` 的規則同步(S680 就是在守這件事),`aggregate_audit` 的手動加總不會跟著跑掉。但現況描述本身失真,而且將來如果有人真的照著「唯一入口」這句話去做,可能誤以為只要盯住 `aggregate_used` 一處就管控了所有「已用額度」的算法,漏掉 `aggregate_audit` 這條稽核路徑。
- 建議修改後:把第 29 行改成類似「已用額度目前有兩條路徑用到同一套逐列計入規則:判門檻走嘗試紀錄模組的「已用額度」函式(對外把「逐筆明細」函式的每一列加總);稽核查詢(`observability.aggregate_audit`)另外自己對「逐筆明細」的結果加總。這份設計只改判門檻那條路徑,稽核那條維持原樣、規則來源相同,兩邊數字理論上仍會一致。」並在「入口名稱與位置不變」那句(第 40 行)後補一句「`aggregate_audit` 的獨立加總不受影響,不用跟著改」。

**核對到、確認語意跟程式一致的項目(逐條列在下面「核對到但沒問題」)**

## 核對到但沒問題的項目

- 「三支既有並行測試用替換入口的方式造競態」:確認為 `tests/executor/test_stale_decision.py`、`test_aggregate_limit.py`、`test_approval.py` 三支對 `attempt_store.aggregate_used` 做 `monkeypatch.setattr`,数目與說法都對(`test_read_only.py`、`test_first_row_materials.py` 只是引用欄位名字串,不是替換函式)。
- 舊算法計入規則逐條核對 `_counted`/`_counted_rows`(attempt_store.py:687-709):已驗證 24 小時窗、未結案不論多久都算(含轉人工/ESCALATED,因為它不在 `TERMINAL_STATES` 裡,設計未特別點名但邏輯上已涵蓋、不矛盾)、失敗不算(失敗是終點狀態、不落在已驗證索引也不落在未結案分支)、舊列沒有租戶時 `update_budget` 算進每個租戶(不分加減,全額,設計自己講的「分不出加減,寧可多擋」)、非 `update_budget`(含暫停)回 0、讀不出新預算丟 `CorruptedAttemptRow`——全部跟設計描述一致。額外查過「有租戶但金額為空」這個分支:目前只有 `begin()` 會寫 `tenant`/`reserved_amount`,兩欄永遠同時來自同一個 `Reservation`,不會出現有租戶、金額卻是空值的列,`_counted` 裡的防禦判斷($amount is not None$)目前是死碼但無害。
- SQLite 整數加總溢位行為:在 `/tmp/f7check` 用 `.venv` 外的系統 `python3`(sqlite 3.53.3)實測兩個 `2**63-1` 相加後 `SELECT sum(v)`,拋出 `sqlite3.OperationalError: integer overflow`,不是悄悄截斷,跟設計第 39 行一致;`MAX_INT = 2**63-1`(`src/rtb/domain/proposal.py:32`)也跟 SQLite 的 64 位元有號整數上限對得上。
- 安全讀檔每一道檢查(`capability_signer._read_config_securely`,第 63-86 行):開目錄、`O_NOFOLLOW` 開檔案、確認是一般檔案才恢復一般讀取、擁有者與群組/其他可寫位元檢查(目錄與檔案各查一次)、`MAX_CONFIG_BYTES + 1` 探測是否超過上限——順序與內容跟設計描述完全一致;設計對快取的要求(每次都照做這些檢查,只有內容比對後才跳過解析/驗證)不會讓任何一道被跳過或調換順序。
- `MAX_CONFIG_BYTES = 64 * 1024`(第 26 行)= 64 KB,跟設計第 30 行數字一致。
- 呼叫點核對:簽發器每次簽發都經 `_tenant_of` → `load_tenants`(`capability_signer.py:166-167`);「處理待核可每一輪讀一次」對應 `execution.py:622-635` 的 `process_awaiting`(且只在有待處理項目時才讀,程式註解本身就這樣寫);核可管理工具(`approve.py`)、指標命令列(`ops/metrics.py`)各自的 `run()` 都只呼叫一次 `load_tenants`。三處的「讀一次/各讀一次」說法均成立。
- 「已驗證那一段從已驗證列的時間部分索引出發、依主鍵接回第一列」:對應 `attempt_store.aggregate_holdings`(第 621-627 行)目前就是這樣寫的查詢(用 `attempts_verified_by_time` 索引再 JOIN 回 `seq=1` 那列),`VERIFIED` 是終點狀態且 `can_transition` 表裡它之後轉不到任何狀態、又有 `attempts_one_terminal_per_key` 唯一索引擋同一把鍵兩列終點列,所以不會出現設計特別要求核對的「已驗證列在窗內但同一把鍵又有別的狀態」這種歧義情況。
