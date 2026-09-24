severity: blocker

### F1 驗證器自己的合約測試若放進 tests/,會在既有 checks 工作裡把 F7 多跑一次,正好加重使用者已經裁定「維持現狀」的那個已知 CI 抖動

severity: blocker
blocking: 是(直接牴觸 spec 自陳的完成條件「驗證結果在本機與 CI 可重現」,且會讓既有 checks 工作的執行內容在使用者不知情下改變)
引句:「已有先例:tools/mypy_sarif.py 與 tests/tools/ 是專案內非產品碼工具的位置,ruff 與 mypy 都掃得到。」
file: `/Users/enzo/rtb-3b/pyproject.toml:20-22` `[tool.pytest.ini_options] testpaths = ["tests"]`——凡放進 `tests/` 底下的測試,`python -m pytest -q`(checks 工作用的指令,見 `.github/workflows/ci.yml:22`)都會收集到,tests/tools/test_mypy_sarif.py 就是先例,已經在既有 1815 支測試裡跑著。
file: `/Users/enzo/rtb-3b/docs/rtb-production-agent-demo-knowledge/Issues/F7端到端在CI上偶爾超過60秒.md:12-13,79-82`——使用者 2026-09-24 已裁定「維持 60 秒與 [S340],CI 因這支超時紅燈就重跑,不改動」,回頭條件是「同一週因這支超時重跑超過兩次」才重新評估。
S809([test:test_the_committed_claims_pass],spec 第 93 行)要對五份正式清單「自己跑」證據測試,其中 aggregate-blast-radius 的證據含 F7(spec 第 69/70 行、`tests/executor/test_f7_end_to_end.py`,實測本機單獨跑 30.02 秒、內部斷言 60 秒硬上限);若這支合約測試依先例放進 `tests/tools/`,checks 工作的 `pytest -q` 會直接收集並執行它,而它的測試主體是子行程再跑一次 pytest 去執行同一支 F7——等於在同一個 checks 工作裡把 F7 從跑 1 次變跑 2 次,且第二次是巢狀子行程、資源狀態與第一次不同,提高撞上 Issue 裡那個「CI 機器慢的那一趟」的機率。spec「CI 與本機」一節(第 74 行)明講另開平行工作是為了「不拉長 checks 那個工作的時間」,但完全沒提到驗證器自己的 S8xx 測試該放哪裡才不會被 checks 工作的 `pytest -q` 反過來撿到——這正是本席被指派要查的「驗證器自己跑測試時被 pytest 全套再收集到」那個遞迴。

### F2 aggregate-blast-radius 宣稱把已知會在 CI 上偶爾超時的 F7 當成守門證據,spec 沒有處理這個已裁定但仍 open 的風險就直接讓它變成擋下閘

severity: blocker
blocking: 是(會讓新增的平行 CI 工作繼承一個已經記錄在案、使用者已明確選擇「不修、紅燈重跑」的抖動,卻把它從「重跑就過」升級成「安全宣稱擋下」)
引句:「未排除:驗證器每次跑證據測試約 45 秒到 1 分鐘(F7 那支占 30 秒),CI 多一個平行工作。」
file: `/Users/enzo/rtb-production-agent-demo/docs/rtb-production-agent-demo-knowledge/Issues/F7端到端在CI上偶爾超過60秒.md:20-25,58`——CI 上這支測試兩次紅燈到 67.4 秒、73.8 秒(本機同期只要 27–29 秒),推估 CI 平常餘裕只剩約 11 秒;原因是總曝險已用額度與簽發讀設定檔的成本隨規模近似平方成長,屬於 CI 機器負載與規模造成,不是程式錯誤。
file: `/tmp/rtb-phase11-r1.md:69`——`claims/aggregate-blast-radius.json` 的證據就是「F7 端到端與總曝險、核可測試」,而 S804(spec 第 88 行)規定「證據的任一測試節點...沒通過時,驗證器應擋下」,沒有為已知的效能邊界抖動留任何區分或重跑空間。
spec「實務隱患」一節只把這 30 秒列成執行時間成本(第 104 行),沒有引用或提及圖譜裡這篇同日期、使用者親自裁定過的 Issue,也沒有回應「F7 在 CI 上本來就會偶爾紅、要人工重跑」跟「驗證器擋下=安全宣稱不成立」這兩種語意被混在一起的問題;新平行工作跑 verify_claims 時若撞上機器慢的那一趟,擋下的訊息會說成宣稱失敗,而不是已知的效能邊界抖動,誤導看報告的人。

