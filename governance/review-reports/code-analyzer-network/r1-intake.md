preflight-4: ran

# 編排者重現表(第 1 輪)

只有一條發現走到「機械重現不到,列進 refuted-set」:sec 報告的第 2 條
(`policy.decide() 對 DSP 數值沒有任何上限或來源檢查`)。其餘存活發現(24 條中的 23 條)
都直接折進程式與測試,不需要重現表。

| id | 重現指令/查證方式 | 結果 | 判讀 |
|---|---|---|---|
| sec-2 | sec 報告原句:「這條**依附**於第 1 條才成立——目前程式庫裡沒有其他管道能讓外部內容進到 `policy.decide()` 的輸入」。第 1 條(httpclient 不自動跟隨重新導向)已折入並有紅綠測試 `test_a_3xx_response_is_not_followed_and_becomes_an_http_error` 守著;另外自己重新掃了一遍 `dsp_client.py`/`inbox_client.py`,確認 `policy.decide()` 的輸入只有兩個來源:`dsp_client.fetch()` 回傳的 `Evidence.payload`(唯一經過 HTTP 往返讀到,且只讀寫死網址的直接 200 回應本文,現在已經不能被重新導向到別的主機)、以及呼叫端自己建的 `TaskRow`(不經過網路)。 | MISS(重現不到「還有其他管道」) | sec 自己標註這條的可觸發性完全依附發現 1;發現 1 已修復關閉,原句講的攻擊路徑(靠重新導向讓攻擊者決定 DSP 回應內容)已經不存在。`policy.py` 檔頭本已聲明「這不是交接文件後面階段要做的真正業務規則」,真正的合理性檢查留給後續增量,不在這次範圍內另外加。 |
