severity: blocker

### F1 增量 1 完工當下,CI 平行工作會因為「少五條」自己先紅

severity: blocker
blocking: 是(會直接讓增量 1 合併後的 CI 紅,不是理論疑慮,是設計本身互斥;沒有任何步驟豁免增量 1)
引句:「沒有任何清單、或少了五條必要宣稱之一也擋」
- file: `/tmp/rtb-phase11-r1.md:51` 第 1 步定義「少五條必要宣稱之一就擋」,沒有限定「僅在全部增量完成後才生效」。
- file: `/tmp/rtb-phase11-r1.md:71` CI 平行工作「跑同一條指令」,對象是 repo 裡真正的 `claims/` 目錄,不是測試用的臨時目錄。
- file: `/tmp/rtb-phase11-r1.md:79` 增量 1 只做冪等與總曝險兩份清單,其餘三份要等增量 2。
- 增量 1 合併後,`claims/` 目錄裡只有 2 份必要清單、缺 3 份;同一條指令跑在真實目錄上,第 1 步的「少五條」判定會立刻擋下,CI 平行工作的結束代碼變 1,紅燈。這跟「拆增量」想要的「增量 1 先落地、CI 綠」互斥,增量 2 的 rollback 若被觸發也會退回這個紅燈狀態。單元測試(S807)可以用假的臨時目錄繞開這個問題,但 spec 明講 CI 平行工作跑的是同一條指令對真實 `claims/`,不是對測試夾具。
- 修法方向(不是本報告要裁定,但要點出討論空間):增量 1 的「五條必要宣稱」名單本身應該分階段(先兩條、增量 2 補滿),或增量 1 先不啟用「少五條就擋」這一格,兩者 spec 都沒選。

### F2 [S807]、[S809] 沒有標明落在哪個增量,而且結構上不可能同時滿足

severity: major
blocking: 是(合約候選不標增量,會讓實作者在增量 1 就寫出跟 F1 一樣會自爆的測試,或者漏掉,兩種结果都要人在當下臨場裁定)
引句:「五份正式清單在目前版本上跑驗證器應全部通過」
- file: `/tmp/rtb-phase11-r1.md:93` [S809] 要求「五份正式清單」全過,但五份要到增量 2 才存在,increment 1 階段這條測試若寫出來、跑在真實 `claims/` 上必敗。
- file: `/tmp/rtb-phase11-r1.md:91` [S807] 「缺少五條必要宣稱任一條時應擋下」——這條本身可以用臨時測試夾具在增量 1 就驗證,不必等五份清單都存在;但 spec 沒說清楚它綁的是臨時夾具還是真實 `claims/`,跟 F1 是同一個模糊點的另一面。
- 「拆增量」整節(`/tmp/rtb-phase11-r1.md:77-80`)只切了「驗證器步驟」跟「清單」,沒有切「合約候選 S800–S809 分別在哪個增量的測試套件出現」,接手的人不看這份報告會猜錯。

### F3 結束代碼 0/1/2 是對外唯一介面,合約候選裡沒有任何一條在測它

severity: major
blocking: 是(CI 平行工作純粹靠結束代碼判紅綠,這個介面沒有專屬合約測試守著,將來改動容易在不知不覺間把 1 跟 2 弄混而沒有測試發現)
引句:「結束代碼 0 是全通過、1 是有擋下、2 是清單讀不懂」
- file: `/tmp/rtb-phase11-r1.md:50` 定義三種結束代碼。
- file: `/tmp/rtb-phase11-r1.md:84-93` [S800]–[S809] 十條都只講「驗證器應擋下」,沒有一條明講「應以結束代碼 1 擋下、清單讀不懂應以結束代碼 2 擋下」,0/1/2 的區分本身沒有落地成任何一條可判定的合約。
- CI 只認結束代碼(`/tmp/rtb-phase11-r1.md:71`「結束代碼不是 0 就紅」),1 跟 2 對 CI 而言效果一樣(都紅),但對本機使用者除錯很重要——這正是「spec 設計段寫了卻沒有條款」的落點。

### F4 claim_id 跟檔名一致的檢查沒有專屬合約

severity: minor
blocking: 否(格式檢查,失敗場景明確但影響範圍小,寫錯 claim_id 頂多讓那份清單被當成格式錯誤擋下,不會被誤判通過)
引句:「claim_id(跟檔名一致)」
- file: `/tmp/rtb-phase11-r1.md:41` 定義規則。
- file: `/tmp/rtb-phase11-r1.md:51` 第 1 步「格式」裡重複提到「claim_id 跟檔名一致」。
- file: `/tmp/rtb-phase11-r1.md:84` [S800] 只講白名單鍵,沒有一條合約候選提到 claim_id 對不上檔名時的行為,實作者可能順手塞進 S800 的測試裡,也可能漏掉。

