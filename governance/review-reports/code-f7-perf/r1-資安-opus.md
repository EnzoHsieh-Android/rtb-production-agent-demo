severity: major

**1. 候選裡只要有一筆沒記租戶的舊列,每次開始一筆的握鎖時間都比改動前還長(退回之前白掃一遍窗內已驗證列)**
severity: major
blocking: 是
引句:「total = _fast_part(conn, verified_sql, verified_params)」
引句:「if total is None: return aggregate_used_reference(tx, tenant, now)」
file: `src/rtb/executor/attempt_store.py:672`、`src/rtb/executor/attempt_store.py:681`

- **問題在哪**:快路徑先把 24 小時窗內的已驗證列整段加總、計數,再看未結案那一段。其中一段發現舊列,才整次改走原算法。所以退回時,已驗證那一段等於被掃了兩次:快路徑一次,原算法又一次。
- **哪種資料會一直觸發**:Phase 6 之前開的鍵,如果停在轉人工這類未結案狀態,沒有終點列,就一直留在未結案那一段。它不受 24 小時窗限制,可能留很久。這段期間,每一次開始一筆(在全域寫入鎖裡)都是「快路徑全掃 + 原算法全掃」。
- **比改動前差多少**:這種資料下比改動前的原算法還慢,不只是沒變快。判準寫明「握鎖時間變長」算 major 以上。計劃〈實作解讀〉與 S680–S683 都沒談退回路徑本身的成本。
- **數額**:退回路徑不會算錯。它回的就是原算法的值,多放或少放都不會發生。

重現:在 /tmp 複本(scratchpad/f7sec)寫了一支一次性量測,借用 patch 測試裡造列的輔助函式,在寫入交易裡各跑 5 次取最佳值。資料是窗內 2 萬筆 t-a 已驗證的乾淨整數列。

| 情形 | 新入口 `aggregate_used` | 原算法 `aggregate_used_reference` |
|---|---|---|
| 沒有舊列 | 0.0177 秒 | 0.0705 秒 |
| 加一筆沒記租戶、轉人工中的舊列 | 0.1078 秒 | 0.0780 秒 |

加了那一筆舊列之後,新入口比原算法慢約 38%。

建議:
- 先做便宜的偵測,再做加總。未結案那一段全表最多 20 把鍵,可以先查;也可以先用一支只查「候選裡有沒有舊列、有沒有型別異常」的輕查詢。偵測到就直接走原算法,不先跑整段 SUM。
- 補一條合約:退回路徑的查詢支數,或握鎖內的工作量,不能超過原算法。

**2. S19 綁定測試收窄本身夠緊,但它原本的漏洞還在;嘗試紀錄模組現在真的依賴共用資料庫模組,這些漏洞更容易被無意踩到**
severity: minor
blocking: 否
引句:「if node.module == "rtb.sqlitekit" and names <= SQLITEKIT_PURE_CHECKS:」
file: `tests/executor/test_attempt_store.py:624`、`tests/executor/test_attempt_store.py:626`

**這次放寬的部分:沒找到漏洞。** 豁免只認模組全名剛好是 `rtb.sqlitekit`、取的名字全部在 `{is_integer_overflow}` 裡:
- `from rtb.sqlitekit import is_integer_overflow, connect` 照樣被擋,實測紅燈。
- 星號匯入 `*` 不會被當成純判斷。
- 用別名(`as _x`)也不會被放行成別的函式。

**原本就有的漏洞(不是這次造成的):** 檢查只記 `node.module`,不看匯入的層級,也不看取出來的名字。以下寫法都能帶進開連線、開交易的函式,測試一律綠燈:
- `from rtb import sqlitekit`:記下的模組是 `rtb`,不在黑名單。
- `from .. import sqlitekit`:相對匯入,記下的是空字串。
- `from ..sqlitekit import begin_immediate as _b`:記下的是 `sqlitekit`,不是 `rtb.sqlitekit`。禁用名稱的檢查只看程式裡的變數名與屬性名,改名之後就查不到。
- `connect_read_only`、`begin_snapshot`、`read_snapshot` 不在禁用名稱清單裡。

這條要緊,是因為這次嘗試紀錄模組多了一條合法依賴到共用資料庫模組。之後有人順手改成 `from rtb import sqlitekit`,再呼叫 `sqlitekit.connect_read_only(...)`,S19 擋不住。

重現:在 /tmp 複本的 attempt_store.py 逐一加入下列匯入,每次只跑 S19 那支測試:

| 注入的匯入 | 結果 |
|---|---|
| `from rtb.sqlitekit import is_integer_overflow, connect` | 1 failed |
| `from rtb import sqlitekit` | 1 passed |
| `from .. import sqlitekit` | 1 passed |
| `from ..sqlitekit import begin_immediate as _b` | 1 passed |
| `from rtb.sqlitekit import is_integer_overflow as _x` | 1 passed(合理) |

