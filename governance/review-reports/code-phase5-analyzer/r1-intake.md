# code-phase5-analyzer r1 收貨紀錄(2026-09-23)

風險分級 high(`lumos pitfalls --diff` 判定:命中風險型樣)。編制七席:s1 正確性與狀態轉換、s2 測試假綠、s3 相容與回歸、s4 設計符合度、sarch 架構對齊、sec 資安、x1 外家 Codex(沒有撞到用量限額)。七席收齊才讀、才動程式。reviewer 報告跑過 report-normalize --write(都已是正規化格式)。quote-check:s4 的第 2 句引句跨兩行,錨不到;該條由編排者機械重現(見下表)後才採信,其餘全數錨定。

另有使用者裁定(2026-09-23,執行側代碼審提出):收件口回給分析行程的擋下原因,超過預算上限與廣告不屬於租戶合併成 not_permitted。分析側 sec-1 的修法照這個值域寫白名單。

| id | 重現命令 | 輸出摘錄 | 結論 | 處置 |
|---|---|---|---|---|
| s1-1、s2-1 | 已交給執行時讓 submit 回字串,跑 advance | 沒有例外,任務停在已交給執行;送出提案那一步同情況丟 _BrokenCollaborator | HIT | 折:型別不對丟 _BrokenCollaborator;新測試先紅後綠 |
| s1-2、s1-3、s4-1 | 版本已變重新規劃後讀錯誤說明 | blocked=version_changed;version_changed;follow_up=…,衝突與已有關係兩條沒有原因 | HIT | 折:統一成「收件口回的;replan=原因;結果」,新測試釘整串格式 |
| s4-2 | 預先寫一列代數用完的接續關係,再帶接續結案 | IntegrityError UNIQUE constraint failed: follow_ups.original_task_id | HIT | 折:已有關係改看列是否存在,不看接續任務編號是否為空;新測試先紅後綠 |
| s2-2 | 把代數上限判斷改成 if False,跑代數上限測試 | 修前:無限迴圈;修後:0.04 秒斷言失敗 | HIT | 折:迴圈加步數上限 |
| s3-1、sarch-2、x1-2 | DSP 回 expected_version=0 | 修前:被當成內容不符、任務永久擋下 | HIT | 折:預算與版本下限改 1;新參數先紅後綠 |
| s3-2 | 讀 _write_follow_up | 接續任務編號沒跑格式檢查 | HIT | 折:補 is_id 檢查 |
| sarch-1 | 個別拿掉 _matches 的廣告、動作、預期版本比對 | 修前:分析側測試全綠 | HIT | 折:四欄位各一組不符的參數;三道變異修後全紅 |
| sec-1 | 餵含逃逸字元與換行的 block_code | 修前:原樣寫進歷史表 | HIT | 折:擋下原因白名單(含 not_permitted),不在清單當讀不懂;新測試先紅後綠 |
| x1-1 | 讓 _from_dsp 忽略存下的鍵 | 修前:五支相關測試全綠 | HIT | 折:測試把存下的鍵改成跟重算不同,斷言查詢用存下的;變異修後紅 |

refuted:none。

變異檢查(清 __pycache__,拿掉防護跑對應測試):不用存下的鍵、不核對廣告、不核對動作、不核對預期版本、擋下原因不驗值域、型別錯吞掉、版本 0 當合法、代數上限失效——八道全紅。分析側測試 243 支全綠。
