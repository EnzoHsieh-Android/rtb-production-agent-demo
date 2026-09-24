severity: major

## 第 1 輪 4 條驗收

- **#1 中斷後留下孤兒行程:部分修好。** 重跑第 1 輪的做法:用假 claude(`sleep 20`),2 秒後送訊號。
  - SIGINT:印出 KeyboardInterrupt,`ps` 看不到殘留,marker 沒有寫出 finished。修好了。
  - SIGTERM:變成 `CallTerminated` 往外丟,exit=1,整組已被殺。修好了。
  - SIGHUP:沒有修,見下面新發現 A。
- **#2 換 HOME 就換帳本:修好。** 在複本把 `HOME` 設成 `/tmp/elsewhere` 再重載,`ledger_path()` 與 `verification_path()` 還是 `/Users/enzo/.rtb/...`,因為它們改用帳號資料庫讀家目錄。
- **#3 錄製檔的旗標、stderr 與內容驗證:修好。**
  - 錄製檔現在明存 `tool_use` 與 `unclassified`,重播時還原。
  - stderr 只寫本機日誌,不進子原因,也不進錄製檔。
  - 載入時會驗鍵是否等於檔名、呼叫者、模型與各欄型別。
  - 還剩一個漏驗的組合,見新發現 B。
- **#4 查與寫之間的空窗、懸空符號連結:修好。** 在複本用假後端跑了兩種情況,結果都是 `RecordingConflict`,送出次數 0,呼叫前就擋下:
  - 懸空符號連結:`錄製檔名已被佔住但讀不懂…這次不呼叫`。
  - 別批留下的佔位:同樣被擋。
  - 佔位改用 `O_EXCL|O_NOFOLLOW` 建立。

## A. 關終端機或 ssh 斷線(SIGHUP)時,claude 行程群組照樣變成孤兒;錄製檔佔位也永久留下
severity: major
blocking: 是
引句:「previous = signal.signal(signal.SIGTERM, _raise)」
file: `src/rtb/modelclaude.py:282`、`src/rtb/modelclaude.py:264`

**問題在哪**
- `_sigterm_as_exception` 只把 SIGTERM 轉成例外。SIGHUP 與 SIGQUIT 還是 Python 的預設處置,行程直接結束,`finally` 與 `_abandon` 都不會跑。
- claude 是用 `start_new_session=True` 開的,自成一個工作階段,收不到終端機的掛斷訊號。
- 關掉終端機分頁、ssh 斷線,是跑即時評估時最常見的中斷方式,但計劃第 222 行只列了 Ctrl-C、例外與 SIGTERM。

