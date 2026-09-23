severity: minor

# 稽核範圍與方法

比對範圍:`git -C /Users/enzo/rtb-3b diff c2aa8dcf92f12a4694f10e336592da3d60073e70..HEAD`,實際改動只有
`docs/rtb-production-agent-demo-knowledge/Systems/分析行程流程與檢查點.md`(新增/改寫多行 WHY/RULE/PITFALL/TEST)、
`docs/.governance-log.jsonl`、`src/rtb/analyzer/flow.py`、`src/rtb/analyzer/task_store.py`、
`tests/analyzer/test_task_lease.py`(新檔)以及 `governance/review-reports/rtb-phase4佇列與重新投遞-增量3b/` 下的審查卷證。

固定席清單裡列的 `Systems/執行迴圈.md`、`Systems/外部寫入嘗試紀錄.md`、`Systems/Mock-DSP.md`、
`Systems/任務流程領域模型.md`、`Systems/共用行程基礎.md`、`Systems/提案收件口.md` 六篇及其牽連檔
(`attempt_store.py`、`execution.py`、`runner.py` 等執行側檔案)**這次 diff 完全沒有改動**——只是
impact 工具因分析側租約設計「概念照執行側」而算出的間接相依,逐篇核對後確認無新增/矛盾內容需要判,略過逐條反駁。

逐句核對方式:讀完整份 diff 後,對每一行新增的 WHY/RULE/PITFALL 逐句比對 `flow.py`/`task_store.py`
實際邏輯;對摘要裡列出的 [test:...] 名稱,在 `/Users/enzo/rtb-3b` 用
`PYTHONPATH=/Users/enzo/rtb-production-agent-demo/.venv/bin/python`(README:應為 `.venv/bin/python`,
`PYTHONPATH=src`)實際跑過;對既有 ★INVARIANT★ 的 kill_recipe(`if row is None or row[0] != expected_seq:`
→ `if row is None:`)複製 `src`+`tests` 到臨時目錄套用後重跑,確認仍能讓綁定測試翻紅(現在會先撞
`IllegalTransition` 而不是原本的並行覆寫斷言,但一樣是紅,判定殺傷力仍在);另外自行加了一個未列在
kill_recipes 裡但对应新 RULE 的變異(拿掉 `commit_step` 裡 `_lease_allows` 的圍籬檢查),確認
`test_an_expired_holder_cannot_commit_after_a_takeover_even_when_the_sequence_still_matches`、
`test_a_commit_without_a_receipt_cannot_write_over_a_live_holder` 兩支綁定測試會紅——租約圍籬確實有牙齒。

## F1 flow.py 模組開頭 docstring 沒有跟著提租約機制
severity: minor
blocking: 否 — 沒有語意錯誤,只是遺漏,不影響行為
引句:「重複呼叫 `advance()` 在中斷後恢復是安全的。」
file: `/Users/enzo/rtb-3b/src/rtb/analyzer/flow.py:1-16`
這段模組頂端 docstring 在本次 diff 完全沒被改動(diff 從第 15 行之後才開始插入 `import`),仍只講
「當機重啟、檢查點恢復安全」,沒有提到 2026-09-23 起 `advance()` 呼叫外部介面前要先取得租約、
拿不到就整輪不呼叫這件事——而這正是本次改動要解的事故 F3(重複花分析費用)的核心機制。
`advance()` 函式本身的 docstring 已經補上租約說明(`flow.py:132-137`),`task_store.py` 的模組 docstring
也同步補了租約表的說明(`task_store.py:1-11`),唯獨 `flow.py` 的模組層級說明沒跟著更新,三個月後
只讀模組開頭會漏掉「這裡現在還管錢」這件事。⚠ 這是文件完整性問題,不是圖譜或測試造假,列出來
給下次補。

## F2 「commit_step 是所有寫入唯一的入口」這條既有 RULE 的字面範圍被新程式碼繞過,但受保護的不變量沒被破壞
severity: minor
blocking: 否 — 逐句核對後,RULE 想保護的東西(狀態轉換合法性)仍然只能經過 `commit_step`;只是「唯一入口」四個字現在字面上不精確
引句:「`commit_step` 是所有寫入唯一的入口,防線放在這裡才擋得住往後任何新呼叫端」
file: `/Users/enzo/rtb-3b/src/rtb/analyzer/task_store.py:288-336`
`acquire_lease`/`release_lease`/`_append_release` 這次新增,直接對 `task_leases` 表 `INSERT`,完全
不經過 `commit_step`。核對後確認這條 RULE 從 2026-09-22 建立時的語境本來就只針對 `tasks`/`evidence`
兩張表的狀態寫入(`create_task` 本身也一直是直接寫、不經 `commit_step`,不是本次新增的例外),
`task_leases` 是全新的、跟任務狀態機無關的表,不落在這條 RULE 想擋的「新呼叫端繞過 `can_transition()`」
範圍內——所以我判定這條 RULE **仍成立於它原本要保護的範圍**,不算矛盾。但這次補寫租約相關 RULE 時
沒有順手在這條舊 RULE 旁註記「範圍不含 `task_leases`」,三個月後容易被字面誤讀成「這模組所有寫入都
只能走 commit_step」,建議下次順手補一句範圍澄清。

# 既有 RULE / INVARIANT 逐條核對結果

- **時間只有一個來源**(`advance()` 的 `now` 同時餵 `EvidenceSource`/`Decide`,不自己讀時鐘):本次
  新增的 `acquire_lease`/`release_lease`/`_lease_allows`/`_is_live` 全部吃呼叫端傳進來的 `now`,沒有
  新增任何 `datetime.now()`/系統時鐘讀取。唯一讀系統時鐘的地方(`instrumented.py` 的 `record_tool_call`
  時間戳)本次未改動,且文件本來就明講那是「呼叫何時發生」的紀錄、跟新鮮度無關的既有例外。**仍成立**。
