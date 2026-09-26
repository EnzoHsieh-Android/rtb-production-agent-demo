severity: minor

## 發現 1：既存 UTC 日期鍵日桶沒有套用新的每日上限
severity: minor
blocking: 否

引句:「    """日桶與樣板的五欄:同 _figures,另加每天的上限(DAILY_MAX_COUNT、DAILY_MAX_CENTS),保證任何」

新上限只用於種子寫入；舊相對日數資料遷移時也會套用。但資料庫若已是前一版的 `day_utc`／整數分格式，開庫會直接跳過遷移，既存超額日桶仍在。七天各存 `MAX_CENTS` 的合法舊值時，7d 回應仍超過分析端白名單。file: `src/rtb/dsp/store.py:487`、`src/rtb/dsp/store.py:696`。

以記憶體 SQLite 放入七筆前一版可存的日桶，再開遷移與讀取路徑，輸出：

```text
seven_day_spend 69999999999999.93
reader_accepts False
```

這使第 2 輪「每天可存，七日卻 invalid」的修復對既存日期鍵資料不完整；影響限於舊庫中的極端值。

## 發現 2：舊相對日數資料遷移可產生自相矛盾的逐日列
severity: minor
blocking: 否

引句:「    return [None if value is not None and abs(value) > (」

引句:「                                *values, no_data))」

若舊日桶五個值都超過新每日上限，遷移把五欄全改成 `null`，卻保留原本的 `no_data=false`。逐日端點因此回出「五欄全空、仍稱有資料」的列；分析端會把整份逐日回應判為 `invalid`。file: `src/rtb/dsp/store.py:352`、`src/rtb/dsp/store.py:585`、`src/rtb/analyzer/dsp_client.py:288`。

不落檔重現輸出：

```text
migrated_values [None, None, None, None, None]
row_no_data False all_null True
reader_accepts False
```

這同樣只涉及舊庫極端值，但「超額改記缺值」的遷移結果應維持讀取契約自洽。

## 發現 3：截斷歷史核對對時鐘先後的說明寫反
severity: minor
blocking: 否

引句:「    讀取時刻,跟收據同一個。DSP 時鐘比這裡快時,近期窗只會比 DSP 那邊窄,不會誤判;比這裡慢才可能」

實際上 DSP 時鐘較晚時，它的三日截止點較晚、計數可能較少。分析步驟傳入的 `now` 早於 DSP 讀取十秒，且某筆加額落在兩個截止點之間時，DSP 的正確摘要為近期零筆，新增核對卻判 `invalid`。file: `src/rtb/analyzer/dsp_client.py:497`、`src/rtb/dsp/store.py:879`。

以相差十秒、加額落在截止點之間的時刻重現：

```text
analyst_recent 1
dsp_recent 0
accepted False
```

結果保守地變成證據不足；程式註解與圖譜對「哪一方時鐘較快」的說法也需修正。

## 第 2 輪修復驗收

- **截斷歷史總數**：兩種操作計數現在必須等於總數。原先「60 筆只分類 50 筆」已拒收；不落檔重現得到 `incomplete_counts_accepted False`。
- **截斷歷史近期計數**：`read_query_options` 已用同一步的 `now` 核對回傳列下界。把近期加額說成零筆時得到 `recent_underreported_reader_accepted False`；發現 3 是此新核對的邊界。
- **七日總額上限**：新種子與舊相對日數遷移已設每日七分之一上限，算術上七日合計不超白名單；既存日期鍵資料與全欄轉空的例外見發現 1、2。
- **固定日期測試**：原溢位 HTTP 測試改用同一行程的固定時鐘伺服器；預設時鐘改為呼叫時查取，自動夾具能覆寫。第 2 輪指出的隨日期翻紅路徑已移除。
- **舊庫前值與文件**：補不回前值而持續證據不足的取捨已記入 Mock-DSP，並設重驗日期；白名單 RULE 與增量 2b 的 `committed_at` 待辦已更新。

## 圖譜固定席

- **Mock-DSP**：冪等、同交易全有全無、F1 逾時及版本衝突合約未被本 diff 改動；「合計一定在白名單內」的敘述對發現 1 的既存資料不成立。
- **任務流程領域模型**：提案解析、新鮮度、終點狀態及領域匯入邊界未改。
- **分析行程流程與檢查點**：步驟落地及不可信文字防線未改；發現 3 可讓合法歷史在三日切點附近變成無結果。
- **正式九條判斷領域規則**：判斷順序與門檻未改；新增核對沿用其三日常數。
- **確定性指標計算**：缺值仍不變成零；發現 2 是整份逐日資料被拒收。
- **評估與 Jev 決策點**：本輪未改評估集、提示或錄製鍵；宣稱清單的 198 個檔案雜湊與現檔相符。
- **提案收件口**：修訂、死信重放與執行前檢查路徑未改。
- **共用行程基礎**：故障注入、綁定位址及 HTTP 標頭邊界未改。
- **執行迴圈**：同鍵對帳、重投遞與租約路徑未改；新的每日限制作用於 DSP 種子，不改預算操作。

## 驗證限制與效能表態

指定 pytest 子集在收集測試前因唯讀沙箱沒有可用暫存目錄而停止，不能記為通過。不落檔 Python 重現已跑出上述結果。每日七分之一上限的乘積確實低於金額與計數白名單；歷史新增核對最多掃描回傳的 50 列，沒有增加 DSP 讀取次數。

本輪未發現可重現的阻斷問題；上述三項是有具體輸入的非阻斷邊界。