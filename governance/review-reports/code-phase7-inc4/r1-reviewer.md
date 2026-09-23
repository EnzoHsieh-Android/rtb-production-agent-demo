severity: clean

## 已看:端到端是否真的走到每一層正式程式,有沒有被替身化

逐一核對 `tests/analyzer/test_f5_end_to_end.py`(diff 全文,新增 164 行)裡 `run_once`/`_walk`/`_analyze`/`_execute` 用到的每一個協作者,對照它們的原始碼:

- 模擬 DSP:`rtb.dsp.server.DspServer` + `rtb.dsp.store.CampaignStore`,行程內執行緒 `dsp.serve_forever`,`fault_injection=False`、帶 `capability_key=TEST_KEY`——跟 `src/rtb/dsp/server.py` 的憑證驗證、寫入交易、版本遞增全部真的跑一次(`_authorized_write` → `verified_claims` → `check_scope` → `store.execute`)。
- 分析行程:`rtb.analyzer.instrumented.dsp_evidence_source(analyzer, dsp_url, 3)` 內部呼叫 `rtb.analyzer.dsp_client.make_client`(真的 HTTP GET `/campaigns/<id>` 與 `/campaigns/<id>/metrics`,逐欄白名單、可信/不可信分流,見 `src/rtb/analyzer/dsp_client.py:138-171`),`inbox_client.make_client` 真的 POST `/proposals`(`src/rtb/analyzer/inbox_client.py:42-50`)。`instrumented.py` 只加一層記錄,不改行為(讀過該檔頭註解與 `InstrumentedSubmit`/`dsp_evidence_source` 的實作,`flow.py` 完全不知道 tool_calls 這件事)。
- 收件口伺服器:`rtb.executor.inbox_server.InboxServer`,行程內執行緒 `inbox.serve_forever`;`_analyze` 送出的提案真的打到 `InboxHandler.handle_request` → `parse_proposal` → `store.accept`。
- 執行迴圈:`_execute` 用真的 `rtb.executor.dsp_client.DspClient(dsp_url, 3.0)`、真的 `rtb.executor.capability_signer.CapabilitySigner(TEST_KEY)`、真的 `tests.executor.fakes.write_config` 產生的租戶設定檔(不是假物件,只是產生 JSON 檔案這個工具函式),呼叫 `Executor(...).process_one()`——追蹤 `execution.py` 的 `_process` → `_sign`(真簽發)→ `_take`(開嘗試)→ `self.dsp.write(...)`(真 HTTP 寫入)→ `_record` → `_verify`(真的重讀 DSP 核對),整段在同一次 `process_one()` 呼叫內完成,沒有繞過任何一層。

沒有發現任何一層被替身/跳過;`tests/executor/fakes.py` 裡唯一被引用的只有 `write_config`(純寫檔工具),不是 `FakeDsp`。

## 已看:S213 斷言是否對應計劃〈端到端〉每一條、對照組是否真的產出改預算

計劃「### 端到端」三條斷言逐一核對 diff 第 130-147 行:

- 「DSP 操作歷史恰好一筆改預算、新預算 110、跟對照組相同、沒有暫停、別的廣告沒被動到」→ `normal_result["writes"] == [("update_budget", 2)]`、`c1.budget == 110`、`result["writes"] == normal_result["writes"]`、`result["c1"].status == "active"`(排除暫停)、`result["other_writes"] == [] and result["c2"].budget == 300`。
- 「分析行程對外呼叫只有讀現況、讀指標、送提案三種端點」→ `result["endpoints"] == EXPECTED_ENDPOINTS`。追過 `dsp_client.py` 只有 `dsp:campaign`/`dsp:metrics` 兩個 `on_call` 端點名稱,`InstrumentedSubmit` 記 `inbox:submit`,三者互斥、沒有第四種呼叫路徑。
- 「提案裡沒有工具名稱、網址、憑證欄位」→ `primitives` 子集於 `CHECKS`(白名單)、字串裡不含 `attacker.example`/`RTB_CAPABILITY_KEY`/`name` 本身。

