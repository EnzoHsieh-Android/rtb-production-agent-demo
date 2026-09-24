severity: minor

# 共用 HTTP 回歸席(a90d48d httpkit)第 3 輪報告

## 結論先講

- **既有呼叫端沒被改壞。** DSP 與收件口的新舊兩版(cd24279 對 7106f29)都用正式入口起成子行程,各打同一組 31 種原始請求(DSP 與收件口各 31 種,共 62 對比對)。狀態行、所有標頭、本文逐位元組相同(只把 Date 標頭遮掉)。唯一的差別是 DSP 成功寫入回應裡的 committed_at 時間戳,那是正常的時間差。
- **新功能的主路徑都成立。** 回應物件(HTML、樣式表、303)正確;內容安全政策標頭只出現在有宣告的伺服器;錯誤頁都不洩漏內部細節;讀表單的行為符合合約。
- **有 3 條 minor,都是潛在問題。** 目前唯一的呼叫端是測試,2b 的展示伺服器還沒寫出來,所以現在觸發不了。建議在 2b 動工前補上。
- **變異測試:16 道抓到 13 道。** 沒抓到的 3 道見第 3 條。

## 我查了什麼

**新舊比對涵蓋的請求:**
- 正常讀取、404
- 主機檢查:非本機主機、大寫 LOCALHOST、HTTP/1.0 沒帶 Host、HTTP/1.1 沒帶 Host、Host 重複
- 分塊傳送 → 411
- 長度:70000 → 413;非數字、負數、11 位數、重複的 Content-Length
- JSON:壞 JSON、非 UTF-8、陣列、空本文
- 方法與請求行:PUT/HEAD → 501、請求行亂碼、HTTP/9.9、網址過長 → 414
- 故障注入沒開卻帶 X-Fault
- 送表單類型給 JSON 端點 → 415
- 收件口帶 Origin → 403
- 用真簽的能力憑證做一次有效的 DSP 寫入
- 稽核金鑰讀取
- 宣告 10 位元組只送 3 位元組 → 408(socket 逾時設 1 秒)

**合約逐條對:**
- **S1051:** 讀表單走同一支 `_read_body`,分塊、長度、上限都共用,重複欄位回 400。實測百分號解碼後才撞名的也會拒,例如 `a=1&%61=2`,以及中文百分號編碼對上原始 UTF-8 位元組。
- **S1062、S1063 的外殼那半:** `Response.css` 回 `text/css; charset=utf-8`;`Response.redirect` 回 303、Location 照給、本文為空。這兩條合約指定的測試名稱(`test_the_stylesheet_is_served_as_css`、`test_every_post_redirects_to_the_current_node`)屬於增量 2b,現在還不存在,這一輪算正常。
- **httpkit 既有規則:**
  - 只綁 127.0.0.1、主機檢查不分大小寫、HTTP/1.0 沒帶 Host 的放行邏輯、require_host、請求總期限與連線上限:本次都沒改動。
  - 未預期例外回 500 不洩漏細節:主路徑成立,例外情況見第 1 條。

**讀表單實測:**
- `+` 會轉成空白;`%2B` 解成 `+`
- `a=&b` 保留空欄位;`=x` 收成空名稱
- `;` 不當分隔符
- `%ED%A0%80`(UTF-16 代理字元)和本文 `\xff` 都回 400
- `%ZZ` 原樣保留,不報錯
- 類型大小寫不分;帶 charset 參數照樣收;多部分表單回 415;Content-Type 重複回 400;分塊傳送回 411

---

## 1. 轉址與額外標頭不驗證就寫出:可注入換行、可導向外站,非 Latin-1 值會疊出兩個狀態行

severity: minor
blocking: 否
引句:「return cls(303, "text/plain; charset=utf-8", b"", (("Location", location),))」
引句:「self.send_header(name, value)」

**問題:**
- Python 3.14 的 `send_header` 不擋 CR/LF。`Response.redirect` 和 `headers` 收任何字串就直接寫出。
- `send()` 先呼叫 `send_response`,再逐條加標頭。如果中途丟出的例外不是 OSError,已經放進緩衝的第一份標頭不會清掉。接著走到 `_reply_unexpected`,500 會接在同一個緩衝後面送出。
- 結果客戶端看到的是 303(或 200),不是 500。這違反共用行程基礎筆記的規則「沒預期到的例外一律回 500」。舊的 JSON 路徑不會這樣,因為 `json.dumps` 在 `send_response` 之前就做完了。

