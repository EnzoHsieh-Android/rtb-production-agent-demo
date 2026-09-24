severity: major

F1 使用者層的 Claude Code 設定沒有隔開:hook 會跑、會往提示裡塞內容,欄位白名單和「沒工具就跑不了指令」兩個前提都不成立
severity: major
blocking: 是
引句:「所以就算模型被廣告名稱誘導,它手上也沒有讀檔、跑指令或連網的工具」
file: `/Users/enzo/.claude/settings.json:43`(SessionStart,三支 hook)、`/Users/enzo/.claude/settings.json:10`(Stop,一支 hook,時間預算 40 秒)、`/Users/enzo/.claude/settings.json:77`(enabledPlugins,裝了 6 個外掛)
- 設計的做法:把 HOME 帶給子行程(為了讓它登入),S906 只要求四件事:關工具、不載入 MCP 與外掛、空的工作目錄、不存對話紀錄。
- 問題:HOME 一帶進去,Claude Code 照常讀使用者層的 settings.json,連同裡面的 hook、settings env、effortLevel、外掛,也會讀 auto-memory。空暫存目錄只擋得住專案層設定;`--tools ""` 管不到 hook。hook 是 Claude Code 自己用使用者權限跑的命令,跟模型有沒有工具無關。
- 具體例:照 S906 字面組出 `claude -p --output-format json --model claude-sonnet-5 --system-prompt … --tools "" --strict-mcp-config --no-session-persistence --max-budget-usd …`,每跑一次:
  - SessionStart 會跑 ci-status、lumos-entry、memory-sweep 三支 Python。它們回傳的附加內容會一起送給模型,裡面有使用者的 CI 狀態和記憶筆記。
  - 結果一:這些是欄位白名單以外的東西,被送到外部。
  - 結果二:S921 說同一情境送出內容逐位元組相同,實際上模型讀到的輸入每次都不一樣。
  - Stop hook 回傳「擋下」時,模型會多跑一輪:花費多一份、num_turns 大於 1。
  - memory-sweep 可能改到使用者的記憶檔;假說命令列「唯一的寫入是花費帳」這句也就不成立。
  - hook 的時間預算加總超過一分鐘,這就是延遲。
  - 外掛(例如 superpowers)也可能自帶 SessionStart hook。
- 應該怎樣:子行程完全不載入使用者層的設定、hook、外掛、記憶和 CLAUDE.md。模型讀到的輸入,只能是我們送的系統提示加使用者內容。
- 建議:
  - S906 的必帶參數補上「不載入任何設定來源與 hook」。以 2.1.281 版的 `claude --help` 來看,候選做法是 `--setting-sources ""`(要實測它接不接受空值),或 `--safe-mode`(說明寫會關掉 CLAUDE.md、skills、已裝外掛、hook、MCP),再加 `--strict-mcp-config` 和 `--disable-slash-commands`。注意 `--bare` 不能用:它規定只收 ANTHROPIC_API_KEY,訂閱登入會失效。
  - 本機實測加一項:改用 `--output-format stream-json --include-hook-events` 跑一次,確認沒有任何 hook 事件。
  - 實務隱患的「參數會改名」那條,把 hook 和設定來源也列進去。
  - S906 不呼叫真模型的測法:測試產生一支假 claude 腳本,把收檔路徑直接寫死在腳本裡(不能靠環境變數傳,因為環境是白名單)。腳本記下 argv、工作目錄、工作目錄內容、stdin,然後印出固定 JSON。
  - 測試逐項斷言:參數都在;工作目錄啟動時是空的;使用者內容只從 stdin 進、不在 argv 裡;呼叫回來後暫存目錄已刪。「刪掉」要在成功、逾時(假腳本 sleep)、非 0 結束三條路徑各斷言一次。

