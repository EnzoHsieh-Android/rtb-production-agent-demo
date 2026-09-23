severity: major

## F1 副作用核對兩條指標需要「窗內全部 DSP 寫入」的列舉,DSP 對外只有單鍵/單廣告查詢,沒有這種讀法——會被迫在維運套件裡開第二條讀 DSP 的路

severity: major
blocking: 是 — 照字面實作做不出來;唯一能跑通的兩條路都是增量 1 沒有的第二種做法(繞開 HTTP 唯讀端點直開 DSP 的資料庫檔,或者另開一個 DSP 沒有設計過的列舉端點),違反本次判準「引入第二種做法或跨層直呼才算 major」。

引句:「有效事件:窗內 DSP 提交的寫入中,冪等鍵是執行端鍵格式的(依 DSP 提交時間歸窗;別的寫入者直接改 DSP 不是本系統的副作用,不算)」([S652] 未授權或違反護欄的副作用率)。

引句:「壞事件:同一份提案內容(任務、修訂、內容雜湊)在 DSP 有第二筆以上提交的寫入(依第二筆的提交時間歸窗)」([S653] 重複有害副作用率)。

這兩條的母體都要求「掃出窗內全部 DSP 寫入」,但增量 1 已經把 DSP 的存取方式定死成——只用已知的冪等鍵去讀一個唯讀端點:「DSP:用冪等鍵讀唯讀端點,留下操作編號與提交時間(執行端現有的 DSP 用戶端讀操作紀錄時會丟掉這兩欄,追蹤檢視另用共用 HTTP 用戶端讀同一個唯讀端點)」(增量 1 追蹤檢視節)。這個模式要求「先有鍵才能查」,不能反過來「給一段時間、列出這段時間內所有鍵」。

查證(file: `/Users/enzo/rtb-3b/src/rtb/dsp/server.py:80-88`)——DSP 的 HTTP 路由只有四種讀法:`GET /campaigns/{id}`、`GET /campaigns/{id}/history`(依廣告)、`GET /campaigns/{id}/metrics`、`GET /operations/{key}`(依冪等鍵)。沒有任何「依時間窗列出全部寫入」或「列出全部廣告」的端點,`store.history()` 與 `store.get_operation_by_key()` 也是同樣的介面(file: `/Users/enzo/rtb-3b/src/rtb/dsp/store.py:321-329`)。執行端本來就不預先知道「這段時間內 DSP 動過哪些廣告或哪些鍵」的完整清單(那清單只存在 DSP 自己的資料庫裡),所以副作用核對函式要拿到「窗內 DSP 提交的寫入」這個母體,唯一能跑通的路只剩:(a) 讓維運套件直接開 DSP 的 SQLite 檔讀,或 (b) 另外幫 DSP 開一個列舉端點——這兩條在設計裡都沒有出現,而且都是增量 1 明確排除的第二種存取方式:增量 1 的追蹤檢視小節開頭就把 DSP 定位成只能透過「共用 HTTP 用戶端」打唯讀端點,執行端、分析端、維運套件三邊本來就不准碰 DSP 的資料庫檔(DSP 是獨立行程,只用 `CampaignStore` 開自己的檔,見 `/Users/enzo/rtb-3b/src/rtb/dsp/server.py:107-113`)。

同一個問題也連帶讓「DSP 提交當下記下的政策版本(憑證帶的)跟提案自己的政策版本不同」這一項核對落空:即使給了冪等鍵,`GET /operations/{key}` 回的 `OperationResult` 也沒有 `policy_version` 欄位(只有 `Operation` 這個輸入端資料類有;`OperationResult`/`HistoryEntry` 都沒有回這個欄位——file: `/Users/enzo/rtb-3b/src/rtb/dsp/store.py:88-125`),核對函式讀不到 DSP 側記下的政策版本可比對。這不是「措辭不精確」,是字面實作會直接卡死或被迫開後門。

設計說「(對照增量 1 追蹤檢視讀 DSP 的做法)」的兩個地方(追蹤檢視按鍵查、副作用核對按窗掃)其實需要的是兩種不同形狀的讀法,增量 3 沒有在合約或最小設計裡交代新讀法怎麼落地,也沒有另開一條「跟 HTTP 唯讀端點對齊」的路——實作者只能自己選一種,選哪種都會撞出一個增量 1 沒批准過的存取層。

---

以下確認沒有問題:

燒損評估器與服務水準設定表放進維運套件、只呼叫增量 2 已在讀取白名單裡的窗內統計函式,沒有另開一條讀分析端/執行端資料的路;這跟增量 1 定的「維運套件只准呼叫白名單裡的讀取函式、白名單裡每一支都是機械判定的非寫入函式」([S617])是同一套機制,沒有引入第二種讀法。

「開始時已用額度」補進嘗試紀錄第一列,設計明寫「舊資料庫開啟時照既有補欄位做法補上,舊列為空」——查證 `/Users/enzo/rtb-3b/src/rtb/executor/attempt_store.py:64-70` 的 `ADDED_COLUMNS` 元組與 `/Users/enzo/rtb-3b/src/rtb/executor/inbox_store.py:441-478` 的 `_missing_columns`/`_migrate_columns`(連線時檢查、缺才在拿到寫入鎖的交易內逐欄 `ALTER TABLE ADD COLUMN`),增量 3 這一欄照同一個既有機制加,不是另開一套遷移邏輯。

設定表(目標值、門檻、窗長、縮短倍數)集中成「程式常數」的做法跟既有 `src/rtb/executor/guardrails.py` 的慣例一致——該檔本身就是「只放常數與純判斷函式,不碰 DSP、資料庫與設定檔,給多個呼叫端共用一份」(file: `/Users/enzo/rtb-3b/src/rtb/executor/guardrails.py:1-19`);增量 3 把設定表放維運套件而非 guardrails.py,是因為這些常數只給燒損評估器用、guardrails.py 明寫「不碰…設定檔」且屬於執行端模組,放維運套件不算引入新做法,是延續同一種「常數表 + 純函式」風格換了個屬主模組。

命令列入口「比照增量 2 的命令列入口」,增量 2 本身又是「比照既有命令列入口寫法」——查證 `def main(argv...)` 的既有慣例散布在 `/Users/enzo/rtb-3b/src/rtb/dsp/server.py`、`/Users/enzo/rtb-3b/src/rtb/executor/approve.py`、`replay.py`、`inbox_server.py`、`runner.py`,增量 3 延續同一種入口寫法,沒有另開一種命令列框架。

嘗試紀錄新欄位、設定表常數化、命令列入口三處都是延續既有做法,沒有發現第二種做法或跨層直呼;唯一的架構對齊問題集中在 F1(副作用核對讀 DSP 的方式),已在上方詳列。
