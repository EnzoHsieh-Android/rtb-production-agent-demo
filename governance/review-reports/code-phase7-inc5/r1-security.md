severity: minor

# 資安審查:rtb-3b code-phase7-inc5(收件口拒收紀錄)

被審:`governance/review-reports/code-phase7-inc5/r1-snapshot.patch`(repo `/Users/enzo/rtb-3b`)。
只改 `src/rtb/executor/inbox_server.py`(新增測試檔不算攻擊面)。角色設定:攻擊者是被劫持的分析
行程,能送任意本文與任意標頭給收件口;目標是讓自己能控制的字串出現在
`invalid_proposal errors=...` 這一行紀錄裡(換行偽造第二行、終端控制碼、或單純洩漏鍵名/內容)。

## 逐類檢查

### 1 不可信輸入流到危險操作(紀錄注入)—— 已看,主結論見下方 F1/F2,注入本身查無漏洞

先把「攻擊者能控制、又會被寫進紀錄」的來源全部列出來,對照這次改動怎麼處理:

- **`parse_proposal` 的錯誤字串**(`src/rtb/domain/proposal.py`):只有三種帶攻擊者可控尾巴——
  `unknown_field:{鍵名}`(鍵名可以是攻擊者放進 JSON 的任意字串,含換行、ANSI 控制碼)、
  `too_large:{位元組數}`、`unexpected_failure:{例外型別名}`。其餘全是固定字面量,或
  `f"{field}:missing"` / `f"{field}:invalid"` ——`field` 一定來自 `CHECKS` 這個寫死的白名單鍵
  (`task_id`、`revision`…),不是從攻擊者的 JSON 鍵取出來的,所以就算攻擊者塞
  `{"revision": -1, "邪惡\n偽造行": 1}`,`revision:invalid` 這行的 `revision` 也不會變成攻擊者的字串。
- **`_TAILED_ERRORS` 的處理**(inbox_server.py 新增):
  `classes = sorted({error.split(":", 1)[0] if error.split(":", 1)[0] in _TAILED_ERRORS else error for error in errors})`
  ——只要冒號前綴命中 `{unknown_field, too_large, unexpected_failure}` 這三個,不管冒號後面是什麼
  (含換行、控制碼、超長字串),整段一律砍到只剩前綴本身。用鍵名裡帶冒號去繞
  (例如鍵名 `"a:b\nfake_line"`)也一樣:`split(":", 1)[0]` 只取到 `unknown_field`,後面連同冒號
  一起被丟掉,不會漏出鍵名任何一個字元。
- **`read_json` 的拒收代碼**(`src/rtb/httpkit.py`):`chunked_not_supported`、`invalid_content_length`、
  `body_too_large`、`invalid_json`、`incomplete_body`、`request_timeout` 全是常數字面量,跟請求內容
  無關;`_parse_or_reject` 把這些代碼直接當「錯誤類別」記錄(`rejection.code`),沒有任何攻擊者輸入
  進得去。
- 最後 `','.join(classes)` 组出來的字串,元素只可能是:固定字面量、`CHECKS` 的固定欄位名、或上述
  三個被砍到只剩前綴的詞——沒有一條路徑能讓原始鍵名、`risk_summary`、標頭值等攻擊者內容原封不動
  地流進這一行。結論:**換行注入、偽造第二行、終端控制碼這三種都查無漏洞,設計目標「不含攻擊者
  給的鍵名與任何請求內容」在這份 diff 裡有守住**。
- 沒被記錄的拒收路徑(Origin 檢查、Content-Type 檢查、404、X-Fault 相關)也如預期不進
  `_parse_or_reject` 的 try 範圍,對應到新測試 `test_requests_that_are_not_a_proposal_body_are_not_logged`,行為一致。

以下兩點是本次唯一能站得住的發現,都是「這個機制本身沒有節流/併發保護」的設計層面觀察,不是能
直接取得資料或執行力的漏洞。

## F1 拒收紀錄沒有節流,垃圾請求可以每次一行把標準錯誤/後續日誌系統灌爆

severity: minor
blocking: 否 — 純可用性面的資源耗用推論,沒有機密性或完整性影響,且與現有威脅模型「防忘記,不防刻意繞過」不衝突,不擋這次 merge
引句:「sys.stderr.write(f"invalid_proposal errors={','.join(classes)}\n")」

攻擊路徑四件:
- 入口:`POST /proposals`,不需要任何驗證即可打到(收件口設計上本來就對外開放給分析行程)。
- 動作:攻擊者(被劫持的分析行程)持續送任何格式不合法的本文,例如最短的 `b"{not json"`。
- 利用點:每一次拒收固定觸發一次 `sys.stderr.write`,程式碼裡沒有任何速率限制、去重視窗或退避,
  只受 `KitServer` 既有的 `MAX_CONNECTIONS=64` 併發上限與 socket/逾時限制節流,長時間持續送仍可累積
  大量行數。
