# code-phase9-inc1 第 1 輪收貨與重現紀錄(2026-09-24)

## 收貨
- 8 席收齊才判讀:inbox、dsp、trace、tests 四個鏡頭(sonnet),外家 finder(Codex,沒撞到用量限額),規格符合、架構對齊、資安(sonnet)。
- 各席動過的 git 只在 /tmp 臨時副本;repo 的 reflog 只有我自己的合併與改署名兩筆。
- quote-check:六份有發現的報告全數錨定;資安、trace 兩席 clean,沒有引句。
- 共 8 條發現(同一件事合併前 10 條):1 條阻擋級,其餘 major。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| dsp-1 | 讀 src/rtb/executor/dsp_client.py 的 _report 沒有攔回呼例外;log_dsp_call 開短交易撞鎖丟 InboxBusy,蓋掉已算好的 DSP 結果 | HIT,折入 |
| x1-4 | 同 dsp-1(呼叫紀錄撞鎖永久漏帳) | HIT,同一件,折入 |
| inbox-1 | 同 dsp-1,另指出 DspPort 的寫入與作廢承諾不丟例外、對帳整輪被放棄 | HIT,同一件,折入 |
| arch-1 | 讀 src/rtb/executor/execution.py 的 record_dsp_call:範圍外讀不到就靜默不記;分析端 DSP 用戶端是顯式傳任務 | HIT,折入(改成每次呼叫顯式傳回呼,拿掉模組層級範圍變數) |
| tests-1 | 讀 tests/ops/test_ops_boundaries.py 的掃描只看屬性、名字、匯入,getattr 加字串派發看不到 | HIT,折入(維運套件禁用 getattr 類動態取屬性) |
| tests-2 | lumos spec-trace 判 [S625][S626] 懸空,測試函式名跟規格標的不同 | HIT,折入(測試改名配規格) |
| spec-1 | 讀 src/rtb/executor/inbox_store.py 的 release:表用 coalesce 保留舊原因,事件卻記這次傳入的空值 | HIT,折入(事件記 coalesce 之後的值) |
| x1-1 | 讀 src/rtb/executor/inbox_store.py 的 take_over:擁有者是自己的分支也無條件寫租約過期被接手 | HIT,折入(只有舊租約真的到期或換人才寫) |
| x1-2 | 讀 src/rtb/ops/trace.py 的 _revision_keys 以任務加修訂去重,內容雜湊不同的第二份被丟掉 | HIT,折入(關聯鍵帶內容雜湊) |
| x1-3 | 讀 src/rtb/httpclient.py:68-70:HTTPError 分支解析本文失敗時丟 ValueError,狀態碼遺失 | HIT,折入(解析失敗的錯誤帶狀態碼,依狀態碼分類) |

## 處置
- 全部折入,放行 0、駁回 0。修法交回增量 1 實作員(先寫會紅的測試再修)。
