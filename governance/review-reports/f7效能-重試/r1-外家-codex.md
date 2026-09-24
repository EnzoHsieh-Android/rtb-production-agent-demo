severity: major

## 發現 1:租約預算漏算第三次 DSP 呼叫與呼叫紀錄寫入

severity: major
blocking: 是

引句:「從那之後到寫結果,最多隔兩次 DSP 呼叫」

對帳查不到操作紀錄時，會依序查操作紀錄、讀廣告；若檢查不通過，還會向 DSP 作廢，之後才寫結果，中間沒有續租。見 `src/rtb/executor/execution.py:1159`、`src/rtb/executor/execution.py:1173`、`src/rtb/executor/execution.py:1225`。每次 DSP 呼叫回傳前，回呼還會補寫呼叫紀錄；資料庫忙碌時，這一步也可能等鎖 5 秒。見 `src/rtb/executor/dsp_client.py:118`、`src/rtb/executor/execution.py:459`。

具體例子：三次 DSP 呼叫各花 9 秒，三次補寫各等鎖 5 秒，最後寫結果首次等鎖 5 秒，接著按設計重試。預期是重試預算仍落在有效租約內；照設計的常數斷言只計兩次 DSP 呼叫，實際在重試完成前就可能超過 60 秒，收據失效而寫不進結果。S690 的保證因此不成立；需按實際路徑與補寫時間重新定預算。

## 發現 2:接手後轉結果不明沒有經過指定的寫結果函式

severity: major
blocking: 是

引句:「轉結果不明也是寫結果函式,忙碌時會重試;重試沒寫過東西,轉一次還是一次」

對帳接手「嘗試中」的鍵後，是在 `_reconcile` 的交易內直接呼叫 `attempt_store.transition` 轉為「結果不明」，沒有呼叫 `_write`。見 `src/rtb/executor/execution.py:1107`、`src/rtb/executor/execution.py:1126`、`src/rtb/executor/execution.py:1131`；`_write` 是另一個入口，見 `src/rtb/executor/execution.py:946`。

具體例子：對帳接手一筆過期的「嘗試中」紀錄，開 `_reconcile` 交易時等鎖逾時。預期依設計在限度內重試這次轉換；照「只包裝寫結果函式」實作，忙碌會直接離開對帳，由啟動迴圈按忙碌輪次處理，連續三輪可停機，見 `src/rtb/executor/runner.py:70`。設計對 S117、S127、S138 的重試承諾沒有落點。

2 條,blocking 2。