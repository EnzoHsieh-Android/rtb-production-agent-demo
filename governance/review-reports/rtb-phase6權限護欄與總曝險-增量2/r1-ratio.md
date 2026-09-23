severity: minor

# 鏡頭:比例上限的算法與整數邊界(增量 2)

範圍限定在「## 增量 2 設計:護欄表格」節內比例上限公式、整數邊界、與 [S402] 的驗證主張;其他節只作背景。

## F1 「現況 0」邊界例在真實 DSP 讀取路徑上不可達

severity: minor
blocking: 否 — 只影響一條範例說明能否對到全端測試,不影響護欄邏輯本身的正確性

引句:「例:現況 100 → 150 過、151 擋、149 過;現況 3 → 4 過(3 的五成取下限是 1)、5 擋;現況 1 → 2 過、3 擋;現況 0 → 1 過、2 擋;現況 100 → 1(減)過;暫停一律過。」

開程式核對:執行端讀 DSP 現況走的是 `DspClient.read_campaign`,裡面用 `_positive_int` 過濾回應欄位——`file: src/rtb/executor/dsp_client.py:31`「`return value if is_plain_int(value) and 0 < value <= _SQLITE_INTEGER_MAX else None`」——要求 `0 < value`。同一支檔第 51-55 行:budget 或 version 解析失敗(`_positive_int` 回 None)就直接丟 `DspUnavailable`,不會回傳 `CampaignView(budget=0, ...)`。也就是說,一個真的預算是 0 的廣告,在真實 HTTP 路徑上會被判成「讀不到 DSP」(執行端會 `_release` 放掉租約、記 `DSP_UNAVAILABLE`),永遠走不到 `precheck` 裡的比例檢查那一步——現況 0 這個邊界值,只有在測試用的替身 DSP(`tests/executor/fakes.py` 的 `FakeDsp` 直接建構 `CampaignView`,不經過 `_positive_int` 過濾)才能構造出來。

這不是這次設計新引入的問題(`_positive_int` 的 `0 < value` 門檻是既有程式碼,增量 2 沒有改它),公式本身在 budget=0 時算出 `max(0, 1) = 1` 沒有錯,而且 budget=1、2、3 的例子已經足夠展示「最小加額」那條分支,不靠 budget=0 這個例子撐場。純粹是這句話舉的範例暗示「現況 0」是護欄要在正式路徑上處理的一種現況,但實際上這個現況目前只能經替身 DSP 到達,寫全端邊界測試(S403「全部走真的處理一筆的路徑」)時如果照抄這個例子去接真的 DspClient 會做不出這個情境,只能用直接建構 `CampaignView` 的替身測。建議在設計裡補一句說明這一點,或把「現況 0」的例子改成只用來說明公式本身、不當成 S403 全端邊界測試的一列。

## 確認沒問題的觀察

「版本已變排在它前面,所以走到比例這一項時,現況預算一定就是分析端當初觀察到的那個版本的預算,比例的基準不會錯」這句話開程式核對後成立:DSP 端唯一改預算或狀態的路徑是 `src/rtb/dsp/store.py` 的 `_next_state`(第 189-196 行),兩種動作(改預算、暫停)都在同一列把 `version` 加 1,而且整支模組只有一處 `UPDATE campaigns` 陳述式(`store.py:419`)套用這個結果——預算與版本永遠一起變,不存在「預算變了版本沒變」或反過來的路徑。執行端三條會呼叫 `precheck` 的路徑(`src/rtb/executor/execution.py:362` 處理一筆、`:496` 憑證過期後重讀、`:654` 對帳查不到重跑)都是先用同一次 `dsp.read_campaign` 讀到的同一個 `view` 物件,同時判版本與(將來)判比例,所以版本核對通過時,拿來算比例基準的 `view.budget` 保證是那個版本當下的真預算,不會被分析行程宣稱的舊基準帶偏,也不會被另一個工作者在讀完之後才做的修改污染(那種情況版本會不合,直接在比例檢查之前就被 `version_changed` 擋下)。

整數邊界與型別:執行端讀到的 `CampaignView.budget`(`src/rtb/executor/dsp_client.py:51-55`)、DSP 內部的 `Campaign.budget`(`src/rtb/dsp/store.py:72`)與提案的 `new_budget`(`src/rtb/domain/proposal.py` 的 `_positive_int`)三處都嚴格限制在 `1` 到 `2**63-1`(三處常數字面重複但數值一致,分別在 `dsp_client.py:27`、`proposal.py:29`、`dsp/store.py:51`)之間的純整數(`is_plain_int`,排除布林與浮點),不會出現字串或浮點型別混進來的情況。用 Python 實測(腳本見下)確認:設計文字要求的「用整數運算(分子、分母),不用浮點」如果照做——即 `max((current_budget * 1) // 2, 1)`——在 0、1、2、3、100 這些小數字與逼近 `2**63-1`(含 `2**62`、`2**53` 前後、`MAX_INT-1`、`MAX_INT//2` 等)的大數字上都不會溢位(Python 整數本身無位寬上限),也不會因為浮點精度流失而算錯;真正的浮點風險(`current_budget × 0.5` 對超過 2**53 的整數會失真)已被設計文字明確排除,不是這次審查放過的洞。

[S402] 的主張比它自己宣稱的驗證範圍更站得住:分析端現行規則(`src/rtb/analyzer/policy.py:81`,`new_budget = min(max(round(budget * 1.1), budget + 1), MAX_INT)`)產生的加量,經腳本對 0–2000 逐一整數掃過、外加 10**6 到 10**18、`MAX_INT`、`MAX_INT-1`、`MAX_INT//2` 附近與 `2**53` 附近共 2016 個取樣點驗證,沒有一個超過比例上限(1/2、最小加額 1)——包括 `budget` 逼近 `MAX_INT` 時 `min(..., MAX_INT)` 把新預算截斷、實際加量變成很小甚至 0 的邊角情況也不會踩線。原因是分析端固定只加一成、且最小加額同為 1,兩邊在小預算(1、2、3)時剛好卡在同一個邊界(都是「剛好等於上限應通過」),大預算時一成遠低於五成、且截斷只會讓加量更小,不會更大。所以 [S402] 現況描述「在 0 到 1000 以及幾個大額預算下全部通過」這句話為真,而且比它字面宣稱的還更普遍地為真,不是抽樣運氣。

沒有找到會讓比例上限公式做出錯誤放行或錯誤擋下的邊界洞;唯一的落差是 F1 描述的「現況 0」範例與真實 DSP 讀取路徑的落差,屬措辭精度問題。
