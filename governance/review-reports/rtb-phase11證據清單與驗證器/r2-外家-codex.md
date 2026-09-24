severity: major

### F1 `symbols` 指到的關鍵程式不一定被納入雜湊範圍

severity: major

blocking: 是（照 spec 實作會漏掉宣稱明列的關鍵程式變更，直接破壞 stale evidence 閘）

引句:「symbols:宣稱依賴的關鍵函式,寫成「檔:名稱」或「檔:類別.方法」。」

file: `src/rtb/dsp/server.py:235` 實際關鍵寫入入口可被列為 symbol，但第 3 步的閉包起點只有 scope 與證據測試檔。

第 2 步只確認 symbol 存在，第 3 步卻未要求 symbol 所在檔必須屬於 scope；若測試透過替身或間接入口而未靜態匯入該檔，清單可在沒有其雜湊的情況下通過。該 symbol 後續被改動時，不會觸發「證據是舊版本」的擋下。

### F2 靜態匯入閉包漏掉 Python 隱式執行的套件初始化檔

severity: major

blocking: 是（依賴閉包會漏掉實際執行的正式程式，讓變更避開雜湊與語意複審）

引句:「沿靜態匯入(檔案裡任何位置的 import,含函式內與相對匯入)在 src/rtb 內遞迴,得到依賴閉包」

file: `src/rtb/__init__.py:1` 此初始化檔非空，且第 8 行設定執行期 `PROGRAM_VERSION`。

實跑 `PYTHONPATH=src … -c 'import rtb.dsp.server'` 確認 `rtb/__init__.py` 會載入，但 AST 中不需要出現對它的顯式 import。spec 沒要求把每個匯入模組的父套件 `__init__.py` 納入 scope，也沒要求把證據測試的父套件初始化檔納入 harness。

### F3 `enumerations` 的來源檔被排除在路徑護欄之外

severity: major

blocking: 是（本機外部路徑或大小寫錯誤可以通過，造成不可重現及 repo 外輸入）

引句:「路徑規則:scope、harness、symbols 的路徑一律是相對 repo 根、用 / 分隔」

file: `src/rtb/dsp/server.py:110` 正式登錄表 `CAMPAIGN_WRITE_ACTIONS` 是實際 enumeration 來源。

`enumerations` 同樣帶「來源檔:常數名」，但第 2 步及 S801 都只把 scope、harness、symbols 交給路徑規則。照字面實作，enumeration 可指向絕對路徑、`..`、符號連結或大小寫錯誤的本機檔，導致本機通過而 CI 找不到或讀到不同內容。

### F4 CI 合約沒有鎖住驗證器必須位於獨立平行工作

severity: major

blocking: 是（照合約測試實作仍可把 F7 加回既有 checks 工作，重現第 1 輪已修的問題）

引句:「接線要有合約:比照既有「防止 CI 步驟看起來有跑、其實不擋」的接線檢查,鎖住新工作的指令字串」

file: `.github/workflows/ci.yml:10` 現況只有 `checks` 一個工作，測試位於同一工作第 24–25 行。

file: `tests/test_static_wiring.py:109` 既有 `runs_tool` 只在全域指令清單尋找精確字串，不辨認指令屬於哪個 job。

S813 只驗精確指令及 `continue-on-error`、`|| true`、`if`，沒有驗 job 名稱、與 `checks` 分離或兩者無 `needs` 關係。實作者把驗證器步驟放進 `checks`，S813 仍可通過，但 F7 會在同一工作裡再跑一次並拉長原工作。

### F5 總時限只終止 pytest 父行程，可能留下測試啟動的孤兒行程

severity: minor

blocking: 否（會留下本機資源而非放過錯誤，屬有具體失敗場景的清理缺口）

引句:「輸出 JUnit XML 到暫存檔、總時限 900 秒」

file: `tests/dsp/conftest.py:38` DSP 測試以 `subprocess.Popen` 另啟子行程。

file: `tests/dsp/conftest.py:94` 子行程只在 pytest fixture teardown 中逐一停止。

Python `subprocess.run(timeout=…)` 超時只 kill 直接啟動的 pytest 行程；pytest 被強制終止時 fixture teardown 不會執行，DSP 等孫行程可留在本機。spec 應定義以 process group 啟動及超時後整組清理，或把此限制列為已知風險。

### F6 (挑戰使用者裁定) 禁止清單自填結果，不等於可以省略驗證器產生的結果出處

severity: major

blocking: 是（上游明定的 verifier identity 與結果無法稽核或重現）

引句:「清單不准有「結果」欄;驗證器只做機械檢查」

file: `/Users/enzo/Downloads/RTB_PRODUCTION_AGENT_DEMO_HANDOFF.md:482` 原始要求明列 manifest 必須保留 verifier identity 與結果。

file: `/Users/enzo/Downloads/RTB_PRODUCTION_AGENT_DEMO_HANDOFF.md:494` 第 15.4 節另要求 reviewer 結論帶輸入版本與理由。

使用者禁止的是投稿者在輸入清單自填 `passed`，但 spec 目前只印通過或擋下，沒有由驗證器產生且帶驗證器版本／雜湊、輸入清單雜湊與結果的輸出卷證。這會讓日後無法判定某次 PASS 是哪一版驗證器對哪一版輸入算出的；可在清單外產生結果檔，不必放寬「輸入不得自報結果」。