- **`commit_step` 是所有寫入唯一的入口**:見上面 F2,**仍成立於原本範圍**,字面精確度有落差。
- **`record_tool_call` 只吞資料庫層錯誤**:本次未改動這支函式(`task_store.py:335-349` 不在 diff 裡),
  新加的 `flow._release_keeping_the_original_error` 明確比照同一組例外(`sqlite3.Error`、
  `DatabaseBusy`),用 `test_a_failed_release_never_masks_the_original_error` 驗過:把 `release_lease`
  monkeypatch 成必丟 `DatabaseBusy`,`before_commit` 丟出的 `_PlannedCrash` 仍然原封不動往外傳。**仍成立**。
- **`InstrumentedSubmit` 呼叫端要在每次呼叫前用當下讀到的列建**:本次沒有改 `instrumented.py`,也還沒有
  任何排程/啟動程式把 `InstrumentedSubmit` 接到 `advance()`(文件已知缺口寫明「分析行程還沒有正式啟動
  程式」)。租約邏輯本身在 `_advance_holding` 內部會先重讀 `current.seq`、跟原本讀到的 `row.seq` 不同就
  整輪放棄不呼叫任何協作介面(`flow.py:164-169`),所以將來真的接上 `InstrumentedSubmit` 時,`row` 在
  被拿去呼叫協作介面之前已經保證是最新一列——跟這條 RULE 的精神一致,不衝突,但**這條 RULE 本身這次
  沒有被任何新測試實際運行到**,只是邏輯上相容,如實記錄。

# ★INVARIANT★ 綁定測試與 kill_recipe 驗證

- `分析行程流程與檢查點.md` 現有的 ★INVARIANT★(「每一步都要落地成新的歷史列」)綁定測試
  `test_two_concurrent_advance_calls_on_the_same_task_never_both_commit_conflicting_outcomes`:
  臨時目錄套用 kill_recipe(把 `if row is None or row[0] != expected_seq:` 換成 `if row is None:`)後
  重跑,測試從綠翻紅(`IllegalTransition: 不合法的轉換:collecting_evidence -> collecting_evidence`)。
  殺傷力仍在,只是失敗訊息從原本的「兩邊都寫成功」變成一個轉換合法性例外——本次改動在 `commit_step`
  裡插入的 `_lease_allows` 檢查排在序號檢查之前,不影響這條 kill_recipe 的老字串是否還逐字存在
  (`task_store.py:271` 一字不改,原封不動)。
- 額外對新的租約圍籬自己做了一次未收錄進 kill_recipes 的變異:拿掉 `commit_step` 裡
  `if not self._lease_allows(task_id, lease, now): return False` 整段,重跑
  `test_an_expired_holder_cannot_commit_after_a_takeover_even_when_the_sequence_still_matches`
  與 `test_a_commit_without_a_receipt_cannot_write_over_a_live_holder`,兩支都紅
  (`assert not True` / 過期持有者寫得進去),`test_a_late_release_from_a_replaced_holder_writes_nothing`
  仍綠(這支測的是 `release_lease` 自己的 `_holds` 圍籬,不吃 `commit_step` 的變異,合理)。
  租約圍籬確實有機械守衛咬著,不是裝飾。
- `tests/analyzer/` 整包(115 個相關測試,扣掉一個因為只複製 `src`/`tests`、沒帶 `pyproject.toml`
  才環境性失敗的 ruff 邊界測試)在臨時目錄全綠,含新增的 `tests/analyzer/test_task_lease.py` 11 支。

# 模組 docstring 與計劃筆記狀態核對

- `task_store.py` 模組 docstring:已同步補上租約表說明,跟實作一致。
- `flow.py` 模組 docstring:見 F1,未同步,屬遺漏不屬矛盾。
- `Projects/RTB_Phase4佇列與重新投遞_計劃.md` 的「## 增量 3b 設計」節狀態行寫「**狀態(2026-09-23):
  實作完成,待代碼審**」(第 360 行),跟目前這次稽核本身就是這個「待代碼審」缺口的一部分一致,沒有矛盾;
  F3 事故本身仍標 `★INVARIANT-PLANNED★`(尚未轉正),跟計劃第 350 行「F3 轉正等 3a 合併後做」一致——
  沒有出現「還沒轉正卻已經在摘要裡當 ★INVARIANT★ 使用」這種常見的圖譜超前於程式碼的問題。
- 計劃第 418 行明講「不能說『同時進行的重複一定只花一次』,只能說『同一時間只有一個有效持有者』」,
  跟 `分析行程流程與檢查點.md` 摘要裡對應的 PITFALL(「不要寫成『不會重複分析費用』」)以及
  `test_an_expired_holder_cannot_commit_after_a_takeover_...` 斷言「照實:接手的情況錢花了兩次」三方
  一致,measurement 上也用 S150/S152 兩支真多執行緒/真接手測試撐住,沒有誇大合約範圍。

# 總結

severity: minor
(最高);blocking 條數:0。新增與改寫的 WHY/RULE/PITFALL 逐句核對實作皆語意相符,
既有 RULE(時間單一來源、`record_tool_call` 只吞資料庫層錯誤)未被破壞,`commit_step` 唯一入口的說法
在原本保護範圍內仍成立但字面精確度有落差(F2),既有 ★INVARIANT★ 的 kill_recipe 在改動後仍逐字存在
且仍有殺傷力,新租約圍籬自行補測也證實有機械守衛。唯一具體落差是 `flow.py` 模組層級 docstring
沒有跟著補上租約機制說明(F1),屬遺漏不屬矛盾,不擋合併。
