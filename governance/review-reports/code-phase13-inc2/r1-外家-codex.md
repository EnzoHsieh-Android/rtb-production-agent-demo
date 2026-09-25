severity: major

## 發現 1: 停止訊號在組提示期間到達，仍會呼叫模型
severity: major
blocking: 是

引句:「+        user = inv.prompt(base_receipt, receipts, state, name, truncated)」

`stop_requested` 的第二次檢查在進入 `_ask` 前；組完提示後，`self.complete` 前沒有再檢查。file: `src/rtb/analyzer/ai_judge.py:98`、`src/rtb/analyzer/ai_judge.py:107`

例子：續租後開始組提示，期間收到停止訊號 → 預期不呼叫模型並跳過這一步 → 實際仍呼叫模型，違反停止後不再送出的合約。

重現：用假 `complete` 計數，讓 `inv.prompt` 的替身在回傳前設下停止旗標，再呼叫 `Judge`。唯讀實驗得到 `stop_set_before_complete=True`、`model_calls_after_stop=1`；未呼叫真模型。

## 發現 2: 操作歷史的壞欄位讓任務反覆卡在蒐證
severity: major
blocking: 是

引句:「+    "action": lambda value: value in {item.value for item in ActionType},」

JSON 回應若把 `action` 寫成陣列，集合查找會拋 `TypeError`，沒有轉成 `invalid` 收據；蒐證步驟吞下例外後留在原狀態，下一次又讀到同一筆。file: `src/rtb/analyzer/dsp_client.py:235`、`src/rtb/analyzer/dsp_client.py:290`、`src/rtb/analyzer/flow.py:344`

例子：`check_change_history` 回應含 `"action":[]` → 預期記錄「查詢沒有結果，原因 invalid」並讓調查繼續 → 實際每次蒐證都拋 `TypeError`，任務無法前進。

重現：把其餘五個歷史欄位填成合法值，執行 `check_history({"history":[row]})`，其中 `row["action"]=[]`；實測拋出 `TypeError: cannot use 'list' as a set element`。

## 發現 3: 逐日點擊率可把不可能的數據寫成可信收據
severity: major
blocking: 是

引句:「+                rate(earlier, "clicks", "impressions"), rate(recent, "clicks", "impressions"))),」

逐日趨勢用一般比率計算點擊率變化，略過領域層已有的「點擊不能多於曝光」檢查；逐日回應的白名單也只逐欄驗型別。file: `src/rtb/analyzer/investigation.py:248`、`src/rtb/domain/metrics.py:205`、`src/rtb/analyzer/dsp_client.py:259`

例子：前四天每天曝光 100、點擊 10；最近三天每天曝光 100、點擊 200 → 預期異常點擊率使 `click_rate_change` 為 `na` → 實際收據寫入 `"1900.0"`，模型可將它當成已核對的證據。

重現：將上述七列及合法的其餘欄位交給 `check_daily`，再呼叫 `receipt_payload(CHECK_DAILY_TREND, ...)`；實測回應通過檢查，產生 `"click_rate_change":"1900.0"`。

3 條,blocking 3。