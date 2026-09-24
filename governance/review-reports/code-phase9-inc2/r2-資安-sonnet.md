severity: clean
All relevant tests pass, consistent with the clean finding. Here is the final report.

---

severity: clean
# 資安審查報告 — code-phase9-inc2 增量 2 第 2 輪(r2-snapshot.patch)

severity: clean

以攻擊者視角複查第 1 輪修正帶進的四個新面(其餘範圍與判準同第 1 輪 `r1-資安-sonnet.md`,該輪已判 clean),沒有找到可被不可信輸入利用的洞:

- **政策版本標籤的值域仍有界**:標籤改成「版本字串本身」後,`_Resolver.policy` 是 `return value if value is not None and value in KNOWN_POLICY_VERSIONS else OTHER`(引句:「value if value is not None and value in KNOWN_POLICY_VERSIONS else OTHER」)。輸入端 `event.policy_version` 來自提案的 `policy_version` 欄位,是不可信輸入(僅受 `POLICY_PATTERN`——`[A-Za-z0-9._:-]{1,64}`——約束,值本身任意),但 `KNOWN_POLICY_VERSIONS` 是寫死在 `src/rtb/domain/proposal.py:29` 的模組層常數(`("demo-pacing-v1",)`),只能靠改程式碼增補,不吃任何設定檔或執行期輸入;比對是嚴格 `in` 一個固定 tuple,提案端塞任何不在清單內的字串都落到 `OTHER`。攻擊者無法讓標籤基數超出「清單長度 + 1(other)」,值域仍有界,沒有恢復成先前「端點是任意字串」那種無界標籤面。
- **時區入口檢查沒有留繞路**:新增的 `is_aware` 檢查在多層都補齊——CLI 層 `_aware_time`(引句:「時間必須帶時區(例:{text}Z 或 {text}+08:00)」)、函式層 `_require_aware`/`_check_window`、以及 `analyzer/task_store.py` 的 `_iso`(引句:「if not is_aware(moment): raise ValueError("時間必須帶時區")」)。核對了這條路徑上唯一可能是「外部/DSP 來源時間」的 `Evidence.observed_at`——它在 `src/rtb/domain/evidence.py:65` 的 `__post_init__` 早就用同一個 `is_aware` 守門(`("observed_at", is_aware(self.observed_at))`),沒帶時區的 `Evidence` 根本建構不出來,不會等到寫入 `task_store` 才炸;`_iso` 的新檢查是縱深防禦、不是新開的可觸發例外面,沒有讓提案/DSP 資料能靠塞一個 naive datetime 讓分析端寫入卡死或悄悄漏資料。
- **參數解析錯誤訊息不構成可利用面**:`_aware_time` 的錯誤訊息確實把命令列原文回顯進 stderr(引句:「看不懂的時間:{text}」),`_Parser.error` 也把 argparse 內部組出的訊息原樣印出。但材料範圍與程式庫內都沒有任何地方把外部輸入(提案內容、DSP 回應、LLM 產出文字)自動組裝成這支 `ops/metrics.py` CLI 的參數——`grep` 全庫只有測試檔匯入這個模組,沒有任何腳本或呼叫端把不可信資料轉成 argv 餵給它;`--since`/`--until`/`--now` 是操作者手動輸入,回顯給操作者自己看不構成注入面,訊息本身也只是純 f-string 組字串,沒有格式化字串漏洞或往下游二次解析。
- **缺索引檢查是拒絕開庫、不是讓指標停擺**:`missing_schema` 新增的索引檢查(引句:「missing += [f"索引 {name}" for name in indexes if name not in existing]」)缺一個就在唯讀開法建構子丟 `DatabaseNotUpgraded`,直接拒絕開庫(`TaskReader.__init__`/`ReadOnlyInbox.__init__` 兩處都在例外時關掉連線並往外拋),不是靜默退化成全表掃描、也不是無限迴圈或掛住——是乾淨失敗,行為可預期。索引本身由部署端遷移腳本建立、不是提案/DSP 這類外部輸入可寫入的 DDL 面,攻擊者沒有管道讓「缺索引」被觸發或反過來被用來讓服務假裝正常但實際掛住;測試 `governance/review-reports/code-phase9-inc2/r2-delta-tests.patch` 也驗證了唯讀開法不會替它補索引(`assert path.read_bytes() == before`),不存在唯讀連線意外寫入的面。

執行 `PYTHONPATH=src /Users/enzo/rtb-p9i1/.venv/bin/python -m pytest tests/ops/test_metrics.py tests/ops/test_window_readers.py` 28 個測試全過,行為與上述分析一致。

檔案位置(供交叉核對,未在材料 patch 內、屬查證用):
- `src/rtb/domain/proposal.py:29` `KNOWN_POLICY_VERSIONS: tuple[str, ...] = ("demo-pacing-v1",)`
- `src/rtb/domain/proposal.py:181` `"policy_version": lambda raw: _matches(POLICY_PATTERN, raw["policy_version"])`
- `src/rtb/domain/_checks.py:29-34` `is_aware()` 定義(`utcoffset() is not None`)
- `src/rtb/domain/evidence.py:65` `Evidence.__post_init__` 對 `observed_at` 的 `is_aware` 檢查(先於增量 2、非本輪新開)

以上四個本輪新面在材料範圍內都有對應且正確生效的邊界控制,沒有可被利用的洞。
