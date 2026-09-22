# rtb-phase2任務流程-inc4補記 r1 intake(治理帳修復:合併原兩輪的真實審查紀錄)

preflight-4: ran

## 為什麼開這個新編號
增量 4 的設計審原本在既有的 `rtb-phase2任務流程` 這個編號下走了兩輪真的審查(第 1 輪:s1/s2/sarch 三席,12 條發現;第 2 輪:全新的 s1、sarch 複驗修復,5 條發現)。記帳過程中,我把已經被 r5 輪引用、雜湊釘住的 `r4-intake.md` 事後又改了兩次(先是把附加內容切到 `r5-intake.md`,又刪掉合併回去),導致 `loop status --disposal` 判定「留痕事後被改」。帳本規矩是不能撤銷已記的筆,只能換編號重記——所以開這個新編號,把兩輪真實審查的判讀原封不動地在這裡重新記一次帳,不是重新審查。

## 收貨(原樣照抄自兩輪的真實收貨)
- 第 1 輪:s1(正確性與可測性)、s2(資安與邊界)、sarch(架構對齊),三席全數收齊才動計劃。s2 報了 1 個 blocker(兩個用戶端完全沒設逾時)。
- 第 2 輪:全新的 s1、sarch 複驗第 1 版的修復,兩席全數收齊才動計劃。

## 判讀與處置(原樣照抄,見 governance/review-reports/rtb-phase2任務流程/r4-s1.md、r4-s2.md、r4-sarch.md、r5-s1.md、r5-sarch.md 的完整內容)

### 第 1 輪(12 條,全部折入)
| 主題 | 結果 |
|---|---|
| s2f1(blocker):兩個用戶端完全沒設逾時 | 折入:新增共用 httpclient.py,timeout_seconds 必填無預設值 |
| s1f3、s2f2:too_many_revisions 誤吸進 SubmitStale,無限重試 | 折入:新增 SubmitRejectedPermanently,轉 FAILED |
| s2f3:inbox 的 X-Fault 沒有對稱保證 | 折入:S44/S52 擴大涵蓋 inbox_client |
| s2f4:「沒有公開介面」只是文件宣稱 | 折入:封閉列舉 + isinstance 核對 |
| s1f1:content_hash 正規化演算法沒定義 | 折入(但第 2 輪發現文字沒真的寫進去,見下) |
| s1f2:配速公式除以零/缺值會丟例外 | 折入:重用 MetricResult |
| s1f4:S51 trace 沒有具體介面 | 折入:trace_for(store, task_id) |
| s1f5:tool_calls 寫入失敗沒有合約 | 折入:自己絕不讓例外往外傳 |
| sarch f1:三個新檔案沒有落點 | 折入:併入既有兩篇節點 |
| sarch f2:兩個用戶端要不要抽共用基礎沒討論 | 折入:httpclient.py |
| sarch f3:tool_calls 由誰寫沒交代 | 折入:instrumented.py 包裝層 |
| sarch f4(minor):雜湊正規化沿用既有慣例 | 折入,併入 s1f1 |

### 第 2 輪(5 條,全部折入)
| 主題 | 結果 |
|---|---|
| r5s1f1:content_hash 演算法第 1 版折入時文字沒真的寫進去 | 折入:這次真的補上演算法文字 |
| r5s1f2:X-Fault 原始碼掃描可被拼接繞過 | 折入:封閉列舉 + isinstance 是真正防線,掃描降級輔助 |
| r5s1f3:tool_calls 的 task_seq 語意未定義 | 折入:等於 evidence 表同一顆 row.seq |
| r5s1f4(minor):落點段落文字瑕疵 | 折入:改成五支 |
| r5sarchf1:SubmitRejectedPermanently 定義層次矛盾 | 折入:明講定義在 flow.py |

refuted:none(兩輪皆是)。

## 處置
- 17 條(12+5)全部折入,沒有放行(第 1 輪有 blocker,規定不得放行)。
