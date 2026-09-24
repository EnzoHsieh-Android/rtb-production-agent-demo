severity: minor

1. 兩者在「顯式傳身分」上是同一種做法，差異只是介面形狀。

分析端把 `task` 沿 `fetch(task, now)` 傳到底層，再交給回呼；執行端則在呼叫點以 `proposal + key` 建立閉包。兩者的身分都由當次呼叫顯式提供，不依賴全域或動態範圍。

file: `src/rtb/analyzer/dsp_client.py:112`  
file: `src/rtb/analyzer/dsp_client.py:123`  
file: `src/rtb/analyzer/dsp_client.py:139`  
file: `src/rtb/executor/execution.py:444`  
file: `src/rtb/executor/execution.py:446`  
file: `src/rtb/executor/execution.py:487`  
file: `src/rtb/executor/execution.py:514`

目前形狀沒有導致錯誤行為或漏記：`DspPort` 五支方法都把 `on_call` 設為必填關鍵字參數；`DspClient._send` 的成功、傳輸失敗、內容讀不懂及 `DspUnavailable` 路徑都會呼叫它。漏傳會由型別檢查及 Python 呼叫簽章直接擋下。

file: `src/rtb/executor/execution.py:131`  
file: `src/rtb/executor/execution.py:136`  
file: `src/rtb/executor/execution.py:140`  
file: `src/rtb/executor/execution.py:146`  
file: `src/rtb/executor/execution.py:150`  
file: `src/rtb/executor/execution.py:154`  
file: `src/rtb/executor/dsp_client.py:96`  
file: `src/rtb/executor/dsp_client.py:104`  
file: `src/rtb/executor/dsp_client.py:108`  
file: `src/rtb/executor/dsp_client.py:115`  
file: `src/rtb/executor/dsp_client.py:118`

反而分析端做法 (a) 的 `on_call` 是可省略的；做法 (b) 的原始物件仍符合 `Submit`／`OperationLookup`，忘記套包裝器不會由協定型別攔住。因此沒有證據支持「改成 (a)/(b) 才比較不會漏記」。

file: `src/rtb/analyzer/dsp_client.py:130`  
file: `src/rtb/analyzer/dsp_client.py:135`  
file: `src/rtb/analyzer/flow.py:109`  
file: `src/rtb/analyzer/flow.py:123`

而且上一輪架構席提出的修法原本就明確包含「在呼叫前建綁好身分的閉包或包裝物件傳進 `_send`」；目前實作正是其中一種。第二輪再把它判成第三種重大偏離，判準前後矛盾。

file: `governance/review-reports/code-phase9-inc1/r1-arch-sonnet.md:31`  
file: `governance/review-reports/code-phase9-inc1/r2-arch-sonnet.md:8`

2. 做法 (a) 在目前的共享拓撲下不能直接正確寫入各工作者自己的資料庫連線。

多工作者會共用同一個 DSP 實例，但每個工作者各自建立 `InboxStore`／`Executor`。目前回呼閉包同時綁定該次提案身分和該 `Executor` 的 `self.store`、`owner`、待寫佇列，因此會回到正確工作者的連線與 actor。

file: `tests/executor/test_multi_worker.py:35`  
file: `tests/executor/test_multi_worker.py:38`  
file: `tests/executor/test_multi_worker.py:40`  
file: `tests/executor/test_f7_end_to_end.py:41`  
file: `tests/executor/test_f7_end_to_end.py:44`  
file: `tests/executor/test_f7_end_to_end.py:61`  
file: `src/rtb/executor/execution.py:440`  
file: `src/rtb/executor/execution.py:448`  
file: `src/rtb/executor/execution.py:462`

若共用用戶端只在建構時綁一支回呼，那支回呼只能固定綁某一個工作者的 store；單傳提案身分並不足以找回「哪個 Executor／哪條 SQLite 連線」。要成立就必須改成每個工作者各有 client，或再增加連線路由／每次另開連線機制，已不是單純套用分析端 (a)。

現有回歸測試也直接驗證共享 DSP client 時兩個工作者的任務與 actor 不會混寫。

file: `tests/executor/test_dsp_calls.py:285`  
file: `tests/executor/test_dsp_calls.py:296`  
file: `tests/executor/test_dsp_calls.py:299`  
file: `tests/executor/test_dsp_calls.py:300`

3. 結論：降為 minor。

這確實是第三種「介面形狀」，但不是第三種身分傳遞語意；目前沒有會做出錯誤行為、靜默漏記或型別防線倒退的證據。要求統一成 (a) 或 (b) 屬於形狀偏好，而且直接套用 (a) 反而與共享 client、工作者各持資料庫連線的現況衝突。