### F5 pytest 找不到節點(結束代碼 4)與 classname 對應規則,沒有專屬合約,而且是最脆弱的一段解析邏輯

severity: major
blocking: 是(用臨時實驗驗證過:節點編號寫錯時 pytest 回結束代碼 4、JUnit 檔案裡連一筆 testcase 都沒有,連原本會過的節點也一起消失;驗證器要從 stderr 文字反查是哪個編號,這段解析完全沒有被 S804 這類籠統的合約測試逼著驗證)
引句:「任一個編號找不到時,pytest 回結束代碼 4 而且整批一支都不跑」
- file: `/tmp/rtb-phase11-r1.md:58` 描述此行為。
- file: `/private/tmp/p11scratch/test_sample.py` 與同目錄 `out.xml`:本席用 `/Users/enzo/rtb-production-agent-demo/.venv/bin/python -m pytest test_sample.py::test_ok test_sample.py::test_nonexistent -q --junitxml=out.xml` 實測,pytest 9.1.1 結束代碼確實是 4,`out.xml` 的 `<testsuite ... tests="0">`,`test_ok` 本身合法卻也沒有任何一筆紀錄——證實 spec 這句敘述屬實,但也證實這條路徑(結束代碼 4、要從 stderr 反查編號)跟「一般擋下」(結束代碼 1、有 JUnit 紀錄可查)是兩套完全不同的程式邏輯。
- file: `/tmp/rtb-phase11-r1.md:88` [S804] 的測試名是 `test_a_skipped_or_failing_or_missing_test_is_blocked`,籠統涵蓋「沒收集到」,沒有拆出「整批因找不到節點而死掉(結束代碼 4)」這條獨立路徑,也沒有涵蓋第 59 行的 classname 對應規則(路徑轉點、去 .py、類別方法接類別名)這段字串轉換邏輯。這段解析最容易因 pytest 版本升級而悄悄壞掉,卻是唯一沒有專屬合約測試的核心邏輯。

### F6 「也不呼叫 lumos」這條沒有落進 [S808]

severity: minor
blocking: 否(不呼叫 lumos 是設計原則,不是使用者看得到的失敗場景;就算漏測,頂多是耦合但不會讓驗證器誤判通過或擋下)
引句:「也不呼叫 lumos;rtb 的任何模組也不准匯入 tools」
- file: `/tmp/rtb-phase11-r1.md:60` 原句包含「不呼叫 lumos」。
- file: `/tmp/rtb-phase11-r1.md:92` [S808] 標題只寫「不應匯入 rtb 的任何模組,rtb 的任何模組也不應匯入 tools」,沒提 lumos 呼叫。

### F7 回退節刪掉檔案後,「每支檔有家」的 Systems 節點會變孤兒,spec 沒提怎麼收

severity: major
blocking: 是(這是計劃自己的 lands_in 指向的新節點;若真的走回退,節點留著卻沒有對應的家可管,之後 `lumos doctor` 的 S8–S10 舊帳檢查會抓到,但 spec 完全沒提「回退時要不要一併刪 Systems/宣稱驗證器 節點或改寫它的 about_code」)
引句:「刪掉 tools/verify_claims.py、claims/ 與 CI 平行工作,不影響任何產品程式或既有測試」
- file: `/tmp/rtb-phase11-r1.md:108` 回退節只講刪程式與 CI,沒提圖譜節點。
- file: `/Users/enzo/rtb-production-agent-demo/docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11證據清單與驗證器_計劃.md:10` `lands_in: Systems/宣稱驗證器`,本席核對過這篇節點目前還不存在(`ls docs/rtb-production-agent-demo-knowledge/Systems/` 沒有這個檔),要新開;一旦新開又走回退,節點會失去它管理的檔案。
- CLAUDE.md 鐵則 5(每支檔有家)本身沒有明講「刪程式檔時節點怎麼處理」,但 spec 至少該補一句「回退時一併處理/刪除該節點或轉交其他家」,不然這是接手人三個月後會踩到的洞。

### F8 RETIRE-IF 的「連續半年零真問題」條件,沒有任何機制分類「真問題」跟「格式/雜湊重貼」

severity: major
blocking: 是(這條 RETIRE-IF 要能被回頭檢查,前提是半年後有人能區分每一次擋下是真的抓到問題還是純格式重貼;spec 沒有設計任何記錄或分類機制,半年後這件事只能翻 CI 歷史紀錄或人的記憶去重建,等於條件寫了但沒辦法真的被觀測)
引句:「連續半年沒有任何一次擋下是真問題」
- file: `/tmp/rtb-phase11-r1.md:15`(對應圖譜計劃筆記同一行)只寫條件,沒寫誰在什麼時候記錄「這次擋下是真問題還是格式重貼」。
- 對照:圖譜計劃筆記本身在「合約候選」與「設計」都沒有任何一項要求驗證器或 CI 把「擋下原因分類」寫進任何持久紀錄(例如一份擋下歷程檔),擋下訊息只印在當次 CI log 裡。