建議:
- 相對匯入先換算成絕對模組名再比對。
- `from rtb import X` 要把 `rtb.X` 記進已匯入清單。
- 禁用名稱改成比對匯入的名字(alias.name),並補上 `connect_read_only`、`begin_snapshot`、`read_snapshot`。
- 或者更乾脆:斷言嘗試紀錄模組從 `rtb.sqlitekit` 取得的名字剛好只有 `is_integer_overflow`,其他寫法一律不准。

**以下攻擊面查過,沒有發現問題:**
- **少算已用額度、又不觸發退回**:
  - 另寫了一支差分隨機測試,3000 份資料,比新入口和原算法的回傳值或丟出的例外,結果 0 份不同,其中 2507 份走了快路徑。
  - 資料刻意放了各種邊角值:
    - 金額:0、負數、`-2**63`、整數上限、5.0、1.5、字串 "7" 與 "abc"、30 位數字字串、二進位、空值、1e300、NaN。
    - 租戶:大小寫不同、帶尾端空白、空字串、空值。
    - 時間:剛好 24 小時,以及再多 1 微秒。
  - 原因:快路徑只把「租戶相符、實際型別是整數、大於 0」的列加總,跟逐列計入函式那一條規則完全對應;其餘情形不是不在候選裡,就是退回原算法。
  - 型別偵測用的是 typeof,金額欄宣告為整數,文字 "12"、5.0 寫入時就會被轉成整數,兩種算法看到的一樣。
- **SQL 注入**:租戶與時間都走參數;拼進語句的只有列舉常數組成的終點狀態清單,和模組內固定的片段。沒有注入面。
- **溢位**:SUM 只加大於 0 的整數,不會出現中途溢位、最後又回到範圍內的情形。本機 SQLite 3.53.3 實測,溢位錯誤不會中止寫入交易(之後的寫入照常提交)。其他資料庫錯誤照舊往外丟。
- **租戶設定快取**:
  - 目錄檢查、不跟隨符號連結、一般檔案檢查、擁有者與權限檢查、大小上限,這些每次呼叫都在鎖內照做。
  - 只有位元組完全相同才回快取。解析函式只依賴位元組和固定常數,所以不存在「改檔後命中舊的較寬鬆設定」的時間窗。
  - 驗不過照樣拒絕、快取不動;改回原內容時命中舊解析結果,那份結果本來就對應這份位元組,是正確的。
  - 快取鍵用的絕對路徑不解析符號連結,只是識別用,不影響正確性。
  - 設定鎖是最內層的鎖,裡面不再拿別的鎖;讀的一定是一般檔案,不會卡在具名管道。沒看到死結。
  - 回傳的是凍結的資料類別加不可變集合,多個執行緒共用安全。
  - 處理待核可改走簽發器;程式裡實作簽發器協定的只有 `CapabilitySigner`,不會有漏掉新方法的實作。
- 新增的三支測試檔與 S19 那支測試,在複本上跑:11 passed。

**看過的檔(涵蓋 r1-snapshot.patch 的全部改動檔):**
- `/Users/enzo/rtb-f7-rev/governance/review-reports/code-f7-perf/r1-snapshot.patch`(全份;其中的 claims/*.json 五個檔、docs 知識圖譜七份筆記都在 patch 內看過)
- `/Users/enzo/rtb-f7-rev/src/rtb/executor/attempt_store.py`
- `/Users/enzo/rtb-f7-rev/src/rtb/executor/capability_signer.py`
- `/Users/enzo/rtb-f7-rev/src/rtb/executor/execution.py`
- `/Users/enzo/rtb-f7-rev/src/rtb/sqlitekit.py`
- `/Users/enzo/rtb-f7-rev/tests/executor/test_aggregate_fast_path.py`
- `/Users/enzo/rtb-f7-rev/tests/executor/test_approval.py`
- `/Users/enzo/rtb-f7-rev/tests/executor/test_attempt_store.py`
- `/Users/enzo/rtb-f7-rev/tests/executor/test_config_cache.py`
- `/Users/enzo/rtb-f7-rev/tests/executor/test_read_only.py`
- `/Users/enzo/rtb-f7-rev/docs/rtb-production-agent-demo-knowledge/Projects/F7效能_計劃.md`

實驗都在 /private/tmp/claude-501/-Users-enzo/36c33b75-fd17-4bff-aa49-20f97a550e58/scratchpad/f7sec 這份複本裡做,沒有改動被審的工作樹。

2 條,blocking 1。