### F3 [S809] 合約文字有兩個子句,綁定的測試名只覆蓋其中一個

severity: minor
blocking: 否(不影響驗證器行為是否正確,只是這條合約有一半沒有機械測試守著,判準是漏掉的子句不會造成擋下失效或漏判,只是驗不到)
引句:「五份正式清單在目前版本上跑驗證器應全部通過;本機與 CI 用同一條指令。」
file: `/tmp/rtb-phase11-r1.md:93`——`[test:test_the_committed_claims_pass]` 這個測試名只對得上前半句「五份正式清單...應全部通過」,後半句「本機與 CI 用同一條指令」沒有對應的機械檢查(例如比對 README/CI yaml 裡的指令字串是否一致),只能靠人讀。
建議把後半句拆成獨立一條合約,或明寫這句是靠 README/CI 設定的人工核對,不是靠這支測試。

### 其餘章節逐節核對結果

- 「使用者裁定」一節:已讀,無 finding(裁定內容與設計後續段落一致,情境題「舊版本證據」對應到第 3 步新舊檢查)。
- 「現況」一節:已讀,無 finding;冪等宣稱列舉來源已從「DSP 儲存層」訂正為「DSP 伺服器模組(路由那一層)」,與實測 `src/rtb/dsp/server.py:110` 的 `CAMPAIGN_WRITE_ACTIONS` 位置一致(前掃已修過,不重報)。
- 「宣稱清單」欄位白名單一節:已讀,無 finding;白名單擋未知鍵、且驗證器自己做 sha256(不採信外部結果)的設計,對照 `xml.etree`/`ast`/`hashlib` 全是標準函式庫,符合零依賴家規。
- 步驟 1–5(格式、存在、新舊、列舉覆蓋、故障注入):已讀,無 finding;JUnit 對 skip/xfail 都記成 `<skipped type="pytest.skip"|"pytest.xfail">`、xfail_strict 讓 xpass 變 `<failure>`、找不到節點編號時 pytest 結束代碼 4 且 JUnit 完全不寫入(只能從 stderr 的 `ERROR: not found: ...` 文字抓是哪個編號)——這三點我用 pytest 9.1.1 實測跟 spec 逐字描述一致(見下方佐證)。
  file: `/tmp/p11_experiment/out.xml`——parametrize `test_param[1]` 展開、xfail 顯示 `<skipped type="pytest.xfail">`、xpass 顯示 `<failure message="[XPASS(strict)] x">` 的實測輸出,與 spec 第 57 行描述完全吻合。
  file: `/tmp/p11_experiment/out2.xml`——未知節點編號時 JUnit 整份是空 testsuite(0 個 testcase),結束代碼 4,與 spec 第 58 行「pytest 回結束代碼 4 而且整批一支都不跑」一致。
- 合約候選 S800–S808:已讀,無 finding;逐條可以寫成先紅後綠測試,測試名跟條款意思對得上(S804 名稱沒提到「被取消選取」這個子情境,但目前 repo 沒有任何 `conftest.py` 做 `pytest_collection_modifyitems` 之類的取消選取邏輯,指不出具體失敗場景,不標)。
- 「CI 與本機」一節:F1/F2 已報。
- 「拆增量」一節:已讀;冪等與總曝險兩條放進增量 1,代表 F1/F2 兩個 finding 從增量 1 就成立,不是增量 2 才會出現,已在上面 finding 裡指出。
- 「實務隱患」其餘三條(covers 自證、列舉侷限、雜湊重貼):已讀,無 finding,屬於語意天花板,不在本席範圍(spec 已自行承認並附 REVISIT)。
- 「回退」一節:已讀,無 finding;回退步驟只刪本增量新增檔案,不動既有測試,可重現。

最嚴重等級:blocker;blocking 條數:2(F1、F2)。