**重現:** 腳本是 `/tmp/p12i1r3-http/lab/newfeat.py`,在 HTML 伺服器上測:
- `Response.redirect("/#current\r\nSet-Cookie: pwn=1")` → 回應裡多出一行獨立的 `Set-Cookie: pwn=1` 標頭。只用 `\n` 也一樣。
- `Response.redirect("//evil.example/")` → 照送 `Location: //evil.example/`,瀏覽器會跳到外站。
- `Response.redirect("/#現在")` → 送出的是 `HTTP/1.0 303 See Other`、`Content-Length: 0`,後面直接接一整份 `HTTP/1.0 500 ...`。客戶端解析的結果是 303。
- 本文誤給 str 時,客戶端收到 200 OK,本文是 500 回應的前 13 個位元組。這一種 CI 的 mypy 嚴格模式抓得到;非 Latin-1 和換行的 Location 抓不到。

**為什麼是 minor:** 設計上唯一的轉址目標是常數 `/#current`(S1063)。目前沒有呼叫端會把不可信的值傳進來。

**建議:**
- `redirect` 只收以單一 `/` 開頭、不含 `//` 和控制字元的相對路徑;或者乾脆只提供 `/#current` 這一個常數。
- `send()` 在呼叫 `send_response` 之前,先把所有標頭值做 Latin-1 編碼並擋掉 CR/LF。本文型別放在 `Response.__post_init__` 驗。
- 補一條測試:非法 Location 要回 500 HTML。

## 2. 內容安全政策標頭只看 text/html 字首:大小寫不同、SVG、XHTML 都會漏掉

severity: minor
blocking: 否
引句:「and response.content_type.startswith("text/html")):」

**問題:** 設計說「宣告 HTML 的伺服器,HTML 回應與錯誤頁一律帶內容安全政策標頭」。S1023 也要求每個 HTML 回應都帶不准腳本的政策。但判斷只比對大小寫敏感的 `text/html` 字首。

**重現:** 在宣告了政策的伺服器上,直接建構以下三種回應,送出時都沒有 `Content-Security-Policy` 標頭:
- `Response(200, "Text/HTML", ...)`
- `Response(200, "application/xhtml+xml", ...)`
- `Response(200, "image/svg+xml", b"<svg><script>alert(1)</script></svg>")`

**為什麼是 minor:** 2a 的流程圖是內嵌在頁面裡的 SVG,不會單獨送出,所以現在觸發不了。

**建議:** 宣告了政策的伺服器,每個回應都帶政策標頭(樣式表、303 帶了也無害),不要依內容類型判斷。這樣也順便關掉「以後有人單獨送出 SVG」這條路。

## 3. 變異測試:空欄位、+ 號轉空白、錯誤頁逸出三處沒有測試守住

severity: minor
blocking: 否
引句:「pairs = parse_qsl(raw.decode("utf-8"), keep_blank_values=True, errors="strict")」

**重現:** 複本 `/tmp/p12i1r3-http/mut/tree` 放 16 道變異,腳本是 `/tmp/p12i1r3-http/mut/run.py`,跑 tests/kit、tests/dsp/test_server.py、tests/executor/test_inbox_server.py、tests/httpclient:

| 結果 | 變異 |
|---|---|
| 抓到 13 道 | 拿掉內容安全政策標頭、HTML 伺服器錯誤頁改回 JSON、自己的例外對應改回 JSON、不拒重複欄位、百分號解碼改寬鬆、本文改用 latin-1 解碼、不檢查表單類型、303 改 302、表單不設長度上限、JSON 伺服器也帶政策標頭、錯誤頁洩漏例外內容、樣式表內容類型錯、額外標頭沒送 |
| **沒抓到 3 道** | `keep_blank_values=False`(247 項全過)、改用 `unquote`,`+` 不再轉成空白(247 項全過)、錯誤頁拿掉 `html.escape`(247 項全過) |

跑完已確認 httpkit.py 還原成原檔。

**影響:**
- 空欄位:設計的確認頁是逐項勾選。空欄位如果被默默丟掉,「欄位存在但是空的」會變成「欄位不存在」。
- `+` 號:瀏覽器送表單時空白一律寫成 `+`。這一條壞掉的話,帶空白的欄位值會被解錯,但沒有測試會紅。

**建議:** 在讀表單的測試裡補三個案例:`a=&b=`、`a+b=c+d`、一個錯誤代碼含 `<` 的處理器拒絕。

---

**收尾 ps 檢查:** 我起的 DSP 和收件口子行程(`PYTHONPATH=/tmp/p12i1r3-http/...`)都已經 terminate 並 wait 收掉。用 `ps -ef | grep` 查,沒有 `p12i1r3-http`、`p12http`、`newfeat`、`diff.py`、`mut/run.py` 的殘留。系統上另外有 4 支 `rtb.dsp.server --db /tmp/audit-h-copy*/…`,是週二下午啟動的,不是本席起的,我沒有碰。

3 條,blocking 0。
