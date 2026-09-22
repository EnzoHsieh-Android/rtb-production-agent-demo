# rtb-phase2任務流程 r2 intake(收貨、重現與處置留痕)

preflight-4: ran

## 前掃(機械宣稱驗語意)
| 宣稱 | 做法 | 結果 |
|---|---|---|
| 增量 1 有 `task_state.transition()` 可重用 | 讀 src/rtb/domain/task_state.py | 屬實:transition/can_transition 存在,IllegalTransition 是 ValueError 子類 |
| 增量 1 的 Evidence 沒有序列化函式 | 讀 src/rtb/domain/evidence.py | 屬實,無 to_primitives;增量 3 實作時要自己序列化(與 dsp/executor 現有模式一致),已在設計裡沒明講,留給實作 |
| `immediate_transaction` 存在可重用 | 讀 src/rtb/sqlitekit.py | 屬實 |
| refcheck 對 `src/rtb/analyzer/` 回 missing | 該路徑是設計階段引用、還沒建立的目錄,不是壞引用 | 已知,設計文件常態,不算問題 |

## 收貨
- 三席全數收齊才動計劃。normalize:三份已是正規化格式;quote-check 三份全數錨定;refcheck 對 r2-s2、r2-sarch 各有一個行號超出(引用審材外程式碼位置,不影響引句本身)。

## 判讀與處置
| 宣稱 | 做法 | 結果 |
|---|---|---|
| s1f1:FAILED 狀態完全沒有寫入路徑 | 讀 task_state.py 確認 FAILED 是自動併入每個非終點狀態的合法目標;逐條核對 S23~S32 的原稿確實沒有任何一條寫 FAILED | HIT。折入:定義 Decide 丟例外時轉 FAILED(唯一的觸發點,理由見下),EvidenceSource/Submit 的例外維持「可重試,不轉 FAILED」 |
| s1f2:PRIOR-ART 與驅動函式描述順序矛盾,並行呼叫沒有規則,序號怎麼決定沒寫 | 讀原稿:PRIOR-ART 說「單一交易先查後寫」,但驅動函式段落自己描述讀→呼叫外部介面→寫,中間夾外部呼叫,兩者確實矛盾 | HIT。折入:改寫 PRIOR-ART 措辭;新增「並行與序號」小節,交易內重核對最新列、序號交易內用 MAX+1 決定;新增 S37、S38 兩條合約 |
| s1f3:tasks 表欄位未定義 | 讀原稿確認只有主鍵 | HIT。折入:列出完整欄位(任務編號、序號、狀態、廣告編號、提案快照、錯誤細節、寫入時間) |
| s1f4:create_task 主線沒有條款 | 讀原稿確認 S20/S21(舊編號)只有兩個例外分支 | HIT。折入:新增 S20(首次建立寫 RECEIVED),原本的兩條變成 S21、S22 |
| s1f5:S34/S35 的中斷鉤子語意沒講清楚 | 讀原稿 | HIT。折入:S39/S40(原 S34/S35)明講鉤子語意與收件口的 before_commit 相同 |
| s2f1:S34/S35 暗中假設 Submit 對同一份提案重複呼叫是冪等的,沒寫進設計 | 讀原稿確認只從 Accepted.replayed 欄位側面暗示 | HIT。折入:明講這是 Submit 介面的前提(對應增量 2 收件口的核心合約),並用它把「Submit 丟出未定義例外」也歸類為可重試(不是 FAILED) |
| s2f2:Decide 呼叫本身丟例外完全沒定義 | 讀原稿確認只有 EvidenceSource 有對等條款 | HIT,與 s1f1 一起折入(FAILED 的唯一觸發點) |
| s2f3:歷史表長期運作無界成長,沒留 REVISIT | 對照 [[Systems/提案收件口]] 已有的同類 REVISIT | HIT。折入:新增 REVISIT(2026-10-20),與收件口的保留期限一起決定 |
| sarch f1:Submit 把執行行程的例外語意改寫成純結果型別 | 對照 src/rtb/executor/inbox_store.py 的 Accepted/InboxRejected 風格 | HIT。折入:Submit 改成「成功回傳 Accepted、預期失敗用型別化例外(SubmitStale/SubmitBusy)」,對齊既有風格 |
| sarch f2:tasks 表用只增日誌當現況來源,跟 DSP/執行行程的兩表分工不同 | 讀三個行程各自的儲存設計 | 屬實且是刻意的。折入方式=在設計裡補一段明確理由(檢查點的正確性目的需要「目前狀態只有一種讀法」,原地改寫做不到),不是照建議改成一致;這是合法的張力,不是壓掉建議 |
| sarch f3:分析行程套件沒有落點 | 讀原稿確認「落點」節只提增量 1 | HIT。折入:落點補一條增量 3 的新節點 |

補充:重跑 lumos spec-gate,41 條全部句式合格、條款綁定通過;lumos lint 通過。

refuted:none。

## 處置
- 8 條全部折入,沒有放行(有 major,規定不得放行)。