F2 「預留的最壞花費」上界在 Claude Code 後端不成立,1 美元和 20 美元兩道上限可能被突破
severity: major
blocking: 是
引句:「token 數不會超過 UTF-8 位元組數」
file: `/Users/enzo/.claude/settings.json:102`(claude-sonnet-5 的 effortLevel 是 high)、`/Users/enzo/.claude/settings.json:94`(全域 effortLevel 是 xhigh)
- 設計的做法:預留金額按「輸入上限 + 輸出上限 token 數」計算,最後再傳一個單次花費上限當保險。
- 問題一:`claude --help` 沒有限制輸出 token 的參數。唯一的管道是環境變數 CLAUDE_CODE_MAX_OUTPUT_TOKENS,但它不在 S905 的四個白名單變數裡。所以「輸出上限 token 數」只影響預留怎麼算和錄製鍵,管不住實際產出。
- 問題二:使用者設定的 effort 會帶進來,思考 token 也算輸出。
- 問題三:F1 講的注入內容會讓輸入超過「位元組數 + 200」。
- 問題四:`--max-budget-usd` 是在每一輪之後才檢查;單輪呼叫中途不會被截斷,算不上「由它自己擋」。
- 具體例:假說呼叫預留輸出 1000 token。Sonnet 5 在 effort high 下思考加回答產出 8000 token,成功結算的原價就是預留的好幾倍。本次展示已用 0.9 美元時,預留 0.05 通過檢查,結算後已用變成 1.2 美元,超過使用者裁定的 1 美元。
- 另外:「剩下額度」是在加上本次預留之前還是之後算,沒寫。如果是之後,可能算出 0,直接傳 `--max-budget-usd 0`,行為不明。
- 應該怎樣:實際花費在機制上不能超過預留;真的超過,要被偵測、被標記。
- 建議:
  - 白名單加 CLAUDE_CODE_MAX_OUTPUT_TOKENS,值等於輸出上限;明確傳 `--effort`,並關掉或寫死思考預算,不吃使用者設定。S905 和 S906 同步更新。
  - 單次花費上限改傳「本次預留 ÷ 1.2」,不傳剩餘額度。
  - 結算時如果原價 × 1.2 超過預留,照實記帳,印錯誤給人看,並讓評估整批停下。這一條補一個合約。
  - 錄製鍵保留「輸出上限」沒問題,前提是它真的有傳到子行程。

F3 七類錯誤怎麼判定沒寫依據,結算 0 的封閉集合沒法機械判斷,S904 的測試會變成自我驗證
severity: major
blocking: 是
引句:「暫時性服務錯誤:Claude Code 回報的其他錯誤(過載、限流、網路)與非 0 結束代碼」
- 衝突:「沒登入、不認得的模型」屬於設定錯誤,要結算 0;但 Claude Code 這兩種情況通常以非 0 結束。照字面讀,「非 0 結束代碼」又被歸進暫時性錯誤、要照預留結算。兩條規則哪條優先,設計沒說。
- 缺漏一:「訂閱額度用完」和「限流」怎麼區分沒寫,可 Phase 1 只有前者會讓評估整批停下。
- 缺漏二:is_error 為真但結束代碼是 0 的情況,屬於哪一類沒寫。
- 回歸:第 4 版每一項都有判定依據(狀態碼),第 5 版拿掉了。
- 具體例:
  - 實作者用字串「model」去比對「不認得的模型」,結果把一次已經送出後才失敗的 API 錯誤判成設定錯誤,結算 0,帳就少記了。
  - 反過來,額度用完的訊息沒被認出來、落到暫時性錯誤:評估不會停,210 個情境逐一失敗,每個都照預留結算,把本地的 20 美元耗光。
- S904 本身可以測:用假 claude 各印一種輸出,或以非 0 結束、sleep 等逾時、讓腳本不可執行,就能分到各類。但判定用的字串是實作者自己挑的,測試只證明「自己跟自己一致」。
- 應該怎樣:每一類要寫明依據哪些欄位、依什麼順序判定。認不出來的一律歸暫時性錯誤、照預留結算。設定錯誤只收「確定還沒送出」的情況。
- 建議:
  - 判定順序寫清楚:先看子行程有沒有起來;再看輸出能不能解析成 JSON;再看 is_error 和 subtype;結束代碼放最後。
  - 「沒登入」改在送出前的本地檢查判斷,例如由模型用戶端先跑 `claude auth status`。這一步也算子行程,要納入 S917 的放行範圍。
  - 「額度用完」和「不認得的模型」的判定樣本,在本機錄製時取真實輸出存成測試夾具,並寫明比對規則。
  - 這樣 S904 的假輸出才能跟真實格式綁在一起。

