severity: minor

## 第 2 輪 s1–s3 驗收

三條都修對了。重跑方式照第 2 輪原報告,在複本 `/tmp/p11b-r3-sec/repo`(HEAD 6306062)跑。

- **s1(SIGHUP 造成孤兒行程):修好。**
  - 重跑 `xp/drive.py`,假 claude 內跑 `sleep 20`,2 秒後送訊號。
  - SIGHUP、SIGTERM、SIGQUIT 三種都變成 `CallTerminated 呼叫途中收到訊號 N`,exit=1。
  - `ps` 看不到 `fakeclaude` 或 `sleep 20` 殘留,marker 只有 `started`,沒有 `finished`。
  - 佔位的訊息已經分開寫。`xp/claimtest.py` 印出:`錄製檔名被別的批次(20260924-deadbeef)佔住、還沒錄完:可能是中斷留下的佔位,確認沒有行程還在跑之後刪掉 … 再重跑`。送出次數 0。
- **s2(結果寫 ok、文字是 null 的錄製檔):修好。**
  - 重跑 `xp/tamper.py`,得到 `raised: rtb.modelcore NoRecording 錄製檔欄位型別不對 | is ModelCallFailed: True`,不再漏出原生 KeyError。
  - `_well_typed` 已經加上兩條驗證:延遲要有限而且不為負;是不是 ok 要跟有沒有文字對得上。
- **s3(批次紀錄不驗):修好。**
  - 重跑 `xp/batch.py`,得到 `BatchInvalid 批次紀錄讀不懂:KeyError`。
  - `record._replayed_batch` 接住 `BatchInvalid`,改成掛旗標。
  - 重播時也會逐列核對結果類別與原價,跟錄製檔對不上就掛旗標。
