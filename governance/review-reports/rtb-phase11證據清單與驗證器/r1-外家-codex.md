severity: major

### F1 增量 1 會被五份清單完整性閘必然擋下
severity: major
blocking: 是——照規格實作後，第一個增量無法通過自己新增的 CI。
引句:「先做冪等與總曝險兩條清單;CI 平行工作」
驗證器步驟 1 與 S807 無條件要求五條宣稱齊全，但增量 1 只提交兩份清單；CI 會在增量 2 前固定回傳擋下，而不是可獨立交付的綠色增量。

### F2 可省略的列舉無法阻止宣稱範圍超過證據
severity: major
blocking: 是——這直接漏掉 Phase 11 明定必須擋下的 scope overclaim。
引句:「要從程式的登錄表機械列舉,寫成來源檔:常數名加過濾條件」
file: `src/rtb/dsp/server.py:110` 已有可列舉的 `CAMPAIGN_WRITE_ACTIONS`。
`enumerations` 被定義成選填，而步驟 4 與 S803 只檢查實際提供的列舉；粗心漏填整個欄位時，冪等清單不需要任何 `covers` 仍能通過。這正是本計劃用來示範的「每一種寫入動作」範圍過大案例。

### F3 證據測試本身不在雜湊合約內
severity: major
blocking: 是——測試被意外弱化後，驗證器仍可給出綠燈。
引句:「evidence:每一項是一個 pytest 節點編號,帶 covers」
file: `tests/executor/test_f7_end_to_end.py:70` 是正式證據測試本體；`tests/executor/test_f7_end_to_end.py:16` 又依賴另一個測試輔助檔。
`evidence` 只保存節點編號，`harness` 的定義只列模擬器、假物件與 fixture，沒有要求雜湊證據測試檔；若斷言被粗心刪除或改成恆真，pytest 仍通過且現有雜湊全不變。測試檔及其證據依賴必須納入可重算材料，否則「證據過期」只保護產品碼的一半。

### F4 scope 完整性全靠作者記得列檔，未列入已承認的天花板
severity: major
blocking: 是——新增或抽出的產品依賴可能繞過新舊檢查。
引句:「scope:這條宣稱涵蓋的正式程式檔清單,每一項帶 sha256」
file: `src/rtb/dsp/server.py:32` 顯示伺服器的權限路徑實際委派給 `src/rtb/dsp/capability.py`。
若權限清單列了 `server.py` 卻漏列 `capability.py`，後者改動不會造成任何雜湊失配；相同問題也會出現在日後抽出的新模組。設計不必防刻意漏列，但必須提供依賴閉包守衛，或把「粗心漏列 scope 檔案會假綠」明列為天花板及重驗入口。

### F5 獨立語意審查沒有版本綁定或執行入口
severity: major
blocking: 是——最關鍵的人工防線可以被無意間完全略過。
引句:「語意是否充分留給獨立審查員、驗證器不採信審查員結論」
file: `/Users/enzo/Downloads/RTB_PRODUCTION_AGENT_DEMO_HANDOFF.md:494` 要求 reviewer 結論帶 rubric、輸入版本與理由。
spec 沒定義何時觸發獨立審查、審哪個 manifest 版本、結果存哪裡或由哪一道流程確認已完成；作者重新貼雜湊及 `covers` 後，CI 可以在沒有任何語意審查的情況下通過。這不要求驗證器採信 reviewer，只要求另有版本綁定的流程守衛來落實使用者裁定。

### F6 重複 JSON 鍵會被標準解析器靜默覆蓋
severity: minor
blocking: 否——屬格式守衛缺口，但有具體的粗心假綠場景。
引句:「欄位白名單,多一個不認得的鍵就擋」
file: `/opt/homebrew/Cellar/python@3.14/3.14.6/Frameworks/Python.framework/Versions/3.14/lib/python3.14/json/decoder.py:216` 將鍵值對直接轉成 `dict`，重複鍵只保留最後一份。
合併衝突若留下兩個 `scope` 或 `evidence`，後者會靜默覆蓋前者，而且仍符合白名單與型別檢查；規格沒有要求以 `object_pairs_hook` 或等價方式拒絕重複鍵。

### F7 pytest 子行程沒有執行上限
severity: minor
blocking: 否——失敗時會卡住而非假綠，但驗證器不能穩定產出通過或擋下。
引句:「把所有證據的節點編號交給 pytest」
file: `tools/mypy_sarif.py:158` 現有工具對子行程明訂 `timeout=600`。
證據測試若因並行回歸或未回收子行程而死鎖，驗證器會一直等待，直到 CI 平台的工作級上限才被外部取消；spec 應定義總逾時、逾時的結束代碼及診斷內容。

### F8 (挑戰使用者裁定) 平行工作會把已知不穩定的 F7 再跑一次
severity: minor
blocking: 否——不會放過錯誤，但會增加已知的非產品紅燈。
引句:「CI 另開一個跟 checks 平行的工作」
file: `tests/executor/test_f7_end_to_end.py:97` 以牆鐘 60 秒作為成敗條件。
file: `docs/rtb-production-agent-demo-knowledge/Issues/F7端到端在CI上偶爾超過60秒.md:12` 已記錄同一提交曾在 CI 跑 67 秒而誤紅。
目前 `checks` 已跑 F7，新驗證工作又把它列為證據，等於每次推送在兩台 runner 各承受一次已知波動；使用者裁定只接受原本的偶發重跑，沒有裁定把暴露次數加倍。

### F9 回退會故意恢復已知錯誤的合約措辭
severity: minor
blocking: 否——不改產品行為，但會重新製造錯誤的權威合約。
引句:「Mock-DSP 合約措辭改窄那一條改回原句」
file: `src/rtb/dsp/server.py:107` 顯示另有 `void_operation` 寫入路由，`src/rtb/dsp/server.py:110` 才是會改廣告狀態的兩種動作。
file: `docs/rtb-production-agent-demo-knowledge/Systems/Mock-DSP.md:41` 現有合約仍寫成「每一種寫入動作」。
回退驗證器不會讓原本過大的合約重新變真；已確認的措辭訂正應獨立保留，不能作為功能回退的一部分反轉。

## 實務隱患逐類覆核

- 金流：無；驗證器不執行正式寫入，只讀檔案並跑測試。
- 對外送出：無；指定證據使用本機模擬服務，沒有正式外部端點。
- 不可逆：無；清單、工具及 CI 工作均可由版本控制回退。
- 守衛面：有；F2–F6 是範圍、證據、人工審查及格式守衛的假綠入口。
- 執行可靠性：有；F7–F8 會造成卡住或已知非產品紅燈。
- 刻意繞過：spec 已正確承認重貼雜湊、虛標 `covers` 與弱故障注入屬天花板；本報告沒有因此否定整體方案。

## 已讀、無 finding 的節

- 檔頭、PRIOR-ART、RETIRE-IF：已讀,無 finding。
- 使用者裁定：已讀,無 finding；F5 要求落實該裁定，並不要求驗證器採信 reviewer。
- 現況：已讀,無 finding；CI、mypy/ruff 範圍、DSP 路由與 pytest 9 JUnit 映射均已對碼核實。
- 審計修正紀錄：已讀,無 finding。
- 交叉引用：已核對交接文件第 15 節、Phase 11、F1–F7 節點、Mock-DSP、`CAMPAIGN_WRITE_ACTIONS`、`tools/mypy_sarif.py`、CI 與 `pyproject.toml` 均存在；S800–S809 是尚待實作的驗收測試識別，不按既有引用缺失計。

總結:最高嚴重度 major;blocking 5 條。