## 第 1 輪修正已驗收

- 增量 1 已改成五份必要清單同時到齊，不再被完整性閘自行擋死。
- F6 已從冪等宣稱移到權限／護欄的死信重放證據。
- scope 已新增沿正式程式靜態匯入計算閉包；本輪的新缺口另見 F1、F2。
- harness 已強制包含證據測試檔、路徑上的 conftest、pyproject 與靜態匯入的測試模組。
- conftest 修改測試結果的假綠場景已納入造假示範。
- 欄位白名單已明定套用到每一層物件。
- JSON 重複鍵、NaN、Infinity、布林冒充整數與超長整數均有明確處置。
- scope、harness、symbols 已禁止絕對路徑、`..`、符號連結與大小寫不符；遺漏的 enumeration 見 F3。
- symbol 已限制為最外層函式／類別／直接方法，且重指派會擋。
- enumeration 已涵蓋普通與帶型別賦值、固定容器字面及非字面值擋下。
- policy 使用固定範圍詞時已強制要求 enumeration。
- enumeration 少覆蓋與 covers 多寫不存在項目都會擋下。
- evidence 已限制為函式層節點；參數個案節點會被拒絕。
- 未知 pytest 節點與帶參數方括號的節點已有專屬合約。
- pytest 工作目錄、目前直譯器、停用快取、停用自動外掛及 900 秒上限均已寫明。
- `xfail_strict=true` 已補上，XPASS 不再被 JUnit 誤當一般通過。
- skip、xfail、未收集、取消選取、失敗及逾時均已定義為擋下。
- pytest 結束代碼 4 要指出未知節點，不准只報籠統錯誤。
- JUnit 的 classname/name 對回規則與失敗訊息輸出已補寫。
- 驗證器測試改用小型臨時 repo，不再於既有全套測試內巢狀重跑正式 F7。
- 正式清單測試只直接呼叫前五步，命令列沒有跳過真跑的旗標。
- 工具與產品雙向禁匯入已分別採 tools/ruff.toml 與 AST 邊界測試。
- 動態匯入、讀檔與子行程依賴看不到的限制已如實列入天花板。
- 結束代碼 0、1、2 已分開定義並新增專屬合約。
- claim_id 與檔名完全一致已有專屬合約。
- CI 精確指令、吞錯及條件式繞過已有合約；job 歸屬缺口另見 F4。
- 語意審查已綁清單 sha256、rubric 與理由，並設回訪日期。
- RETIRE-IF 已改成可由帶標籤 Issue 查核的事件紀錄。
- 雜湊重貼風險的回訪條件已接到代碼審／事故覆盤的 Issue 入口。
- `CAMPAIGN_WRITE_ACTIONS` 已訂正為 server 路由層，而非 store。
- Mock-DSP 過大的合約措辭在回退時不再恢復。
- 作廢不做版本比對及缺少 void-vs-void 並行測試已如實界定在五條宣稱之外。
- Systems 家在回退時改成撤除並清空 about_code，避免孤兒節點。
- F7 重跑取捨與新增工作造成的額外撞擊機會已明載使用者裁定。

## 逐節結果

- 〈這份計劃在解決什麼〉：已讀,無 finding。
- 〈使用者裁定〉：已讀；結果出處問題見 F6。
- 〈現況〉：已讀,無 finding。
- 〈宣稱清單〉：已讀；見 F1、F3。
- 〈驗證器〉：已讀；見 F1、F2、F3、F5。
- 〈語意審查入口〉：已讀；見 F6。
- 〈五條宣稱〉：已讀,無 finding。
- 〈CI 與本機〉：已讀；見 F4、F5。
- 〈拆增量〉：已讀,無 finding。
- 〈合約候選〉：已讀；S801、S810、S813 未封住 F1–F4。
- 〈回退〉：已讀,無 finding。
- 〈審計修正紀錄〉：已讀,無 finding。
- 交叉引用：F7 Issue、交接文件第 15.4 節、治理卷證目錄及 Mock-DSP 登錄表均存在且目標相符。

## 實務隱患逐類

- 金流：無；驗證器不改產品狀態，正式測試使用本機資料庫與模擬 DSP。
- 對外送出：無；所列證據路徑只連本機模擬服務。
- 不可逆：無；清單與驗證器本身只讀，沒有正式副作用。
- 守衛面：有；既有雜湊重貼、covers 自報及靜態分析天花板已誠實列出，另有 F1–F4、F6。
- 並行：無共享狀態競態；CI 平行工作位於不同 runner，JUnit 使用獨立暫存目錄。接線未保證真的平行，見 F4。
- 資源與逾時：有；900 秒能擋住 pytest，但不能保證清理其孫行程，見 F5。
- 供應鏈與環境：無新增 finding；Python、pytest 與開發工具已固定版本，並停用自動載入外掛。
- 機密與路徑：enumeration 來源尚可逸出 repo，見 F3；其餘三類路徑已有邊界。
- 刻意繞過：雜湊重貼、換範圍詞、動態匯入及 covers／故障注入語意造假均已正確列為天花板，不據此否決整份設計。

總結:最嚴重等級 major;blocking 條數 5。
