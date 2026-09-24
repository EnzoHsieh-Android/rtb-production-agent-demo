# code-phase9-inc3 第 1 輪收貨與重現紀錄(2026-09-24)

## 收貨
- 7 席收齊才判讀:slo、side、tests、規格符合、架構對齊、資安(sonnet),finder(Codex,沒撞到限額)。受審 c5a34da..26e4f2b(另開唯讀工作樹 /Users/enzo/rtb-p9i3)。
- quote-check:六份有發現的報告全數錨定;slo 席 clean,沒有引句。
- 共 21 條(3 條 minor),合併成 15 件事。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| spec-1 | 讀 src/rtb/ops/sli.py:226-236 端到端三輪不同回最後一輪,Tally 沒有穩定欄;對照 src/rtb/ops/trace.py:427-434 與 metrics 都標不穩定 | HIT,折入 |
| arch-1 | 讀 src/rtb/ops/side_effects.py 的 unauthorized、duplicates 單輪讀、無穩定標記 | HIT,同一類,折入(Tally 加穩定欄;副作用核對依因果順序恆穩定,理由寫進說明) |
| spec-2 | 讀 src/rtb/ops/slo.py:146、:209 任一子窗缺就把 violating 清成空 | HIT,折入 |
| x1-5 | 同 spec-2 | HIT,同一件,折入 |
| 資安-1 | 讀 src/rtb/ops/side_effects.py:223,225 對 committed_at 裸呼叫 fromisoformat,例外不是 DspUnreadable | HIT,折入 |
| 資安-2 | 讀 src/rtb/ops/side_effects.py:275-292 _tally_duplicates 內的 _operation 呼叫沒被接 | HIT,折入 |
| side-2 | 同 資安-2,另指 evaluate 沒有逐條隔離 | HIT,同一件,折入 |
| 資安-3 | 讀 src/rtb/dsp/server.py:171-178 兩支新端點不驗憑證、回傳全租戶明細;既有寫入端點 :198 驗憑證 | HIT,折入(代使用者裁定:加唯讀稽核憑證) |
| 資安-4 | 讀 src/rtb/dsp/server.py:175 isdigit 收上標數字、19 位可超過 SQLite 整數上限 | HIT,折入(minor) |
| x1-7 | 同 資安-4 | HIT,同一件,折入 |
| side-3 | 同 資安-4 | HIT,同一件,折入(minor) |
| side-1 | 讀 src/rtb/ops/side_effects.py:107 expected_version 為空直接比對判壞;policy_version 為空判無法核對 | HIT,折入 |
| x1-1 | 讀 src/rtb/dsp/store.py:399-408 提交時間原樣存時鐘字串,游標查詢用 UTC 字串比較;時鐘可注入非 UTC 偏移 | HIT,折入(寫入前統一成固定 UTC 格式) |
| x1-3 | 讀 src/rtb/ops/sli.py:88 轉人工只判小於期限,期限當刻轉人工後同刻結案回好事件;與實作解讀「剛好期限轉人工算壞」矛盾 | HIT,折入 |
| x1-4 | 讀 src/rtb/executor/attempt_store.py:974 批量讀取沒沿用 snapshot 的鍵一致性檢查;side_effects.py:309 解析失敗 good += 1 | HIT,折入 |
| x1-6 | 讀 src/rtb/executor/attempt_store.py 核可使用批量查詢沒有依鍵索引,每批全表掃 | HIT,折入(不得改動 Phase 6 釘住查詢計畫的測試) |
| tests-1 | 測試席把 slo.run 的計數參數對調,98 支全綠;全庫沒有測試呼叫 slo.run | HIT,折入 |
| tests-2 | 測試席拿掉 missing=True,17 支全綠 | HIT,折入 |
| tests-3 | 測試席拿掉同鍵兩筆的防禦核對,全綠 | HIT,折入 |
| tests-4 | 測試席把端到端 <= 改 <,全綠 | HIT,折入 |
| tests-5 | 翻頁剛好 50 筆且同時間戳沒有回歸測試,行為正確 | HIT,折入(minor) |
| x1-2 | 時鐘倒退時墊高提交時間,重查已過窗可能多出後來的寫入。規格 [S667] 明定墊高行為,計劃「實務隱患」已列為未排除風險(設計審第 3 輪外家席提出、代使用者裁定);實作照規格,重現不到偏離 | MISS,駁回(照規格 [S667],已列實務隱患;另記進使用者覆核清單) |

## 處置
- 折入 20(合併成 15 件),駁回 1(x1-2),放行 0。修法交給新派的增量 3 修正實作員(本工作樹開分支 phase9-inc3-fix)。
