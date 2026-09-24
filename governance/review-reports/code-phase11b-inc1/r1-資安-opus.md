severity: major

## 1. 呼叫途中被中斷(Ctrl-C/KeyboardInterrupt/SIGTERM)時,claude 行程群組沒人殺,變成沒有逾時的孤兒
severity: major
blocking: 是
引句:「returncode, timed_out = _finish(process, time.monotonic() + timeout_seconds)」
file: `src/rtb/modelclient.py:633`、`src/rtb/modelclient.py:637`

**問題在哪**
- `run_claude` 只在 `_finish` 正常跑完時才殺整組。
- 如果在 `_exited_unreaped` 的等待迴圈裡收到 KeyboardInterrupt,例外直接穿出 `_finish`。`finally` 只刪暫存目錄,不殺 `process.pid` 那一組。
- 子行程是 `start_new_session=True` 啟動的,自成一個工作階段,終端按 Ctrl-C 送不到它。
- 收到 SIGTERM 時,Python 用預設處置直接結束,連 `finally` 都不會跑。

**後果**
- claude 變成父行程為 1 的孤兒,逾時 120 秒的保證沒了。
- 它會一直跑、一直花額度,直到自己結束;網路卡住就一直掛著。
- 那筆預留永遠不會結算。等超過 `LONGEST_REQUEST`(7 分鐘),人就能核銷成較低的金額,但孤兒可能還在花錢。
- 這違反 [S939]「四條路徑都照 `_finish` 清理」的本意。
- 既有測試只用 `FakeBackend(KeyboardInterrupt())` 在後端層丟例外,沒有經過真的子行程。

**重現**
- 在 /tmp/p11b-sec/xp 放一支假 claude:寫標記檔、`sleep 20`、再寫 finished。
- 用 `run_claude(..., 60.0)` 啟動它,2 秒後對 Python 送 SIGINT。
- 結果:
  - Python 印出「python got KeyboardInterrupt and is exiting」並結束。
  - `ps` 看到 `36511 ppid=1 pgid=36511 /bin/sh .../fakeclaude` 與 `sleep 20` 還活著。
  - 19 秒後標記檔出現 `finished`,表示子行程完整跑完、沒被殺。

**建議**
- 把 Popen 之後的部分包進 `try/except BaseException`:先 `_kill_group(process.pid)` 再領回,然後把例外往外丟。
- 入口在呼叫期間裝 SIGTERM 處置,轉成例外,走同一條清理。
- 補一支測試:用真的假腳本子行程,中途丟 KeyboardInterrupt,斷言整組已經不在。

## 2. 帳檔與啟用紀錄都跟著環境變數 HOME 走:換個 HOME 就是一本新帳,上限歸零
severity: minor
blocking: 否
引句:「return Path.home() / LEDGER_RELATIVE」
file: `src/rtb/modelledger_view.py:26`、`src/rtb/modelclient.py:405`

**問題在哪**
- `Path.home()` 讀的是環境變數 HOME,不是帳號資料庫。
- 做法:`HOME=/某處`,再把 `~/.rtb/live-verification.json` 複製到 `/某處/.rtb/`,然後跑即時模式。
- 空暫存 HOME 隔離下,登入靠鑰匙圈,不需要真 HOME。所以即時模式照常開,而花費帳是一本空的新帳,每月 20 美元與每次展示 1 美元一起歸零。
- [S925] 說「入口沒有參數能改成別本」,但 HOME 實際上就是一個參數。
- 要故意操作才會中(換了 HOME 卻沒有啟用紀錄時,會 fail-closed 走錄製),所以列 minor。

**重現**
- 讀程式碼確認。conftest 本身就靠換 HOME 讓整套測試寫進暫存帳,正好證明這條路可行。

**建議**
- 即時模式的帳檔改用 `pwd.getpwuid(os.getuid()).pw_dir` 算。測試另外用明確的注入點替換,不要靠 HOME。
- 或者在即時啟動時,HOME 跟帳號資料庫的家目錄不同就拒絕即時。

## 3. 重播會丟掉「無法可靠分類」與「工具使用」旗標;錄製檔不驗內容,還把 stderr 寫進要入庫的檔案
severity: minor
blocking: 否
引句:「failure.unclassified = recording.sub_reason == "unclassified"」
file: `src/rtb/modelclient.py:1098`、`src/rtb/modelclient.py:683`、`src/rtb/modelclient.py:999`

