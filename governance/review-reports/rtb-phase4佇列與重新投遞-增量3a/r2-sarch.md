severity: major

### 1. 忙碌改成「休息重試」的行為變更,沒有列進「被取代的既有合約」,執行迴圈筆記的系統故障 RULE 會與程式碼脫節

severity: major
blocking: 是 落地後 `docs/rtb-production-agent-demo-knowledge/Systems/執行迴圈.md` 的「系統故障…資料庫忙碌…一律停機」這條 RULE 沒被同一個提交改掉,圖譜會對外宣稱一個 3a 之後已經不成立的行為,誤導之後靠 RULE 判讀(而非讀程式碼)的人,包括對即將接手的增量 3b 或值班排查
引句:「這一輪休息、下一輪再試」

說明:第 2 版把「資料庫忙碌」從「一律停機」改成「這一輪休息、下一輪再試;連續 3 輪都忙才停機」([S135]),而且作者自己在回退段落也寫得很清楚——「忙碌改回直接停機、沒有收據時序號對不上改回停機」——證明作者知道這是行為變更,不是措辭調整。但整份計劃專門用來追蹤這類變更的「被取代的既有合約」小節,只列了 S55(鎖)、S68(鎖檔換掉停機)、S13(重啟恢復全轉)與兩支依賴鎖的測試,漏了執行迴圈筆記裡那條把「資料庫忙碌」跟「租戶設定檔壞掉或不安全、DSP 回其他 4xx、條件寫入沒進展、鎖檔被換掉」歸在同一類「一律停機」的 RULE。

具體例子:多工作者上線後,執行迴圈連續撞到 2 輪資料庫忙碌、第 3 輪成功不停機。照現有 RULE 字面(「一律停機並以非零代碼結束」),任何一輪忙碌就該立刻停機;實際行為卻是撐到連續第 3 輪才停。兩者對不上,而且沒有任何一步的合約清單要求把這條 RULE 一併改掉或撤除,落地時很可能被漏掉。
file: `docs/rtb-production-agent-demo-knowledge/Systems/執行迴圈.md:23`

### 2. 「同行程多執行緒」並行測試計劃共用既有的假 DSP,但假 DSP 沒有鎖、也沒有冪等鍵去重,對不上專案「真併發要加鎖/由底層序列化」的前例

severity: major
blocking: 是 S132(以及同樣會撞到共用假 DSP 的 S129)照字面實作,測試本身的替身沒有正確模擬真 DSP 的冪等語意,會讓「DSP 只套用一次」這類斷言在正常實作下就過不了,或者只是巧合式地綠燈,不是靠真的互斥機制過的
引句:「共用同一個資料庫檔與同一個假 DSP」

說明:「真並行驗證」節把 S129–S134 的做法定調成「同一個行程裡開多個執行緒…各自注入時鐘、共用同一個資料庫檔與同一個假 DSP」,並特別強調「假 DSP 不驗憑證時間,A 被擋的原因只可能是收據對不上」——這句話指向的正是既有的 `FakeDsp`(`tests/executor/fakes.py`),因為只有它完全不驗證憑證到期時間;真的 `DspServer`(當機測試用的那個)是會驗的。但 `FakeDsp.write()`(`tests/executor/fakes.py:65-91`)完全沒有鎖保護 `self.campaigns`/`self.writes`/`self.operations` 這些共用可變狀態,也沒有「這把鍵已經在 `self.operations` 裡就直接回原結果」的冪等去重(對照 `test_crash_recovery.py` 自己的說明:「模擬 DSP 對同一把冪等鍵的第二次寫入會直接回原結果、不留痕跡」——那是真 DSP 模擬層的行為,`FakeDsp` 沒有做)。這正好跟專案自己在「真的要讓兩個執行緒同時打同一個假服務」時的既有做法不一致:`test_crash_recovery.py` 的 `CountingHandler`/`CountingDsp` 特地用 `threading.Lock()` 保護計數(`tests/executor/test_crash_recovery.py:96-114`),而且底層的 `DspServer` 繼承 `ThreadingHTTPServer`(`src/rtb/httpkit.py:41`)——這是專案裡唯一一處「真的多執行緒同時呼叫同一個 DSP 替身」的前例,而它是有鎖的。

具體例子:S132 情境——A 卡在假 DSP 的寫入攔截點,B 撥過 A 的租約接手同一把鍵、完整跑完寫入與驗證(budget 100→150,DSP 记下 `operations[key]`);之後放行 A,A 原本卡住的 `dsp.write()` 呼叫繼續往下執行到 `apply()`。照 `FakeDsp.write()` 現有邏輯,它只檢查 `key in self.voided`,不檢查 `key in self.operations`,所以 A 會直接對同一個廣告再套用一次(budget 依然算出 150,但 `version` 多跳一次、`writes` 清單多一筆)。設計要的斷言是「DSP 只套用一次,最終已驗證」,字面實作出來的寫入次數會是 2 次而不是 1 次——不是程式碼邏輯真的錯,是測試替身沒有補齊真 DSP 既有的冪等去重語意,設計裡完全沒提到要動 `FakeDsp`。