F4 「工具使用的痕跡」沒定義,而它是「關工具參數失效」時唯一的後備防線
severity: major
blocking: 是
引句:「結果文字超過 64 KB,或出現任何工具使用的痕跡」
- 問題:`--output-format json` 只回一個結果物件,不含每一輪的內容區塊。工具有沒有被用過,不會出現在結果文字裡。
- 衝突:實務隱患那條說參數失效時,組指令的測試仍會綠,緩解就是靠這項偵測。偵測沒定義,這個緩解等於沒有。
- 具體例:新版 Claude Code 把 `--tools ""` 改了語意,工具實際還開著。模型被廣告名稱誘導呼叫 Bash,最後照樣回一段乾淨的文字。實作者如果去掃結果文字,會判成成功。
- 應該怎樣:用結構化欄位判斷有沒有發生過工具呼叫。
- 建議:
  - 寫死判定:num_turns 必須是 1,permission_denials 必須是空的;兩者任一不符就歸「回應讀不懂」。或者改用 stream-json,只要出現 tool_use 區塊就判失敗。
  - 補一條合約:假 claude 回 num_turns=2 時,要丟「回應讀不懂」、照預留結算。
  - 本機實測也用同一個判準。

F5 測試誤呼叫真 claude 沒有機械防線:開發機兩個條件已經滿足,HOME 夾具擋不住
severity: major
blocking: 是
引句:「實作者的自動測試一律用假的 claude 指令(一支印固定 JSON 的小腳本放在暫存 PATH 裡)」
- 變化:第 4 版要即時呼叫,需要 ANTHROPIC_API_KEY,開發機預設沒有。第 5 版的條件換成「找得到 claude」,這台機器 `which -a claude` 查得到 `/Users/enzo/.local/bin/claude`。
- 結果:凡是要測即時路徑的測試都得設 RTB_MODEL_LIVE=1 並帶展示編號,之後實際跑到哪一支 claude,只看 PATH 順序。
- 共用夾具只把 HOME 指到暫存。PATH 裡是絕對路徑,照樣找得到真 claude。macOS 上的登入還能走鑰匙圈(本機鑰匙圈有 Claude Code-credentials 這筆),不依賴 HOME。
- 具體例:S904 要測「找不到 claude → 結算 0」。實作者把假腳本從 PATH 拿掉,但原本的 PATH 沒動。真 claude 被找到,即時模式成立,就真的呼叫了模型:吃掉訂閱額度,測試結果也不穩定。這正是「這個階段要防的事故」裡的第四條。
- 應該怎樣:整套測試在機制上碰不到真 claude。
- 建議:
  - 加一個整套自動套用的夾具:刪掉 RTB_MODEL_LIVE 和 RTB_MODEL_RECORD;把 PATH 換成只含暫存目錄加 /usr/bin、/bin。
  - 模型用戶端改收「claude 執行檔的絕對路徑」參數,由入口解析後傳入;測試直接給假腳本的路徑。
  - 補一條合約(比照 S932):整套測試期間,真 claude 不在 PATH 上。
- S900 的測法:假 claude 被執行就寫一個標記檔。三個開關列出所有缺一的組合,逐一斷言標記檔不存在、來源標「錄製」。另外把 subprocess.Popen 換成呼叫就丟例外的版本,作第二重保險。

F6 核心原則說「已登入」,模式判定和 S900 只看「找得到」,兩處說法不一致
severity: minor
blocking: 否
引句:「而且找得到已登入的 claude 指令」
- 衝突:核心原則要求「已登入」;模式判定那段和 S900 只要求 RTB_MODEL_LIVE=1、帶展示編號、找得到 claude。
- 麻煩:「已登入」只能靠啟動子行程來檢查。在入口檢查,又跟 S917「只有模型用戶端啟動 claude」衝突。
- 具體例:沒登入時照核心原則應該改走錄製,照模式判定卻會走即時,拿到設定錯誤後評估整批停下。
- 建議:二選一寫死。建議刪掉「已登入」,把沒登入交給設定錯誤處理(跟 F3 的送出前檢查一起做)。