**丟旗標**
- 即時的「無法可靠分類」有三種子原因:`unclassified`、`exit_code`、`stderr: …`(stderr 前幾行)。
- 重播時只有字面是 `unclassified` 的會還原,另外兩種在重播時不會停。
- `tool_use` 根本沒有寫進錄製檔。
- 結果是同一批在即時會停下的地方,重播照跑。

**stderr 入庫**
- `_parse_output` 把 claude 的 stderr 前 200 字當 `sub_reason`,存進錄製檔,而錄製檔會入庫。
- 計劃列的錄製內容只有:結果文字、延遲、token 數、結果類別、花費估計、後端種類、批次編號,沒有這一欄。
- stderr 裡可能有本機路徑或帳號資訊。

**不驗內容**
- `_load_recording` 不比對檔內的 `key`、`caller`、`model` 跟檔名與請求是否一致,各欄型別也不驗。
- 改過的錄製檔照樣被信任,可以改判定或讓評估停下。
- 合成集一律不採用,所以不會改變採用結論。

**重現**
- 讀程式碼,追三條 `unclassified=True` 的來源與重播還原的條件。

**建議**
- 錄製檔明確存 `unclassified` 與 `tool_use` 兩個布林。
- stderr 只留在本機日誌,不寫進錄製檔。
- 載入時驗 `key` 等於檔名,並驗 `caller`、`model`。

## 4. 錄製檔「查」與「寫」之間有空窗,懸空符號連結也會觸發:錢花了才丟「錄製檔已存在」,而且被歸成「確定沒呼叫模型」
severity: minor
blocking: 否
引句:「raise RecordingConflict("錄製檔已存在,不覆寫") from exists」
file: `src/rtb/modelclient.py:1124`、`src/rtb/modelclient.py:1144`

**會怎麼發生**
- 即時加錄製模式下,呼叫前用 `path.is_file()` 查。有兩種情況會讓它查不到、之後寫又失敗:
  - 兩個不同批次同時跑同一個鍵。
  - 錄製目錄裡放了一個懸空符號連結 `<key>.json`:`is_file()` 回 False,但 `os.link` 會撞上它。
- 這兩種情況都會送出、結算完,才在 `_save_recording` 丟 `RecordingConflict`。

**後果**
- `RecordingConflict` 是 `ConfigError`,結果類別是 config_error,語意是「確定沒呼叫模型」。
- 評估旁路紀錄因此把它記成沒送出(UNSENT),已付費的回應也丟了。
- 花費帳本身記得對,所以列 minor。
- 這跟 [S931]「別的批次已有就在送出前拒絕、不花錢」不符。

**重現**
- 讀程式碼:查的地方和寫的地方中間隔著 `_reserve`、`_send` 與 `_try_settle`。

**建議**
- 錄製失敗改丟獨立的類別,不要繼承 `ConfigError`,並保留已經拿到的回應。
- 或在呼叫前先用 `O_EXCL` 佔住檔名。
- 用 `os.path.lexists` 判斷「已存在」,把懸空符號連結也算進去。

## 查過、沒發現問題的部分
- **子行程環境**:只帶 PATH、HOME、USER、LANG 與輸出上限變數,令牌與金鑰進不去。
- **指令參數**:固定寫在程式裡,不經 shell。系統提示是常數,使用者內容只從標準輸入送。
- **不可信文字**:接入點 3 的輸入只有七個數字與狀態欄位。
- **工具與設定**:`--tools ""` 加上安全模式。依 `claude --help` 2.1.281,安全模式會停用 CLAUDE.md、hook、外掛、MCP,所以暫存目錄上層的 CLAUDE.md 注入不成立。
- **檔名與暫存目錄**:錄製檔名是 sha256 十六進位,不會路徑穿越;批次編號由程式產生;暫存目錄用 mkdtemp,權限 0700。
- **花費帳**:SQL 全部用參數佔位;預留在 BEGIN IMMEDIATE 交易裡,不同行程之間序列化;核銷拒收 NaN 與負數。
- **啟用紀錄**:任一項不是 True 就拒絕。真 HOME 隔離因為 `settings_suppressed` 一定回 False,實際上永遠不會通過,是 fail-closed。

4 條,blocking 1
