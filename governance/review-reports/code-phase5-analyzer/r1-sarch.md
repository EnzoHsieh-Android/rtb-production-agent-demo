severity: major

### 1. `_matches` 對帳只有一個欄位被測試守住,另外三個欄位拿掉防護後測試仍全綠

severity: major
blocking: 是 major 以上一律是
引句:「record.action == proposal.action_type.value」

`flow._matches`(新增函式,S311「查得到、內容不符 → 轉擋下,記冪等衝突」的核對邏輯)比對四個欄位:`campaign_id`、`action`、`new_budget`、`expected_version`。這四個欄位的核對正是第 2 輪外家席特別點名要補的防線(design 原文:「查到不等於是這份提案寫的」)。

實際跑變異測試:把 `_matches`裡 `campaign_id`、`action`、`expected_version` 三個欄位的比對個別拿掉(單獨拿掉,不是全拿掉),`tests/analyzer/` 全部 231 支測試仍然全數通過;只有拿掉 `new_budget` 那一個比對會讓 `test_after_the_retention_window_the_dsp_decides_completion` 翻紅。也就是說,若這三個欄位的核對將來被誤刪、打錯欄位名、或改壞條件式,整包測試(含作者聲稱做過的十三道變異)不會有任何一支變紅——這正是判準裡「測試在被守的程式拿掉後仍然綠(假綠)」的 major 情況。

對應到影響:`_matches` 是 F4 收尾階段判斷「DSP 這筆操作紀錄是不是這份提案寫的」的唯一防線(冪等鍵本身是 SHA-256 雜湊,只要 DSP 對同一把鍵回錯資料——伺服器端邏輯錯誤、未來重構、或任何非雜湊碰撞成因——這四個欄位就是最後一道核對)。目前只有 `new_budget` 這一項真正被守住,`campaign_id`/`action`/`expected_version` 三項是名義上的防線、實際上沒有任何測試在替它們把關。

file: `tests/analyzer/test_replan.py:54-63`(`dsp_operation()` 輔助函式只在 `test_after_the_retention_window_the_dsp_decides_completion` 裡被 override 過 `new_budget`,全檔沒有任何一處 override `campaign_id`、`action`、`expected_version` 來測不符的情況)
file: `src/rtb/analyzer/flow.py:357-362`(`_matches` 定義)

### 2. DSP 操作紀錄的數字欄位核對比執行端寬,允許 0(執行端要求 `> 0`)

severity: minor
blocking: 否 minor
引句:「return value is None or _is_int_between(0, value)」

`dsp_client._int_or_none`(給 `make_operation_lookup` 核對 `new_budget`/`expected_version` 用)接受 `0` 為合法值(`_is_int_between(0, value)` 下界是 0);而執行端對同一批 DSP 回應欄位的核對用的是 `_positive_int`,下界是「大於 0」(`src/rtb/executor/dsp_client.py:30-31`:`0 < value <= _SQLITE_INTEGER_MAX`)。DSP 存入 `expected_version` 時本身就要求 `0 < expected_version`(`src/rtb/dsp/store.py:149`:`0 < expected_version <= SQLITE_INTEGER_MAX`),所以正常情況下 DSP 不會真的回 0,這條差異目前不會被觸發成真正的錯誤行為;但這是「分析端與執行端各一份的操作核對」這個既有慣例裡,兩份核對用不一樣的下界寫法,屬於架構對齊角度的風格不一致,不到會做錯事的程度,列為 minor。

file: `src/rtb/executor/dsp_client.py:30-31`
