severity: minor
# 審查結果

severity: minor

我以攻擊者視角針對凍結 patch(`/Users/enzo/rtb-production-agent-demo/governance/review-reports/code-phase3-attempts/r1-snapshot.patch`)逐項檢查:冪等鍵是否可撞、同廣告互斥/全表上限能否繞過、細節文字與處置理由過濾能否繞過、SQL 拼接有沒有注入點、快照讀回的信任邊界、以及是否能讓某把鍵或整個行程永久卡死。實際用 `/Users/enzo/rtb-production-agent-demo/.venv/bin/python`(`PYTHONPATH=src`)跑了原有測試(`tests/domain/test_attempt.py`、`tests/executor/test_attempt_store.py`,59 條全過)與我自己寫的攻擊腳本。結論:核心不變量(鍵唯一、互斥、上限、狀態機、SQL 皆參數化)在目前程式碼路徑下守得住,唯一挖到的洞是快照讀回缺一道自我一致性檢查。

### 1. `snapshot()` 讀回提案時沒驗證雜湊配不配得上呼叫端拿的那把鍵

severity: minor
blocking: 否 <目前程式碼裡沒有任何從「提案」或「DSP 回應/例外訊息」這兩個不可信輸入管道能寫到 `proposal_json` 欄位的路徑——該欄位只在 `begin()` 的 `INSERT` 寫一次,之後所有 `_append`(`transition`/`record_verification_timeout`/`resolve`)都不帶這個欄位,而且 `tests/executor/test_attempt_store.py` 的 `test_the_attempt_history_has_no_update_or_delete_statements` 用 AST 掃描鎖死了整支模組不准出現 `UPDATE`/`DELETE`。要利用這個洞得先取得能直接改執行行程 SQLite 檔的權限,超出題目定義的攻擊面(提案、DSP 回應/例外),所以不夠格擋線,但值得補強。>
引句:「parsed = parse_proposal(json.loads(record[0]))」

說明:`snapshot()`(`src/rtb/executor/attempt_store.py`)的職責是「讀回第 1 列存的完整提案,經同一個解析器還原成領域層的提案物件」,文件明講這是給「重新授權」用的依據(`docs/.../外部寫入嘗試紀錄.md`)。它確實把 JSON 丟回 `parse_proposal` 重新驗證格式,但**沒有再檢查 `operation_key(還原出來的提案) == 呼叫端傳入的 key`**——也就是說,只要 `proposal_json` 欄位的內容變了(不管是未來哪支程式碼、遷移腳本、還是直接改 DB 檔),`snapshot()` 會原樣把它當成「這把鍵原本就代表的提案」吐回去,完全偵測不出雜湊已經對不上。

實測(用凍結 patch 裡同一套 `begin()`/`snapshot()`):對一筆用預算 100 開出的鍵,直接改 `attempts` 表第 1 列的 `proposal_json` 為預算 999999 的另一份合法提案後:
```
snapshot budget: {'new_budget': 999999}
snapshot operation_key matches stored key? False
```
`snapshot()` 沒有丟出任何錯誤,安靜地把竄改過的內容當可信提案還原回去。由於這支函式的存在意義正是「快照讀回會不會把竄改過的資料當成可信提案」這一類風險的最後一道防線,建議補上 `if operation_key(parsed.proposal) != key: raise CorruptedAttemptRow(...)` 作縱深防禦,即使目前的寫入路徑已經用「只增不改」鎖死了攻擊面。

其餘檢查項目結論(均未發現可利用漏洞):
- **冪等鍵碰撞**:`operation_key()`(`src/rtb/domain/attempt.py`)只由 `task_id`/`campaign_id`/`action_type`/`requested_change`/`campaign_version_observed` 五個已經過白名單驗證的欄位做 `sort_keys=True` 的 JSON 序列化再 SHA-256,不同 `action_type` 或 `requested_change` 形狀不可能序列化成同一字串,同一邏輯操作重算永遠是同一把鍵(`tests/domain/test_attempt.py` 的 S1–S3 逐一驗證,實測通過)。
- **同廣告互斥 / 全表上限**:`begin()` 的「查目前列 → 查廣告未結案 → 查全表未結案 → 寫入」全部包在 `InboxStore.transaction()`(`BEGIN IMMEDIATE`,`src/rtb/sqlitekit.py:44-52`)之內,寫入鎖在交易一開始就拿到,check-then-act 沒有競態窗口;`test_concurrent_begins_for_the_same_key_create_exactly_one` 與 `..._different_keys_on_one_campaign_create_exactly_one` 兩條併發測試通過。
- **細節文字 / 處置理由過濾**:`is_clean_detail()` 用 `isascii()` 擋掉全形冒號、同形字、雙向覆寫字元等一切非 ASCII 字元,再用 `isprintable()` 擋掉換行、tab、ESC 等控制字元;逐一核對測試裡列的繞過樣本(`狀態:已驗證`、`\uff53...`、`\u202eevil`、`tab\there` 等)全部被拒。
- **SQL 拼接注入**:`attempt_store.py` 裡帶 `# noqa: S608` 的 f-string 只拼接模組內固定常數(`_COLUMNS`、`_LATEST_ONLY`)或由 `UNRESOLVED_STATES` 這個固定 enum 集合生成的 `?` 佔位符數量,所有實際值一律走參數化查詢;沒有找到注入點。
- **永久卡死**:`transition()`/`record_verification_timeout()` 的 `MAX_ROWS_PER_KEY` 上限只擋一般轉換,轉去 `ESCALATED` 與 `resolve()` 一律 `capped=False`,任何未結案狀態都保證有出口能轉人工、且轉人工後保證能被 `resolve()` 結案(`test_a_capped_history_still_accepts_escalation_and_resolution_but_nothing_else` 驗證)。全表 `MAX_UNRESOLVED=20` 滿了會擋新寫入,但這是文件明講的刻意「出事就停」設計,不是本次 diff 引入的繞過漏洞。