- **附帶(第 1 輪 #2 迴歸)**:把 HOME 改成 `/tmp/elsewhere` 再重載,`ledger_path()` 與 `verification_path()` 仍是 `/Users/enzo/.rtb/...`。

## A. 用 nohup 或忽略 SIGHUP 啟動的即時評估,斷線時反而被中止:轉例外的處理器蓋掉了使用者的「忽略」設定
severity: minor
blocking: 否
引句:「previous = {number: signal.signal(number, _raise) for number in CONVERTED_SIGNALS}」
file: `src/rtb/modelclaude.py:297`

**問題在哪**
- `_stop_signals_as_exception` 不看原本的處置是什麼,一律把 SIGHUP、SIGTERM、SIGQUIT 裝成丟例外。
- 原本是 `SIG_IGN` 也照蓋。最典型的就是 `nohup python -m rtb.eval.record --live … &`,使用者正是要撐過 ssh 斷線。

**後果**
- 第 2 輪之前,這種跑法斷線後會照常跑完。現在斷線當下這一次呼叫被殺,`CallTerminated` 往外丟,整批標成中斷。
- 那筆預留沒結算,要等人核銷。
- 清理是對的:沒有孤兒行程,也沒有超出上限,所以列 minor。

**重現**
- 在 `xp/` 跑 `nohup python drive.py NOHUP &`,2 秒後送 `kill -HUP`。
- 結果:exit=1,輸出 `python got CallTerminated 呼叫途中收到訊號 1`,marker 只有 `started`。
- 另寫 `xp/ign.py`:先把 SIGHUP 設成 `SIG_IGN` 再 exec 同一支腳本,結果相同。

**建議**
- 某個訊號原本的處置是 `SIG_IGN` 時,就不要裝處理器,保留忽略。
- 補一支測試:在 SIG_IGN 下送 SIGHUP,斷言呼叫正常完成。

## B. RTB_TEST_ACCOUNT_HOME 在正式執行也能生效:同時設 PYTEST_CURRENT_TEST,帳本與啟用紀錄就整本搬走
severity: minor
blocking: 否
引句:「if override and os.environ.get("PYTEST_CURRENT_TEST"):」
file: `src/rtb/modelledger_view.py:35`

**問題在哪**
- 能不能用覆寫,只看兩個環境變數,而兩個都是呼叫端可以隨便設的。
- 設了之後,`live_ledger_path()`、`verification_path()` 與核銷命令列會一起改指到別的目錄。
- 後果:每月 20 美元的累計歸零;啟用紀錄也能換成另一份。
- 第 1 輪把帳改成讀帳號資料庫,理由是「HOME 誰都改得動」。這裡又開了同強度的口子,只是門檻從一個變數變成兩個。

**為什麼列 minor**
- 意外觸發要兩個特定變數同時存在,機率很低。
- 刻意繞過的是本人,本人本來就刪得掉帳檔,不在威脅模型內。

**附帶的反方向小洞**
- 在 pytest 裡、但覆寫還沒設的時候,`account_home()` 會回真的家目錄。
- 這包括:收集階段、session 或 module 範圍的夾具、autouse 夾具之前。
- `pytest_sessionfinish` 的兜底只能在事後發現,真帳已經寫進去了。

**重現**
- `PYTEST_CURRENT_TEST=x RTB_TEST_ACCOUNT_HOME=/tmp/p11b-r3-sec/fakehome python -c "…print(mc.live_ledger_path(), cc.verification_path())"`
- 輸出 `/tmp/p11b-r3-sec/fakehome/.rtb/model-ledger.sqlite /tmp/p11b-r3-sec/fakehome/.rtb/live-verification.json`。

**建議**
- 即時入口(`settings_from_env`、`modelverify.run`、核銷命令列)偵測到覆寫變數就拒絕即時,改走錄製,等於 fail-closed。
- 或者把覆寫改成只能在行程內注入,例如模組層的測試鉤子,子行程另外傳。
- 反方向:在 pytest 裡覆寫還沒設時,`account_home()` 直接丟錯,不要回真家目錄。

## 查過、沒有列為發現的部分

- **訊號遮罩被子行程繼承**
  - 實測 claude 子行程起來時,SIGHUP、SIGINT、SIGQUIT、SIGTERM(1、2、3、15)都在遮罩裡。假 claude 裡讀出 `[1, 2, 3, 15]`。
  - 實作者自述「子行程繼承遮罩」屬實。
  - 後果有上限:逾時與中斷一律用 SIGKILL 殺整組,不受遮罩影響。
  - 只有兩種情況會受影響,兩者最後都收斂成 120 秒逾時再 SIGKILL:一是 claude 自己用 SIGTERM 收孫行程;二是有行程另起工作階段脫離這一組。這一版 `--tools ""` 沒有孫行程。
  - 不列 blocking。將來開工具,要在子行程裡解除遮罩(例如改用 posix_spawn 的 setsigmask)。
- **O_NOFOLLOW 佔位**
  - `O_CREAT|O_EXCL` 本來就不跟隨符號連結,懸空連結也會被擋(重跑結果是 `RecordingConflict`,送出 0 次)。
  - `save_recording` 用 `os.replace`,不跟隨目標的符號連結。
  - `release` 只刪佔位。
  - 沒有新洞。
- **ctypes 讀開機識別**
  - 實測 `boot_identity()` 回 `id:753732E2-…`,跟 `sysctl kern.bootsessionuuid` 一致。
  - 緩衝 64 位元組夠放 36 字元的 UUID;不夠時 sysctl 回非 0,結果是讀不到,核銷一律拒絕。
  - 值由核心給,使用者改不動;撥牆鐘不會被當成重開機。
  - 核銷先查主行程在不在,再比開機識別,順序正確。
- **resolve 後的 claude 路徑能不能被換掉**
  - 本機 `~/.local/bin/claude` 解開後指到 `~/.local/share/claude/versions/2.1.281`。這是原生安裝,每個版本一支檔,升版不會就地改寫。
  - 已實測過的那支被刪掉,就是起不來的設定錯誤。
  - 能改寫那支檔的只有本人,不在威脅模型內。
  - 殘餘風險:若用 npm 安裝,解開後是 `cli.js`,升版會就地改寫,整批途中可能換成沒實測過的版本。版本只在啟動時查一次,屬低機率,不列。
- **modelverify 的對照組**
  - 拿掉安全模式的對照組只在空暫存 HOME 隔離下跑(真 HOME 一律回 False),碰不到使用者真的設定與記憶。
  - 實測的每次呼叫都經過預留與結算,算進上限。
- **只在主執行緒裝處理器**:`call_model` 只在單執行緒路徑被呼叫(`model_candidate`),沒有找到會繞過的執行緒。
- **附帶(不屬本鏡頭)**
  - 全套 1675 支通過,但 `test_the_login_check_counts_against_the_call_deadline` 在本機連續 4 次都失敗:跑出 ConfigError `login_check_timeout`,不是測試預期的 ModelTimeout。
  - 原因:macOS 第一次執行新寫出的腳本要多花 0.7–1.4 秒,實測頭一次 2.72 秒與 3.37 秒、第二次 2.02 秒。登入檢查 2 秒加上這段時間,超過了 3 秒期限。
  - 這是測試太吃時間精度,產品碼的行為合理,請測試席確認。

**~/.rtb 檢查**:實驗前、全套測試後、全部實驗結束後,`ls -la ~/.rtb` 都是同一個空目錄(`drwxr-xr-x 2 enzo staff 64 Sep 24 17:29`),沒有寫進任何檔。沒有呼叫真的 claude 模型,只跑了 `claude --version`。

**看過的檔**
- r3-snapshot.patch 改動的產品碼:`src/rtb/modelclaude.py`、`modelclient.py`、`modelcore.py`、`modelledger.py`、`modelledger_view.py`、`modelledger_writeoff.py`、`modelrecording.py`、`modelverify.py`、`eval/model_candidate.py`、`eval/record.py`
- 測試:`tests/conftest.py`、`tests/model/fakes.py`、`tests/model/test_claude_process.py`、`tests/test_suite_isolation.py`、`tests/test_spawn_boundary.py`(只看測試清單)
- 其餘改動檔沒有逐檔細讀:各層 `ruff.toml`、docs、`recordings/model/README.md`、`ops/metrics.py`,以及其他測試檔。這些都在全套 pytest 1675 支通過的覆蓋範圍內,並用 r3-delta.patch 的差異清單核過範圍。
- 輪次紀錄:`governance/review-reports/code-phase11b-inc1/r2-資安-opus.md`、`r3-delta.patch`

2 條,blocking 0
