# rtb-phase2任務流程 r4 intake(收貨、重現與處置留痕)

preflight-4: ran

## 前掃(機械宣稱驗語意)
| 宣稱 | 做法 | 結果 |
|---|---|---|
| DSP 有 /campaigns/{id} 與 /campaigns/{id}/metrics 兩個端點 | 讀 src/rtb/dsp/server.py ROUTES | 屬實 |
| 收件口有 /proposals,回應含 status/task_id/revision/state/content_hash/replayed 或錯誤代碼 | 讀 src/rtb/executor/inbox_server.py | 屬實,REJECTION_STATUS 對照表存在 |
| 增量 1 的提案格式有 policy_version 欄位 | 讀 src/rtb/domain/proposal.py Proposal | 屬實 |
| Evidence 需要 content_hash 符合 64 位十六進位 | 讀 src/rtb/domain/evidence.py | 屬實,設計裡沒明講怎麼算(交給實作),不算矛盾 |

## 收貨
- 三席全數收齊才動計劃。normalize、quote-check、refcheck 全數通過。

## 判讀與處置
| 宣稱 | 做法 | 結果 |
|---|---|---|
| s2f1(blocker):兩個用戶端完全沒設逾時 | 讀原稿確認完全沒提 | HIT。新增共用的 httpclient.py,timeout_seconds 必填無預設值 |
| s1f3、s2f2:too_many_revisions 被吸進 SubmitStale,無限重試迴圈 | 讀 inbox_store.py 確認 too_many_revisions 是結構性上限,重送不會消失 | HIT。新增 SubmitRejectedPermanently,flow.py 對它轉 FAILED(這是這個增量唯一要改增量 3 程式碼的地方,原因寫清楚) |
| s2f3:inbox 的 X-Fault 沒有對稱保證 | 讀增量 2 設計確認收件口確實也支援 X-Fault | HIT。S44/S52 擴大涵蓋 inbox_client |
| s2f4:「沒有公開介面」只是文件宣稱,無法機械驗證 | 讀原稿確認沒有具體設計 | HIT。改成 request_json 的 headers 參數只接受封閉列舉,原始碼字串掃描 + 簽章檢查兩道 |
| s1f1:content_hash 正規化演算法沒定義 | 讀增量 2 已有的正規化模式 | HIT(設計文字已補,細節留給實作跟增量 2 一致的做法,不重複整段抄) |
| s1f2:配速公式除以零/缺值會丟例外 | 讀 metrics.py 的 MetricResult.below() | HIT。改用既有的 MetricResult 設計,缺值一律 NoAction |
| s1f4:S51 trace 沒有具體介面 | 讀原稿確認只有一句話 | HIT。定義 trace_for(store, task_id) -> TraceRecord 具體函式 |
| s1f5:tool_calls 寫入失敗沒有合約 | 讀原稿 | HIT。訂為「自己絕不讓例外往外傳」,比照收件口事件表的既有做法 |
| sarch f1:三個新檔案沒有落點 | 讀原稿 | HIT。併入既有兩篇節點,不新開 |
| sarch f2:兩個用戶端要不要抽共用基礎沒討論 | 讀增量 2「共用基礎」段落的既有做法 | HIT。抽出 httpclient.py,跟 httpkit.py/sqlitekit.py 同一層 |
| sarch f3:tool_calls 由誰寫沒交代 | 讀原稿 | HIT。新增 instrumented.py 包裝層,dsp_client/inbox_client 保持不碰 TaskStore |
| sarch f4(minor):雜湊正規化有沒有沿用既有慣例 | 讀原稿 | HIT,一併在 s1f1 的折入中處理 |

補充:重跑 spec-gate,54 條全部句式合格、條款綁定通過。

refuted:none。

## 處置
- 12 條全部折入,沒有放行(有 blocker,規定不得放行)。

## 第 2 輪(r5)收貨與判讀
- 兩席(全新的 s1、架構對齊複驗 sarch)。normalize、quote-check 全數通過。
- 9 條確認真的修好(clean);5 條真的沒修好或有瑕疵。

| 宣稱 | 做法 | 結果 |
|---|---|---|
| r5s1f1:content_hash 演算法折入時文字沒真的寫進去 | 讀快照確認確實空白 | HIT,這次真的補上演算法文字(沿用增量 2 的正規化 JSON + SHA-256) |
| r5s1f2:X-Fault 的原始碼掃描可被拼接繞過 | 讀設計確認只有子字串比對 | HIT,改成「封閉列舉 + isinstance 核對」當真正防線,掃描降級為輔助訊號 |
| r5s1f3:tool_calls 的 task_seq 語意未定義 | 讀設計確認沒講 | HIT,定義成跟 evidence 表同一顆 row.seq,兩表用同一個鍵對起來 |
| r5s1f4(minor):落點段落文字瑕疵(三個 vs 五支) | 讀原文確認數字錯 | HIT,改成五支 |
| r5sarchf1:SubmitRejectedPermanently 定義層次前後矛盾 | 讀原文確認兩處說法不一致 | HIT,明講定義在 flow.py,inbox_client.py 只匯入使用 |

補充:重跑 spec-gate,54 條全部句式合格。

refuted:none。

## 第 2 輪處置
- 5 條全部折入,沒有放行(有 major,規定不得放行)。
