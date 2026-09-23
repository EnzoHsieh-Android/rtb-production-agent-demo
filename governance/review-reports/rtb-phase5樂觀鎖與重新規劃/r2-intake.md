# 第 2 輪設計審收貨與重現紀錄(2026-09-23)

## 收貨
- 五席收齊才動計劃:s1 前輪修正驗收、s2 收件口與 DSP 分流、s3 接續任務與 F4 完成條件、sarch 架構對齊、x1 外家 Codex(codex exec --sandbox read-only,沒有撞到用量限額,不用頂替)。
- 受審版本:第 2 版(提交 1367daa 那一版,含記帳後補上的落點欄位)。第 1 輪處置閘因記帳後才補落點而指紋不符(G3),照前例不改帳,由本輪覆核新版本。
- x1 報告從 Codex 輸出的最後一段(用量統計之後)原樣截出。
- report-normalize:五份都已是正規化格式。quote-check:五份全數錨定。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| x1-1 | 讀 src/rtb/executor/execution.py:166 寫入成功先進「已提交未驗證」;:413、:430 只有已驗證才確認已交給執行;src/rtb/executor/inbox_store.py:50 已交給執行的定義 | HIT,採信,折入(改成先問收件口,收件表清掉才問 DSP) |
| x1-2 | 讀 src/rtb/dsp/store.py:93 操作紀錄帶廣告、動作、參數、預期版本 | HIT,採信,折入(查到後核對內容,不符記冪等衝突) |
| s2-1、x1-3 | 第 2 版兩次查詢之間無鎖;讀 src/rtb/executor/inbox_store.py:407 清除只清「沒有待處理也沒有處理中」的任務 | HIT,採信,折入(順序對調後,清掉時執行端已結束,空檔消失) |
| x1-4 | 讀 src/rtb/domain/attempt.py:3 鍵存下後不從提案重算、:17 前綴是算法版本 | HIT,採信,折入(交接時存鍵) |
| x1-5 | 讀 src/rtb/executor/inbox_store.py:37 保留期、:407 清除;嘗試紀錄不清 | HIT,採信,折入(指標兩側各讀不會被清的資料) |
| s1-1 | 讀 src/rtb/analyzer/flow.py advance 簽章只有三個協作者 | HIT,採信,折入(可選具名參數) |
| s1-2、s2-2 | grep OPERATION_PREVIOUSLY_FAILED:execution.py:418、:433、inbox_store.py:489 共三處 | HIT,採信,折入 |
| s1-3 | 讀 tests/analyzer/test_flow.py:228 | HIT,採信;判準另想:改用可選參數後這支與 :372 那支都不必取代,S316 直接綁它 |
| s3-1 | 讀 src/rtb/analyzer/task_store.py:186 唯一入口先檢查格式 | HIT,採信,折入(另開建接續任務入口) |
| s3-2 | 讀第 2 版 S307 只寫「仍衝突」 | HIT,採信,折入(不分原因) |
| s3-3 | 讀 src/rtb/analyzer/task_store.py:392 軌跡只用單一任務編號 | HIT,採信,折入(S318) |
| sarch-1 | 讀 Systems/分析行程流程與檢查點.md:40 的規則 | HIT,採信,折入 |

## 處置
- 折入 16:s1-1、s1-2、s1-3、s2-1、s2-2、s3-1、s3-2、s3-3、sarch-1、x1-1、x1-2、x1-3、x1-4、x1-5。s1-2 與 s2-2 是同一件事、s2-1 與 x1-3 是同一件事,各計一條。
- 放行 0、駁回 0。
- 方向調整:第 2 版「DSP 為真相」被 x1-1 推翻,第 3 版改成「收件口為真相、收件表清掉才以 DSP 補位,補位查不到就交給人」。使用者裁定(情境題 c、設計 B)沒有動。
