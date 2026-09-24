severity: major

F1 核可表單沒有綁定使用者實際看見的數字，快照核對實際上無從執行
severity: major
blocking: 是
引句:「送出時伺服器核對:表單隨機值、來源標頭(同其他 POST)、每個確認框都勾了、表單帶的展示編號等於待核可那一次、提案雜湊對得上那一筆,而且頁面上給人確認的數字等於驅動程式在 F7 進待核可時記下的數字快照;任一不對就拒絕。」
file: `/Users/enzo/rtb-12-page/src/rtb/demo/state.py:167`
file: `/Users/enzo/rtb-12-page/src/rtb/demo/page.py:857`
具體例:使用者開啟核可頁，看到「剩餘額度 100、此次增加 90」→ 之後展示狀態裡的數字被更新，但提案雜湊與展示編號沒變；表單送出的只有固定值為 1 的確認框、token、proposal_hash 與預定新增的 demo_id，沒有數字、數字雜湊或一次性畫面版本，伺服器只能拿「目前狀態」跟自己比較，仍會簽出核可 → 應拒絕與使用者當時所見數字不同的過時頁面。現有 `ApprovalForm` 也只有 numbers，沒有可在送出時證明是哪一版畫面的識別碼。
建議:伺服器產生核可頁時建立一次性 `approval_view_id`，在伺服器端綁定 demo_id、提案雜湊、關卡、數字正規化雜湊與模型文字雜湊；表單只送 view id，POST 原子核對並消耗它，任何欄位已變就要求重新載入。合約 S1032 應明訂這個跨 GET/POST 的綁定，而不是只寫無法觀測的「頁面上的數字」。

F2 「頁面讀取不寫資料庫」與顯示核可頁即寫收據互相衝突
severity: major
blocking: 是
引句:「頁面與進度讀取不寫任何資料庫;只有觸發會啟動驅動程式,驅動程式照正式程式寫它自己的暫存資料庫。」
具體例:F7 等待核可時，瀏覽器每兩秒重讀頁面 → 設計另一段要求每次顯示模型說明時由伺服器追加顯示收據；即使去重後只有第一個 GET 寫入，它仍直接違反上述規則及 S1018，實作者若遵守唯讀合約就漏收據，若遵守收據合約就讓唯讀測試失敗 → 應明確決定頁面 GET 是否允許這項受限、冪等的寫入。
建議:把 S1018 改成精確的不變量，例如「頁面讀取不得改動被測系統與展示結果；核可頁首次顯示只准在獨立收據表追加一筆，其他表不得改動」，並測試第一次與後續 meta refresh 的資料庫差異恰好只有一筆收據。

F3 流程圖對應清單仍漏掉真正決定分析分支的 RoutePath 與 NoActionReason
severity: major
blocking: 是
引句:「系統的結果列舉每一個成員,在流程圖裡都要對得到一條邊或一個節點;對應表的鍵一律是(列舉類別, 成員名)」
file: `/Users/enzo/rtb-12/src/rtb/analyzer/policy.py:114`
file: `/Users/enzo/rtb-12/src/rtb/analyzer/policy.py:129`
file: `/Users/enzo/rtb-12/src/rtb/analyzer/policy.py:252`
file: `/Users/enzo/rtb-12/src/rtb/analyzer/flow.py:156`
具體例:分析端因缺少狀態資料而不提案 → `NoActionReason.MISSING_STATE_OR_METRICS` 與「配速沒有偏低」最後都只變成 `NoAction`；正式 `decide()` 丟掉 reason，而 `advance()` 只回傳 `TaskState`，驅動程式看不到實際原因，卻會照設計寫出一條確定的白話判斷。另一方面，候選逾時退回規則時的 `RoutePath.FALLBACK_TIMEOUT` 也不在第 3 版列出的對應清單，S1024、S1026 可以全綠但流程圖沒有這些分支 → 應保存並顯示當時真正走過的原因與路由，不能從共同終態猜測。
建議:把 `RoutePath`、`NoActionReason` 納入流程圖與白話說明的封閉清單；正式流程要提供結構化的決策觀測結果或事件，至少包含 decision、reason、route path，並由驅動程式直接記錄。沒有觀測到原因時顯示「無法還原」，不得自行推導。