對照組不是空綠:`assert normal_result["c1"].budget == 110` 且 `normal_result["writes"] == [("update_budget", 2)]` 在每個參數化案例都重新斷言,親自把 `policy.py` 的 `new_budget = min(max(round(budget*1.1)), ...)` 改成 `+name_len`(讓廣告名稱長度混進金額,對應計劃列的第一種變異)後在乾淨臨時目錄跑過:10 個 S213 案例全部翻紅(`AssertionError: assert 122 == 110`),對照組 assertion 先抓到,證明它不是「兩邊都沒做也會綠」的空判準。

## 已看:S214、S215 是否會假綠

S214、S215 的斷言用「白名單成員資格」而非「不等於 FAILED」:`assert result["state"] in (TaskState.HANDED_OFF, TaskState.NO_ACTION)`。追過 `_walk`/`_analyze` 的迴圈(`for _ in range(8)`,只在 `HANDED_OFF`/`NO_ACTION`/`FAILED` 才 `break`):任務若卡在 `COLLECTING_EVIDENCE`(例如蒐證持續丟例外,`flow._from_collecting_evidence` 吞例外留在原狀態不寫入)不會被誤判成通過,因為 `COLLECTING_EVIDENCE` 根本不在允許集合裡,斷言會如實翻紅,不是「不轉失敗」就直接判定通過。

親自把 `rtb.analyzer.dsp_client._campaign_text` 改成名稱超過 1000 字元就 `raise DspRequestFailed`(對應計劃列的第五種變異:讓超長名稱蒐證失敗)在乾淨臨時目錄跑過:3 份 oversized 素材(zh/en/emoji)× 2 種配速共 6 個 S215 案例全部翻紅,錯誤訊息 `assert <TaskState.COLLECTING_EVIDENCE> in (<HANDED_OFF>, <NO_ACTION>)`,證明卡在蒐證確實會被抓到,不是假綠。

## 已看:時間相關的偶發失敗風險

DSP 憑證驗證的時鐘容許誤差 `MAX_SKEW_SECONDS = 30`(`src/rtb/dsp/capability.py:39`)、提案有效期 30 分鐘(`policy.py` 的 `DECISION_LIFETIME = timedelta(minutes=30)`)。`Executor` 收到的 `clock` 參數是 `_now` 函式本身(不是呼叫結果),每次簽發、每次到期判斷都重讀真實時鐘(`execution.py` 的 `_sign`/`_process`),不會固定用測試開始時的舊時間戳。實測在乾淨臨時目錄跑滿 26 個案例(含啟兩個真 HTTP 伺服器、真簽發、真驗證)總耗時 1.15–1.64 秒,離 30 秒容許誤差與 30 分鐘有效期有數量級的餘裕,找不到具體會在合理慢速 CI 上翻紅的路徑,不標成 finding。

## 已看:變異測試(在臨時目錄,repo 本身未被修改)

在 `mktemp -d` 建的臨時副本(`git -C` 全程對臨時目錄操作,repo 原始檔案未動)跑了兩種變異,兩種都如預期翻紅後已用 `git checkout --` 還原、刪除臨時目錄:

1. 讓決策讀名稱影響金額(`policy.py` 加 `name_len` 混進 `new_budget`)→ 10 個 S213 案例全紅。
2. 讓超長名稱使蒐證失敗(`dsp_client.py` 的 `_campaign_text` 超過 1000 字元丟例外)→ 6 個 S215 案例 + 3 個 S213 oversized 案例全紅(共 12 個)。

變異前基準:`pytest tests/analyzer/test_f5_end_to_end.py` 26 個案例全綠,對應計劃「十份對抗性素材、S213–S215」的案例數(10 + 10 + 3×2)。

## 已看:圖譜機械反查(三格皆空)

指派時給的受影響測試/共改夥伴/呼叫者三格皆空,核對後合理:這支測試檔是全新檔案,repo 裡沒有其他程式或測試檔引用它,也沒有共同修改歷史可挖;不代表沒有相依,只代表這是一支獨立的端到端驗收測試,審查已直接讀過它呼叫到的每一層正式程式碼補上這段機械反查漏掉的相依關係。

⚠ 交編排者:計劃〈增量 4 設計〉步驟 4、5 提到本輪之後還要「派一個不知道脈絡的代理審 F5 是不是真合約」並在同一個提交完成轉正;本輪只審了「端到端測試夠不夠格被綁進合約」,F5 轉正本身不在這次 diff 範圍內,沒有東西要交編排者裁——上述只是提醒後續步驟還沒做,不是本次審查的缺口。

最高 severity:clean,blocking 0 條。
