# code-metrics r1 intake(收貨、重現與處置留痕)

## 收貨三道
- quote-check:兩席全數錨定。refcheck:m1 引了示範路徑 `src/rtb/other/o.py`(席位為了實測 ruff 範圍自己造的假路徑,不是真檔案),不影響 finding。
- 席位:m1 通才單一審查員、march 架構對齊(標準強度;外家席依編制「退同門並留痕」,本輪沒派外家)。

## 編排者機械重現(用修復前的程式碼)
| 宣稱 | 命令 | 結果 |
|---|---|---|
| 極大整數讓 ctr 丟 OverflowError(m1-2) | 舊版 metrics.py 跑 ctr(2**1024, 1) | HIT:OverflowError |
| roas(1e300, 1e-300) 回 inf 且被當有值通過門檻(m1-2) | 舊版跑 roas(...).at_least(10) | HIT:value=inf,at_least(10) 為 True |
| pacing(None, 100, 2) 被說成缺資料而非不合理(m1-1) | 舊版跑 | HIT:reason=missing_data |
| bool(ctr(0, 0)) 為 True(m1-3a) | 舊版跑 | HIT:True |
| MetricResult(1.0, "no_denominator") 可被建構(m1-3b) | 舊版跑 | HIT:建構成功 |
| ruff 全域禁用清單也誤擋 dsp 以外的新路徑、且不含 requests 等(march-1、march-2、m1-5) | 席位以 --stdin-filename 實測輸出完整 | 採信;修復後改為目錄內設定,新增測試證明 dsp 與 tests 不受影響、requests/httpx/urllib3/aiohttp/importlib 被擋 |
| seed_metrics 不驗值、NaN 變成 null、bytes 讓 GET 回 500(m1-4) | 席位以 s.py、h.py 重現輸出完整 | 採信;修復後有測試,拿掉驗證會紅 |

refuted:none。

## 處置
- 9 條全部折入,沒有放行。
- 兩個語意取捨(cvr 轉換多於點擊、pacing 時間比例為 0)不改程式,寫進圖譜「已知缺口」並附回頭條件;`__import__` 與逐行 noqa 擋不住,也寫進圖譜。
- 折入方式:程式修正加測試(176 條);新增防護做 11 個變異檢查,全被抓到;lumos 的 linter 指令實測會吃到目錄內設定。