F4 單一情境重跑會混合兩次展示，卻沒有為其餘六個情境保存來源
severity: major
blocking: 是
引句:「伺服器持有「目前顯示的展示狀態」:全部跑一次整份換新;單一情境重跑只替換那一個情境,其他六個情境、流程圖、已知限制原樣保留;花費分開顯示「這次重跑的花費」與「上一次全部跑一次的花費」。」
file: `/Users/enzo/rtb-12-page/src/rtb/demo/state.py:175`
file: `/Users/enzo/rtb-12-page/src/rtb/demo/state.py:202`
具體例:全部跑一次產生展示 A，之後單獨重跑 F3 產生展示 B → 畫面與靜態報告頂端只有 `DemoState.demo_id=B`，但 F1、F2、F4–F7 實際來自 A；`Scenario` 沒有來源展示編號或執行時間，報告會看起來像七個情境都在 B 跑過 → 應逐情境標明證據來自哪次執行，或明確把它標成跨展示合成視圖。
建議:為每個 Scenario 加 `source_demo_id`、`source_started_at`，單一重跑替換時保留其餘六個來源；頁面與報告逐情境顯示來源。S1020、S1040 應增加來源不被改寫的斷言，靜態報告標題也應說明它是合成快照。

F5 靜態報告沿用絕對樣式表網址，直接開檔時會失去全部版面
severity: major
blocking: 是
引句:「另存一份到 ~/.rtb/demo-reports/(檔名含展示編號與時間),連同同源樣式表一起;只保留最近 20 份,多的從最舊的刪。」
file: `/Users/enzo/rtb-12-page/src/rtb/demo/page.py:40`
file: `/Users/enzo/rtb-12-page/src/rtb/demo/page.py:174`
file: `/Users/enzo/rtb-12-page/src/rtb/demo/page.py:206`
具體例:2b 把 `render_report()` 的結果與 `demo.css` 存進 `~/.rtb/demo-reports/`，使用者直接開啟該 HTML → 報告仍含 `href="/static/demo.css"`，瀏覽器會找 `file:///static/demo.css`，不會讀旁邊的 CSS，流程圖與表格因此以無樣式文件呈現；S1039 只檢查有存檔與保留二十份，仍會通過 → 應讓靜態報告引用實際隨報告保存的相對樣式表。
建議:`render_report` 使用獨立的相對路徑如 `demo.css`，動態頁維持 `/static/demo.css`；新增測試把報告與 CSS 寫到暫存目錄，解析 link 並確認從報告位置能解析到存在的檔案。

F6 F3 的展示斷言只驗 DSP 寫入一次，漏掉「同時只花一次分析費用」的正式合約
severity: major
blocking: 是
引句:「F2、F3:執行迴圈經展示啟動器在指定死點猝死,重啟時時鐘往後撥,從持久事實恢復;F3 把同一則訊息投遞兩次。斷言:恢復後 DSP 上只套用一次。」
file: `/Users/enzo/rtb-12/tests/analyzer/test_task_lease.py:80`
file: `/Users/enzo/rtb-12/tests/analyzer/test_task_lease.py:111`
file: `/Users/enzo/rtb-12/tests/executor/test_crash_recovery.py:359`
具體例:兩個分析工作者同時取得同一任務並各呼叫一次昂貴分析，但執行端靠冪等鍵只讓 DSP 寫入一次 → 第 3 版的 F3 oracle 只檢查 DSP，仍把情境標成「照預期跑完」；正式 F3 合約與既有測試還要求同一時間只有一個有效租約持有者花分析費用 → 應在這次展示本身斷言分析呼叫次數與租約互斥，不可用執行端冪等性掩蓋分析端重複花費。
建議:F3 的完成條件增加任務識別不變、狀態轉換合法、重複投遞只產生一次副作用，以及兩個同步分析工作者中恰好一方進入付費判斷；新增展示層真並行測試，不能只依賴驗證器另跑的既有測試。

6 條,blocking 6。