### F9 REVISIT:2027-03-31 的事件觸發沒有寫明「事件入口在哪」

severity: major
blocking: 是(CLAUDE.md 鐵則 4 要求綁事件的回頭條件要明寫事件入口,這條同時帶日期跟事件描述,但事件本身——「雜湊重貼沒被審查員抓到」——沒有講清楚誰會發現、從哪裡發現;純散文事件描述沒有可觀測的觸發點,等於到期日到了也只能憑印象判斷有沒有發生過)
引句:「若雜湊重貼沒被審查員抓到的情形發生過一次」
- file: `/tmp/rtb-phase11-r1.md:101` 完整 REVISIT 句。
- 對照 CLAUDE.md 鐵則 4:「綁事件的明寫事件入口在哪」;這裡的「發現」依賴的是某次事後稽核或事故覆盤才會浮現,spec 沒有指向任何固定的入口(例如某份 Issue 分類、某個 lumos 指令的輸出),到期時沒人知道去哪裡查有沒有發生過。

### F10(挑戰計劃筆記,非挑戰使用者裁定)已合併進圖譜的計劃筆記,CAMPAIGN_WRITE_ACTIONS 的位置寫錯,跟凍結中的 spec 副本與程式碼都對不上

severity: major
blocking: 是(這不是使用者裁定,是純技術事實描述,圖譜筆記跟程式碼、跟審查用的凍結 spec 副本三方不一致,會誤導未來實作者去 store.py 找一個不存在的常數)
引句:「從 DSP 儲存層的 CAMPAIGN_WRITE_ACTIONS 列舉」
- file: `/Users/enzo/rtb-production-agent-demo/docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11證據清單與驗證器_計劃.md:67` 寫「從 DSP 儲存層的 CAMPAIGN_WRITE_ACTIONS」。
- file: `/tmp/rtb-phase11-r1.md:70`(凍結審查副本,較新版本)已改成「從 DSP 伺服器模組(路由那一層,不是儲存層)的 CAMPAIGN_WRITE_ACTIONS」。
- file: `/Users/enzo/rtb-3b/src/rtb/dsp/server.py:110` 本席用 grep 核對,`CAMPAIGN_WRITE_ACTIONS = ("update_budget", "pause_campaign")` 確實定義在 `server.py`(路由層),`/Users/enzo/rtb-3b/src/rtb/dsp/store.py` 裡沒有這個常數,只有 `store.py:181`、`store.py:231` 引用 `op.action == "update_budget"` 的散落判斷。
- 已合併進圖譜的計劃筆記還停在舊的、錯的說法,凍結審查副本已經修正但還沒回寫圖譜——這正是「文件與現實對不上」的活案例,提醒下一個接手的人:更新 spec 之後記得同步寫回 `lumos set` 過的圖譜筆記,不要只改 /tmp 工作副本。

### F11 [S805]「驗證器自己跑、不採信外部結果」缺一個可判定的失敗場景

severity: minor
blocking: 否(這條原則已經被設計本身的執行方式——子行程呼叫 pytest、輸出到暫存 JUnit——結構性滿足,不太可能因為程式改壞而不小心讀到外部結果,是低機率、可以靠測試但不緊急)
引句:「不讀任何外部提供的測試結果檔」
- file: `/tmp/rtb-phase11-r1.md:89` [S805] 敘述本身沒有給出具體的「外部提供的測試結果檔」長什麼樣、放在哪個路徑,實作者要自己想像測試場景(例如塞一份偽造的 JUnit XML 到暫存路徑,確認驗證器仍會自己重跑而不是讀那份偽造檔)。這是可以做出來的測試,但目前 spec 對這條沒有給出具體輸入,跟其他條(S800–S804、S806)相比是相對空的一條。

### 其他章節

已讀,無 finding:PRIOR-ART/RETIRE-IF 開頭兩句(借鑑範圍界定清楚)、宣稱清單欄位白名單設計、五條宣稱各自的證據描述、Mock-DSP 措辭改窄的推理(已用程式核對:store.py 的 void 操作確實只比對鍵、不動廣告狀態欄位,跟「作廢只看鍵、不改廣告狀態」一致)、增量 2 的刻意造假示範清單、「已排除」三項實務隱患的理由。

---

最嚴重等級:blocker;blocking 條數:8(F1、F2、F3、F5、F7、F8、F9、F10)。
