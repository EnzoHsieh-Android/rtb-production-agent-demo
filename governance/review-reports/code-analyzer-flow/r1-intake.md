# code-analyzer-flow r1 intake

## 收貨
- 七席全數收齊才動程式:通才四席(s1 條款符合度、s2 併發與崩潰恢復、s3 測試品質、s4 邊界與型別)、架構對齊 sarch、資安 sec、外家 x1(Codex)。normalize:七份都已是正規化格式;quote-check 全數錨定(r1-s2 有一句引用審材外程式碼、不影響 finding,不採信)。
- 外家席有派,不必降級成單一家族視角。

## 使用者裁定
- Protocol vs Callable(sarch f1):使用者明講「可替換介面是不錯的寫法,如果是架構需要,也不必研習舊制」——不改回 Callable,保留 Protocol,在 flow.py 補一段理由;這不算壓掉建議,是有裁定依據的張力。

## 編排者重現與處置
| 主題(涉及席位) | 做法 | 結果 |
|---|---|---|
| 證據表 evidence_id 單鍵在多輪重新規劃撞唯一鍵(s2f1、s4f2、x1f3 三席獨立重現) | 紅測試:兩輪蒐證給相同證據編號 | HIT。改成複合主鍵(task_id, task_seq, evidence_id) |
| commit_step 不核對證據的 task_id(s1f6、s4f1、x1f3) | 紅測試 | HIT。加 EvidenceTaskMismatch 檢查並拒收 |
| Submit 回傳值不驗證型別(s1f1、x1f1) | 紅測試 | HIT。加 isinstance 檢查,不合格丟 _BrokenCollaborator |
| Decide 回傳值誤判為 NeedsFreshEvidence(s1f2、x1f2) | 紅測試 | HIT。加明確的三選一 isinstance 分派,第四種丟 _BrokenCollaborator |
| S41 守衛測試篩選邏輯有洞(s1f3、x1f6) | 讀程式證實邏輯錯誤(or 運算子優先序) | HIT。重寫篩選,並加「守衛的守衛」測試證明種下去的違規真的被抓到 |
| advance() 自己輸掉並行競爭的回傳路徑沒測試(s3f1) | 紅測試(monkeypatch commit_step 回 False) | HIT,補測試 |
| S37/S38 不是真並行、沒測過 advance() 這一層(x1f4) | 改寫成真實 20 條執行緒加 threading.Barrier 測試 | HIT。過程中意外撞出 EvidenceSource 回傳非 tuple 時 commit_step 對 None 取迭代器崩潰的真實 bug(不在任何一席報告裡,是補測試時自己發現的) |
| S39/S40「每一步」只測一步(s1f5、x1f5) | 拆成四個測試,RECEIVED/COLLECTING_EVIDENCE/ANALYZING/PROPOSED 各自崩潰前中斷一次 | HIT |
| create_task 無格式驗證(s4f3、secf3) | 紅測試 | HIT。用網域層既有的 is_id |
| error_detail 無長度上限(secf4) | 紅測試 | HIT。加 MAX_ERROR_DETAIL_LENGTH=2000 截斷 |
| 讀回毀損的資料沒有安全邊界(secf1、secf2) | 讀程式確認屬實 | 部分折入:evidence_for 的毀損路徑接住轉 FAILED(有測試);advance() 最開頭 store.latest() 讀到毀損列仍會往外傳例外,這部分寫進圖譜已知缺口並附 REVISIT,留給增量 4 決定呼叫端策略 |
| TaskStore.__init__ 不轉譯 DatabaseBusy(s4f5) | 讀程式確認 | HIT。加 TaskStoreBusy |
| evidence_for 排序不保留蒐證順序(s4f4) | 紅測試 | HIT。改用 ORDER BY rowid |
| commit_step 不驗證 new_state 合法性(s2f2) | 讀程式確認屬實,是防禦縱深缺口不是行為錯誤 | HIT(比原本要求的更進一步):加 can_transition 檢查,不只是記錄成已知限制 |
| NOOP 模組層級單例污染跨測試(s3f4) | 讀程式確認、對照污染實驗 | HIT。加 autouse fixture 每個測試前清空 .calls |
| decide/evidence_source 引數未斷言(s3f3) | 補測試 | HIT |
| evidence_for 的 task_seq 過濾沒被測試咬到(s3f2、s2f5 clean) | 補測試(跨輪隔離) | HIT |
| Protocol 取代 Callable 慣例(sarch f1) | 使用者裁定保留 | 折入方式=補理由到 flow.py docstring,不改寫法 |

補充:全套 549 條測試、ruff、mypy 全過;新增與修正的 9 個防護逐一變異檢查,起初有 6 個沒被咬到,補測試後全部翻紅;analyzer 測試連跑 25 次(含真實多執行緒測試)0 次失敗。

refuted:none。

## 處置
- 21 條全部折入,沒有放行(有 major,規定不得放行)。