- 影響:標準錯誤如果被導向檔案或日誌收集系統,長時間灌爆會佔用磁碟或拖慢下游處理,屬於可用性
  (DoS)面的資源耗用;不影響機密性(每行內容仍是固定詞彙,不洩漏內容),也不是本次「紀錄注入」
  目標要防的東西,所以只標 minor、不擋。這點文件裡也自陳「持久化與告警留給 Phase 9 可觀測性」,
  屬於已知未完成範圍,若之後要處理建議在 Phase 9 一併做速率限制或彙總。

## F2 多執行緒同時拒收時,寫入標準錯誤沒有鎖,行與行之間理論上可能交錯

severity: minor
blocking: 否 — 推論;交錯只會混雜固定詞彙(仍不含攻擊者內容),不構成資訊洩漏或偽造任意內容,只影響事後人工/工具解析紀錄行的可讀性
引句:「def _log_invalid_proposal(errors: Sequence[str]) -> None:」

攻擊路徑四件:
- 入口:同一台收件口在 `ThreadingHTTPServer`(`KitServer`)下,每個連線各自一條執行緒處理請求。
- 動作:攻擊者用多條連線同時送不合法本文(收件口本身允許到 `MAX_CONNECTIONS=64` 併發)。
- 利用點:`_log_invalid_proposal` 對 `sys.stderr` 的 `write` 呼叫沒有鎖或序列化機制,CPython 的
  `write` 對單一字串通常是一次系統呼叫、在多數平台上短字串會原子寫入,但這不是語言層保證,理論上
  仍可能在極端情況下交錯。
- 影響:即使交錯,兩邊寫入的內容仍各自只由固定詞彙組成(見 F1 之上分析),所以交錯出來的最壞結果
  是「一行看起來像兩個固定詞彙黏在一起」,不會產生攻擊者可控的新內容,也就不構成資安意義上的偽造
  第二行或終端控制碼注入,純粹是紀錄可讀性/可解析性的邊角案例,故只列為推論、不擋。

## 2 權限與範圍

已看,無。這份 diff 沒有新增任何權限檢查點、路由或跨租戶存取;`_parse_or_reject` 只是把既有的
`parse_proposal` / `read_json` 呼叫包一層紀錄,拒收後的狀態碼與行為(`RequestRejected` 照樣往上拋)
與改動前完全一致,收件口本來就「不做政策與租戶檢查」是既定設計,不在這次改動範圍內變動。

## 3 密鑰與個資(R15/R16)

已看,無。整份 diff 沒有讀取或處理任何秘密(API key、token、憑證);寫進 `stderr` 的內容如上分析
只會是固定詞彙或白名單欄位名,不含請求本文、不含標頭值、不含攻擊者鍵名——符合 python-idioms R15/
R16「秘密與不可信內容不進 log」的要求,而且是專門為了滿足這條而新增的機制。

## 4 加密與隨機數

已看,無。這份 diff 沒有觸碰任何雜湊、簽章、隨機數或金鑰產生邏輯(`content_hash` 等既有函式未變動)。

## 5 執行邊界

已看,無。新增程式碼只是字串處理(`split`、`sorted`、`join`)加上一次 `sys.stderr.write`,沒有
`eval`/`exec`/子行程呼叫/反序列化,也沒有引入新的反序列化路徑;JSON 解析仍是既有的 `json.loads`
邊界,未變動。`_parse_or_reject` 捕捉的例外型別限定在 `RequestRejected`,不會意外吞掉其他例外或
改變既有的例外傳遞邊界(`parse_proposal` 本身仍保證不丟例外)。

## 6 行動端

已看,無。此變動屬於後端 Python 收件口伺服器,與行動端無關。

## 小結

主要威脅(讓攻擊者控制的字串出現在拒收紀錄這一行)在這份 diff 裡有被正確擋下:三種帶尾巴的錯誤
(`unknown_field` / `too_large` / `unexpected_failure`)一律被砍到只剩固定前綴,其餘錯誤字串的可變
部份永遠來自寫死的欄位白名單,不是攻擊者輸入本身,經過對 `冒號前綴 in _TAILED_ERRORS` 分支邏輯與
攻擊者鍵名帶冒號/換行等繞法逐一推演都查無漏洞。剩下兩點(F1 紀錄無節流可能被灌爆、F2 併發寫入理論
上可能交錯)都只是可用性/可讀性層面的次要觀察,不影響機密性也不構成注入,标為 minor、blocking 否。