**後果**(跟第 1 輪 #1 同一類)
- claude 變成父行程為 1 的孤兒,120 秒逾時的保證沒了。
- 那筆預留永遠不會結算;過了最長請求期限,人就可以把它核銷成較低的金額,但孤兒可能還在跑。
- 錄製模式下,`{"claimed_by_batch": …}` 佔位檔永久留在 `recordings/model/`:
  - 之後每一批遇到這個鍵,都收到「別的批次已經錄過同一個鍵;換一批要整批重錄、舊批整批刪掉」。
  - 這段話誤導人:其實只是當掉的佔位,不是已錄好的舊批。
  - 這個佔位檔也可能被一起提交進版本庫。

**重現**
- 複本 `/tmp/p11b-r2-sec/xp/drive.py` 用 `run_claude` 跑假 claude,2 秒後送 `kill -HUP`。
- Python 結束代碼 129。
- `ps` 看到 `29981 ppid=1 pgid=29981 /bin/sh …/fakeclaude HUP` 與 `sleep 20` 還活著。
- 約 18 秒後 marker 檔出現 `finished`,子行程完整跑完,沒被殺。
- 同樣的腳本送 SIGTERM 與 SIGINT,都清得乾淨。

**建議**
- SIGHUP 與 SIGQUIT 走同一個轉例外的處置,要轉的訊號寫成一組集合,`_abandon` 期間也一起擋住。
- `RecordingConflict` 的訊息分兩種寫:讀到的是佔位,就提示「可能是中斷留下的佔位,確認沒有行程在跑後刪掉」。
- 補一支測試:真子行程加上 `os.kill(os.getpid(), SIGHUP)`,斷言整組已經不在。

## B. 竄改過的錄製檔(結果寫 ok、文字是 null)讓 `call_model` 丟出原生 KeyError;延遲也不驗是否有限、是否非負
severity: minor
blocking: 否
引句:「failure = core.BY_OUTCOME[outcome](f"錄製的結果:{outcome.value}",」
file: `src/rtb/modelclient.py:218`、`src/rtb/modelrecording.py:102`

**問題在哪**
- `BY_OUTCOME` 裡沒有 `Outcome.OK` 這一項。
- 錄製檔寫 `"outcome":"ok","text":null` 時,這個組合通得過 `_well_typed`,也通得過鍵、呼叫者、模型的比對,接著就走進失敗分支查表失敗。
- `call_model` 只包 sqlite、OSError 與溢位,所以 KeyError 原樣漏出去,違反模組說明的「不讓原生例外漏出去」。
- 評估端有 `UNEXPECTED_ATTEMPT` 接住,會停下,所以不會錯判。
- `latency_ms` 只驗是不是數字:`NaN`、`-1e9` 都照收,之後帶進重播帳列與結果。

**重現**
- 複本 `xp/tamper.py`:寫一個鍵、呼叫者、模型都對,但結果 ok、文字 null 的錄製檔,然後重播。
- 輸出:`raised: builtins KeyError <Outcome.OK: 'ok'> | is ModelCallFailed: False`。

**建議**
- `load_recording` 加一條不變量:結果是 ok 就一定要有文字,不是 ok 就一定沒有文字。
- 延遲要求有限、而且大於等於 0。
- 違反的一律丟 `NoRecording`。

## C. 批次紀錄檔完全不驗,但重播時比較表的成本、延遲與各種比率全都從它算
severity: minor
blocking: 否
引句:「data = json.loads(Path(path).read_text(encoding="utf-8"))」
file: `src/rtb/eval/model_candidate.py:249`、`src/rtb/eval/model_candidate.py:349`

**問題在哪**
- 第 1 輪之後,錄製檔已經驗型別與鍵,但同樣存在版本庫裡的 `recordings/model/batches/*.json` 沒有任何驗證。
- 竄改的方式有兩種:
  - 格式錯:讓 `eval.record` 在重播時丟原生 ValueError 或 KeyError,直接崩。
  - 格式對、數字造假:改動逐格門檻的過或沒過。
- 跟錄製檔的交叉核對只看批次編號。
- 合成集一律不採用,所以不會改變採用結論,列 minor。

**重現**
- 複本 `xp/batch.py`:把 `rows` 裡的 `cell` 改成 `"nope"`。
- `load_batch` 丟出 `ValueError 'nope' is not a valid WorthCell`,沒有被接住。

**建議**
- `load_batch` 比照 `_well_typed` 驗型別與非負,不合就當「沒有批次紀錄」並掛上旗標。
- 或者重播時用逐列錄製檔的原價與延遲,跟批次紀錄比對,對不上就掛旗標。

## 查過、沒發現新問題的部分
- **佔位與符號連結**:
  - `claim` 用 `O_EXCL|O_NOFOLLOW`。
  - `load_recording` 先用 `lexists`,再拒絕符號連結與非一般檔。
  - `save_recording` 用隨機暫存名加 `os.replace`。
  - `write_batch` 用 `open("x")`,懸空的符號連結也會失敗。
  - 被拒、被中斷時 `release` 只刪佔位,已錄成的不動。
- **帳檔與啟用紀錄路徑**:兩者都用帳號資料庫的家目錄。即時模式拒收 `--ledger`,核銷命令列寫死同一本帳。
  - 管理政策、記憶檢查仍跟著 HOME 走,但這跟子行程實際拿到的 HOME 一致,沒有落差。
- **子行程環境與工具**:
  - 環境只帶 PATH、HOME、USER、LANG 加輸出上限變數。
  - 參數固定,不經 shell;使用者內容走 0700 暫存目錄裡的檔案。
  - stderr 不入庫。
  - `home_files` 的路徑只來自實測命令列的常數。
- **啟用紀錄能否被繞過**:
  - 任一項不是 True、隔離方式讀不懂、版本不同,都走錄製。
  - 真 HOME 隔離在 `no_memory` 與 `settings_suppressed` 一定回 False,不可能由工具寫出通過的紀錄,是 fail-closed。
  - 手寫紀錄屬於本人自己的操作,不列。
- **中斷清理的極窄競態**:例如第二次 Ctrl-C 剛好落在 `except BaseException` 與 `pthread_sigmask` 之間的幾個位元組碼,判斷為不實際,不列。
- **附帶(不屬本鏡頭)**:機器負載約 6 時,`test_claude_backend.py` 有 2 支計時測試跑出偶發的 ModelTimeout,另外兩次重跑都是 6 passed。

3 條,blocking 1
