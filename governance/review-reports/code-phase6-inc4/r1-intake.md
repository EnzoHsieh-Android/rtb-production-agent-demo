# 第 1 輪收貨紀錄(code-phase6-inc4,high)

九席:查詢正確性(correct)、開庫補欄位與索引(migrate)、測試殺傷力(tests)、對增量 1 的影響(compat)、外家 Codex 找碴(finder)、外家 Codex 否決(veto)、規格符合(spec)、架構對齊(arch)、資安(資安-sonnet)。correct、migrate、compat 三席 clean(migrate 席用 10 個獨立行程連開同一顆舊庫 30 次無誤;檔首格式退回該席自補,判定未動)。載體選外家 finder 席(引句全錨)。
收件口模組的新增讀取方法先報主線核准(位置照主線要求放在 record_event 正上方與 _parse_payload 前面,避開增量 3 的改動)。主線另告知增量 3 在停下紀錄加了「比例過大」種類(已用、門檻是空值):這裡的查詢只取總曝險已滿與表滿延後,停下紀錄的已用與門檻本來就可為空,不受影響。

## 重現表

| id | 席 | 重現命令 | 結果 | 處置 |
|---|---|---|---|---|
| finder-F1 | 外家 | 讀 aggregate_audit:holding 只是嘗試紀錄模組的 (鍵, 金額, 舊列);測試只驗金額 | HIT:「目前佔額度的」答不出是哪幾份提案 | 折:holding 改成跟通過清單同一種逐筆紀錄(帶任務、修訂、廣告、狀態);[S367][S368] 測試驗身分與舊列標記;拿掉身分那行 4 支紅 |
| veto-F1 | 外家 | 同上(範圍外仍佔額度的那筆沒有身分) | HIT | 折:同上 |
| arch-F1 | 架構 | 讀 observability 第一版:直接對 write_stops 下 SQL,attempt_store 開了交出連線的 connection() | HIT | 折:收件口模組加 stop_count、stops 兩個只讀方法(先核對交易歸屬),嘗試紀錄模組加 counted_first_rows_started、counted_first_rows;新模組不寫 SQL;拿掉 connection();補一支「別的收件表開的交易讀不到」的測試,拿掉任一邊的歸屬核對就紅 |
| security-F1 | 資安 | 讀 aggregate_audit:只擋沒給範圍,不擋極寬範圍 | HIT(目前沒有外部呼叫端) | 折(文件):模組說明與筆記寫明範圍不設上限的代價,Phase 9 接輪詢時一併決定上限與不握寫入鎖的讀法 |
| tests-F1 | 測試 | 讀 [S363] 測試:已用與 aggregate_used 同源互比 | HIT | 折:拿掉那行,只留對照具體數字的斷言 |
| tests-F2 | 測試 | 讀 Holding.legacy:沒被用也沒被測 | HIT | 折:holding 改成帶舊列標記的逐筆紀錄,[S368] 驗 up/old-up/old-down 的標記;把標記寫死成否就紅 |
| spec-F1 | 規格 | 讀新筆記:說依賴只有嘗試紀錄模組,實際也用收件口模組的種類列舉 | HIT | 折:筆記改成「這裡 → 收件口模組、嘗試紀錄模組」 |
