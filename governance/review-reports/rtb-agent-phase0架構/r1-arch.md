severity: minor

# 架構對齊席 第1輪報告

總評:元件切分與 handoff 第 5 節相符(沒有多餘的 agent,分析端與執行端因寫入憑證邊界而分開),PRIOR-ART、RETIRE-IF、決策四欄都在。下面是對不上專案做法的地方,都不擋設計。

## Finding 1 計劃檔名不符「Projects/<主題>_計劃」慣例
severity: minor
blocking: 否 判準:命名偏離但不改變設計內容,補改檔名即可。
問題:CLAUDE.md 鐵則 1 規定設計、spec、計劃一律寫成 `Projects/<主題>_計劃`,本篇檔名是 `RTB_Agent_Phase0架構`,沒有「_計劃」後綴。後果:依慣例找計劃節點的指令與人(`lumos handoff <計劃節點>`、`lumos spec-gate <計劃>`、設計審查迴圈)可能認不出這是計劃,之後別篇用 `[[Projects/RTB_Agent_Phase0架構_計劃]]` 連結會斷;Phase 1 起若沿用同樣命名,慣例會漸漸分岔成兩種。
佐證:
引句:「這一篇只管 Phase 0:定最小架構、信任邊界、決策紀錄,並約定 Phase 1 的範圍。」
file: `/Users/enzo/rtb-production-agent-demo/CLAUDE.md:53`
file: `/Users/enzo/rtb-production-agent-demo/docs/rtb-production-agent-demo-knowledge/Projects/RTB_Agent_Phase0架構.md:1`

## Finding 2 lumos 測試指令與驗收指令現在對不上,同步動作沒有落點
severity: minor
blocking: 否 判準:計劃已自承落差,只缺負責的落點與檢查,不影響本階段(不含程式)。
問題:現行 `.lumos/config.json` 的測試指令是系統 `python3 -m pytest`,但計劃的驗收指令是 `.venv` 內的 pytest,且計劃自己說系統 Python 被 EXTERNALLY-MANAGED 擋住。也就是說在 Phase 1 建好 .venv 並改設定之前,lumos 測試指令跑不起來(系統沒有 pytest)。計劃把「改 lumos 測試指令」寫成一句散文,沒有寫進 Phase 1 範圍或驗收條目,也沒有機檢確認兩邊一致,容易忘。
佐證:
引句:「.venv 在 Phase 1 開頭建立,建好後 lumos 的測試指令要同步改成指向 .venv 內的 pytest。」
file: `/Users/enzo/rtb-production-agent-demo/.lumos/config.json:5`

## Finding 3 pytest 例外只記在單條決策,零依賴家規的入口沒同步、也沒撤除條件
severity: minor
blocking: 否 判準:例外已有記錄與理由,缺的是與家規入口對齊和回頭條件。
問題:CLAUDE.md 要求零依賴家規下幾乎不採新依賴。d6 有記錄例外與替代方案,方向正確。但 (a) PRIOR-ART 一行寫「其餘採用 Python 標準函式庫」「不引入框架」,沒把 pytest 列為例外,讀者看 PRIOR-ART 會以為全專案零依賴;(b) 「只限測試工具」的邊界沒有任何機械守衛或回頭條件(例如執行路徑一旦 import 非標準函式庫就該紅),依鐵則 4 這類承諾要附重驗條件;(c) 專案根目錄目前沒有 .gitignore,「.venv 不進版本控制」還沒有落點。
佐證:
引句:「取捨:多一個外部依賴,只限測試工具,Demo 執行時仍只用標準函式庫。」
引句:「其餘採用 Python 標準函式庫(sqlite3、http.server、urllib),不引入框架。」
file: `/Users/enzo/rtb-production-agent-demo/CLAUDE.md:65`

## Finding 4 REVISIT 只有一條,且與所承認的風險不緊鄰;另兩處風險/假設用事件式回頭條件但沒寫入口
severity: minor
blocking: 否 判準:格式與接電缺漏,屬家規文書層面,可補寫。
問題:鐵則 4 要求帶日期的回頭條件寫成獨立一行 `REVISIT:YYYY-MM-DD`,且緊鄰原句。本篇的 REVISIT 行前面隔了一條 DSP 限制,不是緊鄰 SQLite 那句。此外「http.server 精確逾時」殘留風險與「SQLite 寫入鎖能重現競態」兩條假設,只寫「Phase 1 開頭」「Phase 6 才會驗證」,沒有說事件入口在哪(哪個 Phase 驗收條目、哪支測試),純散文回頭條件依家規沒人會回頭。
佐證:
引句:「REVISIT:2026-10-21 回頭看 SQLite 單寫入者是否已限制 Phase 6 的並行 worker 測試。」
引句:「緩解:Phase 1 開頭先做最小實驗,實驗不過就換做法,不硬撐。」
file: `/Users/enzo/rtb-production-agent-demo/CLAUDE.md:56`

## Finding 5 lands_in 沒指出 Phase 0 這份架構本身落在哪
severity: minor
blocking: 否 判準:落點資訊不完整,不阻礙設計,補上節點名即可。
問題:鐵則 5 要求計劃寫 `lands_in`(現況落在哪幾篇或新開哪一篇)。本篇只說 Phase 1 會新開兩篇 Systems 節點,沒給節點名,也沒說 agent 行程(佇列、狀態、執行閘)的架構與信任邊界圖之後由哪一篇承接。Phase 0 的主要產出正是這張邊界圖,卻沒有家,之後 Phase 2 起的程式檔要找「當初邊界為什麼這樣畫」會找不到。
佐證:
引句:「lands_in: 尚未有程式,Phase 1 會新開兩篇 Systems 節點:Mock DSP、確定性指標計算。」
file: `/Users/enzo/rtb-production-agent-demo/CLAUDE.md:57`

## Finding 6 分析端與執行端同屬一個行程,「隔離」的強制方式沒寫,也沒記錄不另拆 agent 的理由
severity: minor
blocking: 否 判準:切分本身符合 handoff 第 5 節(未多拆),缺的是依第 5 節要求的一句記錄。
問題:handoff 第 5 節允許因 permission 邊界獨立出「Execution Agent 或隔離的 Execution Runtime」,且新增邊界要記錄解決的具體風險與「不用新 Agent 為何不夠」。本篇圖上把分析端與執行端畫在同一個 Agent 行程內,並宣稱分析端無寫入權限、只有執行閘持有憑證,但沒說在同一行程裡憑什麼強制(模組邊界、憑證只傳給執行端物件?)。這是關鍵的信任邊界,只寫在圖裡;守衛實作雖然延到 Phase 6,但選「同行程內模組隔離」而非「另一個行程」這件事本身是架構決策,現在沒有 decisions 條目。
佐證:
引句:「權限邊界:分析端不持有 DSP 寫入憑證;寫入只發生在執行端,且要通過交接文件第 8 節的檢查閘。」
file: `/Users/enzo/Downloads/RTB_PRODUCTION_AGENT_DEMO_HANDOFF.md:159`

## 已讀,無 finding 的項目
- 決策四欄(context / why_chosen / decided / valid)齊全,d1 到 d6 皆附替代方案與取捨。
- PRIOR-ART 與 RETIRE-IF 各一行,位置在計劃頂部,撤除條件可觀察。
- 元件數量:只有 agent 行程、Mock DSP、兩個唯讀畫面,未見多餘 agent 或服務;Mock DSP 獨立行程有 F1 事故的需求對應,未違反第 18.5 節「不要過度工程化」。

總結:最嚴重 severity 為 minor,blocking 共 0 條(共 6 條 finding,皆 blocking:否)。
