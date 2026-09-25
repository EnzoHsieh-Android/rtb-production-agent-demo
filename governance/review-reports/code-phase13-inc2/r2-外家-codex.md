severity: major

c1 已驗:組完提示後會再查停止旗標，收到停止時不呼叫模型。  
c2 已驗:操作歷史的 `action` 先驗字串，逐欄檢查的型別錯誤會轉成不合格。  
c3 已驗:逐日成效有點擊多於曝光時，該段算出的變化值會寫成 `na`。

## 發現 1:理由可通過驗證，卻無法寫入調查紀錄
severity: major
blocking: 是

引句:「+_REJECTED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})」

新判準漏了代理字元類別 `Cs`。JSON 中的 `\ud800` 會被解析成孤立代理字元；`_plain_line` 放行後，理由進入調查紀錄的 SQLite `TEXT` 寫入，觸發 UTF-8 編碼錯誤。提交位於 `_from_ai` 的例外處理範圍之外，任務留在分析中。file: `src/rtb/analyzer/investigation.py:482`、`src/rtb/analyzer/task_store.py:747`、`src/rtb/analyzer/flow.py:329`

例子：假模型回合法 `propose` 與 base 引證，理由為 JSON 跳脫字串 `"\ud800"` → 預期當成選項外答案，退回程式規則並提交 → 實際通過回答驗證，提交時拋編碼錯誤。

重現：用假 `complete` 回傳上述 ASCII JSON，讓任務走到 `TaskStore.commit_step`；檢查 `parse_answer` 已接受理由，而調查紀錄未寫入。不需呼叫真模型。

## 發現 2:共用資料區會改寫部分合法廣告名稱
severity: minor
blocking: 否

引句:「+    return "".join(ch if ch.isprintable() else f"\\u{ord(ch):04x}"」

`\u` 只能表示四位十六進位碼；對不可列印的非 BMP 字元，這裡會寫出五位以上，JSON 讀回時只消耗前四位。調查提示因而不是原名稱的 JSON 字串。file: `src/rtb/domain/evidence.py:164`、`src/rtb/analyzer/investigation.py:421`

例子：名稱 `A`＋U+E0001＋`B` → 預期資料區的 JSON 字串讀回原名稱 → 實際 U+E0001 被寫成 `\ue0001`，讀回成 U+E000 加字元 `1`。

重現：以該名稱呼叫 `quoted_untrusted`，再用 `json.loads` 讀回並與原字串比較；也可從假 DSP 的廣告名稱走到 `inv.prompt` 檢查資料區。此項依程式碼推理，未在唯讀沙盒執行實驗。

2 條,blocking 1。