F7 分析端「照舊」禁子行程模組,但現況沒有這條禁令;新禁令要照閉包掃描的結構加
severity: minor
blocking: 否
引句:「分析端目錄本身照舊不准直接匯入網路模組與子行程模組」
file: `tests/analyzer/test_boundaries.py:99`、`src/rtb/analyzer/ruff.toml:4`、`src/rtb/domain/ruff.toml:17`
- 現況:子行程模組只有領域層禁了。分析端的 NETWORK_MODULES 沒有 subprocess,但有 asyncio;而且這份清單同時套在目錄掃描和整個匯入閉包上(`test_boundaries.py:295`)。
- 陷阱一:模型用戶端會經模型說明命令列進入分析端閉包。實作者如果把 subprocess 直接加進 NETWORK_MODULES,閉包掃描會失敗。
- 陷阱二:模型用戶端如果用 asyncio.create_subprocess_exec,現有的閉包測試會直接擋下來。
- 建議:
  - 把「照舊」改成「新增」。
  - 模型用戶端寫明只用 subprocess、不用 asyncio。
  - S917 的測法:對 src/rtb 全部檔做語法樹掃描,抓 import subprocess、multiprocessing、pty,以及 os.system、os.popen、os.exec*、os.spawn*、os.posix_spawn*、asyncio.create_subprocess_*。只有 rtb.modelclient 放行。分析端閉包的放行也只給它。
- S918 的測法:rtb 閉包只追 rtb 自己的模組,看不到標準庫。所以「評估套件不啟動子行程」得另外用同一個掃描器查 src/rtb/eval。「送出呼叫只准模型候選呼叫」照現有 `_check_request_uses` 的寫法做名稱掃描即可。

F8 改動段外還留著舊的金鑰或 API 說法
severity: minor
blocking: 否
引句:「或 Anthropic 停止這個 API 版本且沒有相容替代」
- RETIRE-IF 還綁著 API 版本。應該改成「Claude Code 非互動模式停用,或訂閱條款不再允許這種用法」。
- 評估整批停下那段和 S924 還寫「認證或設定錯誤」,測試名還是 bad_key;例外類別已經改名叫「設定錯誤」。
- S900 的測試名還是 never_touches_the_network,實際要守的是「不啟動子行程」。
- 核銷要「附 Claude Code 或主控台的用量」,但訂閱沒有主控台,被殺掉的呼叫也沒有 JSON 可附,等於核銷拿不到依據。要寫明訂閱後端下以什麼當依據。
- 假說命令列「唯一的寫入是經模型用戶端記花費帳」:claude 子行程本身會寫 ~/.claude 底下的檔(F1 修好後剩下設定與狀態檔),應照實寫。

F9 S905 的白名單在 macOS 上測法有陷阱,也可能漏掉登入需要的變數
severity: minor
blocking: 否
引句:「子行程環境用白名單,只帶 PATH、HOME、USER、LANG(HOME 讓它找得到使用者的登入憑證)」
- 實測:用 `env -i PATH HOME USER LANG` 啟動專案 .venv 的 python3,子行程看到的環境多了 LC_CTYPE 和 __CF_USER_TEXT_ENCODING;改用 /usr/bin/python3 還會多出 CPATH、SDKROOT 等。
- 具體例:用 Python 寫假 claude,然後斷言「環境只含四個變數」,會誤判失敗。
- 另外:使用者如果是用 CLAUDE_CODE_OAUTH_TOKEN(setup-token)登入,或需要 HTTPS_PROXY,白名單會把它們擋掉,結果是設定錯誤。
- 描述也不精確:在 macOS 上,登入憑證可能在鑰匙圈裡,不一定靠 HOME 找。
- 建議:
  - S905 在呼叫邊界斷言:傳給 Popen 的 env 字典的鍵集合,是四個變數的子集;DSP 讀取令牌這類假值不在裡面。
  - 假腳本那一側只斷言「沒有出現被擋的變數」,不斷言「恰好只有四個」。
  - 實務隱患補一句:用 OAuth 令牌變數或代理登入的情況不支援。

9 條,blocking 5
