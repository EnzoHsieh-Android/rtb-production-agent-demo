severity: major

### 1. sitecustomize 自動載入注入是專案裡原本沒有的第二種當機/故障注入做法

severity: major
blocking: 是 這是架構對齊鏡頭下唯一要擋的判準(第二種做法),而且有現成的既有手法可以達到同樣效果,不必引入新機制
引句:「注入點用 Python 啟動時自動載入的 sitecustomize 機制,只在測試的 PYTHONPATH 下出現,正式程式碼不加鉤子。」

說明:專案裡現有兩種當機/故障注入,都是「顯式、寫在看得到的地方」:

1. `src/rtb/dsp/server.py` 的旗標式注入:啟動時帶 `--fault-injection` 才接受 `X-Fault` 標頭,故障種類是封閉列舉(`FAULT_MODES`),寫在正式程式碼裡、受旗標保護。
2. `tests/dsp/test_store.py` 的行程猝死測試(`CRASH_CHILD`,389–419 行):用 `subprocess.run([sys.executable, "-c", CRASH_CHILD, ...], env={"PYTHONPATH": src})`,把要監控的猝死點(`store._record_idempotency = die`)直接寫死在傳給 `-c` 的內嵌腳本文字裡——沒有另開可被自動載入的目錄,沒有環境變數決定「在哪個位置包一層」,監控點在同一段測試碼裡一眼看到。

增量 2 的設計是第三種:在 PYTHONPATH 多放一個目錄,裡面放一支 `sitecustomize.py`,由 Python 直譯器啟動時**自動**載入(不需要任何 `import` 陳述式),讀一個環境變數,再**動態決定**去包哪一個正式程式碼裡的位置(執行迴圈的哪一步)。這跟既有兩種做法都不同:比起 (1),它不是正式程式碼裡受旗標保護的封閉列舉,而是外部隱式掛勾;比起 (2),既有的猝死測試監控點是寫死在同一段可見腳本裡,新設計是外部目錄 + 環境變數決定的可變掛勾點。

而且這個新機制並非必要:`tests/executor/test_runner.py`(67–74 行)裡既有的執行迴圈子行程測試是用 `python -m rtb.executor.runner <argv>` 啟動,但完全可以比照 `CRASH_CHILD` 的手法——改用 `python -c "<inline script>"`,在腳本裡先 `import rtb.executor.runner as runner` 並直接對目標函式做 monkeypatch(例如 `attempt_store.xxx = die`),再呼叫 `runner.main()`。這樣子行程一樣是真的、時鐘一樣是真的系統時間、猝死點一樣可以精準命中「寫完嘗試中、還沒呼叫 DSP」等位置,而且完全落在既有慣例裡,不必新增 PYTHONPATH 注入目錄與環境變數驅動的自動載入掛勾。「可見性逾時縮短成 1 秒」也同理可以在同一段 `-c` 腳本裡直接 monkeypatch 常數,不需要靠 sitecustomize。

file: `tests/dsp/test_store.py:389-419`
file: `src/rtb/dsp/server.py:1-8`
file: `src/rtb/dsp/server.py:55-63`
file: `tests/executor/test_runner.py:67-74`

### 2. S122 的原始碼掃描守不住 sitecustomize 這種自動載入通道,且比既有故障注入通道少一層執行期防線

severity: major
blocking: 是 這個守衛測試給的是假保證,會讓「F2、F3 的猝死恢復證據」建立在一個實際上守不住生產環境誤帶 PYTHONPATH 的假設上
引句:「正式程式碼(src/)不讀這個變數、不 import 這個目錄,用原始碼掃描擋住。」

說明:S122 的斷言與測試名稱都指向「查有沒有 `import` 這個目錄、有沒有讀那個環境變數」:

引句:「正式程式碼不應讀取當機注入的環境變數、不應匯入當機注入的目錄。」

對照既有的邊界掃描寫法(`tests/dsp/test_store.py:93-109` 的 SQL 關鍵字正則掃描、`tests/dsp/test_store.py:491-509` 的 import 語句 AST 掃描),這些既有掃描能成立,是因為被禁止的東西一定要以**原始碼文字**的形式出現在被掃的檔案裡才會生效(寫了 `UPDATE` 字樣才會真的下 SQL;寫了 `import` 陳述式才會真的匯入)。但 sitecustomize 的整個設計重點正是「不需要任何 `import` 陳述式,Python 直譯器啟動時只要那個目錄在 `sys.path`/`PYTHONPATH` 上就自動載入」——這正是 `tests/dsp/test_store.py:491-509`(`test_runtime_code_imports_only_the_standard_library_and_this_project`)自己已經記錄過、第二輪合約審計才補上的同一類漏洞:純掃 `import` 陳述式擋不住「不寫 `import` 也能被載入/執行」的路徑,該測試才特地加了對 `__import__`/`importlib.import_module` 動態匯入的攔截。S122 現在要守的 sitecustomize,結構上就是「不需要 import 陳述式即可生效」的那一類,`src/` 底下再怎麼掃都掃不到「部署環境不小心把測試用的 PYTHONPATH 帶進正式行程」這個真正的風險——那是行程啟動時的環境/部署屬性,不是 `src/*.py` 的原始碼文字屬性,原始碼掃描在定義上就到不了。

此外,既有的故障注入通道(DSP 標頭)不只是「正式程式碼不用它」的消極聲明,還有主動的執行期防線:`tests/analyzer/test_boundaries.py:29`(`test_the_agent_cannot_reach_fault_injection_on_production_style_servers`)證明生產樣態伺服器整套跑起來不受故障注入影響,`tests/analyzer/test_boundaries.py:75`(`test_fault_headers_sent_to_production_style_servers_are_refused`)更進一步證明就算有人硬送 `X-Fault` 標頭給沒開旗標的伺服器,伺服器本身也會主動拒絕(`src/rtb/dsp/server.py` 文件註解:「旗標關閉時收到標頭一律回 400」)。sitecustomize 設計沒有對等的主動防線——它沒有「正式進入點在偵測到這個環境變數/目錄存在時主動拒絕啟動」這一層,只有 S122 這一層守不住的靜態掃描。

file: `tests/dsp/test_store.py:93-109`
file: `tests/dsp/test_store.py:491-509`
file: `tests/analyzer/test_boundaries.py:29`
file: `tests/analyzer/test_boundaries.py:75`
file: `src/rtb/dsp/server.py:233-242`

---

其餘部分(三個當機點的恢復邏輯、F3 的兩個執行迴圈先後交遞、確認時機說明)沒有再發現「引入第二種做法」或「跨層直呼」等級的問題;這兩條都指向同一個根因——增量 2 引入了一個 PRIOR-ART 段落未交代、專案裡查無先例的自動載入式測試掛勾機制(全庫搜尋 `sitecustomize` 只出現在這份計劃與其審查卷證裡),而且它引入的守衛測試（S122）在既有的邊界掃描慣例下結構性地驗不出它宣稱要擋的風險。建議增量 2 改用既有的「`-c` 內嵌腳本 + 顯式 monkeypatch」手法(比照 `tests/dsp/test_store.py` 的 `CRASH_CHILD`)取代 sitecustomize,若仍要保留原始碼掃描則需誠實標註它只防「複製貼上進 src/」這一種情境,不能宣稱擋住注入機制本身。
