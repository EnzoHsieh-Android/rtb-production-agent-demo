severity: major
以下為完整報告全文(已依 `p9i4-fmt.md` 格式撰寫):

---

severity: major

# 審查報告(架構對齊,第 2 輪修正 r3-delta.patch)

### 1. write_scan.py 檔首拿掉「Phase 8 死信守衛」這個仍在用的直接消費者,說明跟真正呼叫方對不上
severity: major
blocking: 是

引句:「以及把拆開的字串拼回來(稽核表守衛共用)。」

觸發情境:修正前 `write_scan.py` 檔首明講「拼回字串」這個讀法是「Phase 8 死信守衛與 Phase 9 增量 4 稽核表守衛共用」,兩個具名消費者都列出來。本輪把稽核判定搬進新開的 `tests/executor/audit_guard.py` 後,順手把檔首這句改成只剩「(稽核表守衛共用)」,拿掉了「Phase 8 死信守衛」。但 `tests/executor/test_dead_letter.py` 並沒有停止直接匯入這個讀法:它仍然 `from tests.executor.write_scan import reconstructed_strings`(`tests/executor/test_dead_letter.py:34`),而且真的拿來用在兩處跟稽核判定完全無關的地方——事故 F6「只有重放方法能把死信改回來」的機械守衛 `_functions_containing`(`tests/executor/test_dead_letter.py:455`),以及驗證共用掃描器本身「拆開字串拼得回來」的 `test_the_source_checks_see_through_split_strings`(`tests/executor/test_dead_letter.py:496`)。

會出的錯:檔首現在宣稱這個讀法只服務「稽核表守衛」,但實際上死信測試檔仍是直接、不經 `audit_guard.py` 轉手的獨立消費者。往後有人想動 `text_of`/`reconstructed_strings` 的行為(例如再補一種拼法、改空白處理規則),照檔首說明只會盯著 `audit_guard.py`/`test_audit_tables.py` 這條線,看不出 `test_dead_letter.py` 也直接掛在同一支函式上,容易漏測就把死信那兩個獨立測試改壞。這正是第 2 輪發現 1 指出過的同一種病(檔首宣告跟實際消費者對不上)——這次修正在把稽核政策搬家的同時,又在檔首上造出一個方向相反的版本。

建議修法:把 `write_scan.py` 檔首「拼回字串」那句的消費者名單改回列出兩個獨立呼叫方,例如「稽核表守衛(經 `audit_guard.py`)與 Phase 8 死信守衛測試(直接匯入)共用」,不要只寫「稽核表守衛共用」。

### 2. 稽核表只增不改守衛.md 改寫「代碼審第 1 輪」舊條目,跟新增的「第 2 輪」條目對同一件事講出兩個矛盾版本
severity: major
blocking: 是

引句:「判定本身放在同目錄的稽核守衛模組」

觸發情境:本輪在 `docs/rtb-production-agent-demo-knowledge/Systems/稽核表只增不改守衛.md` 動了兩處:(a) 把「代碼審第 1 輪」那條舊條目結尾從「判定本身搬進共用掃描模組,兩支守衛測試都從那裡匯入」改成「判定本身放在同目錄的稽核守衛模組(`tests/executor/audit_guard.py`),兩支守衛測試都從那裡匯入」(`docs/rtb-production-agent-demo-knowledge/Systems/稽核表只增不改守衛.md:31`);(b) 新增「代碼審第 2 輪」條目,結尾寫「稽核判定從共用掃描模組搬到稽核守衛模組,共用掃描模組只留通用讀法」(`docs/rtb-production-agent-demo-knowledge/Systems/稽核表只增不改守衛.md:32`)。

會出的錯:這兩句緊鄰、講的是同一件事(稽核判定現在放在哪裡),卻互相打臉。(b) 明講「從共用掃描模組搬到稽核守衛模組」是這一輪(第 2 輪)才做的事,言下之意第 1 輪時判定還在共用掃描模組——這也符合事實(`write_scan.py` 舊版確實把 `audit_violations` 等放在自己身上,r3-delta.patch 對 `write_scan.py` 的整段刪除可證);但 (a) 把第 1 輪那條舊條目直接改寫成「判定本身放在同目錄的稽核守衛模組」,讓讀者以為第 1 輪當時就已經是這個結構。CLAUDE.md 明講「同一篇筆記內部也會新舊打架」是 doctor 驗不出的坑,這正是新造出一個這樣的坑:往後有人只讀這篇筆記想搞懂「audit_guard.py 是什麼時候出現的」,兩條紀錄給出矛盾答案;而且 (a) 被改寫後不再忠實記錄「第 1 輪當時真的把判定放錯地方、第 2 輪才修正」這件事,等於把第 2 輪發現 1(判定不該待在共用掃描模組)這個修正的歷史脈絡抹掉了——往後想查「這個架構決策是哪一輪、為什麼改」會被誤導。

建議修法:把 (a) 那句改回只描述第 1 輪當時實際做的事(判定放進共用掃描模組),不要提前寫成本輪才有的落腳點;第 2 輪的落腳點變化只在 (b) 那條新條目講清楚即可,不要回頭覆寫舊條目的歷史紀錄。
