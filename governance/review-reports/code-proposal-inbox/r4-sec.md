severity: minor

# code-proposal-inbox r4 資安最終驗收(攻擊者視角)

## 結論
- 第 3 輪那條 major(取代鏈無限寫入)已修好:磁碟寫入現在有硬上限(5000 列,每列 payload 最多 16KB,整表最壞約 80MB)。
- 沒有找到越權寫入或繞過驗證。三道新界限沒有讓被清掉的舊提案被當新提案重收,也沒有讓修訂連號倒退。
- 剩兩條 minor,都只影響分析行程自己的流程,或影響後續 Phase 3 的設計前提。
- 驗證方式:把專案複製到自己的臨時目錄,用可控時鐘起真的 InboxServer,以真實 HTTP 請求實測。

## 實測過、沒有問題的項目
- 修訂上限:同一任務送到第 51 版回 409 too_many_revisions,highest_revision=50。
- 修訂連號:清理只刪整個任務,沒有出現序號倒退或跳號。
- 清理後重送:舊提案(有效期 1 小時)在 2 小時加 1 秒後重送,回 422 expired_proposal。
  在 received+2h 邊界也是 422。因為 RETENTION(2h) 大於最長有效期(1h),不能重收。
- 清理不動待處理的任務:有一列待處理就不刪。
- 時間組合:
  - 建立時間 +5 分鐘且有效期 1 小時,回 422 expiry_too_far。
  - 建立時間 +5 分 1 秒,回 422 created_in_future。
  - 建立時間 now-3599 秒,有效期 1 小時,合法(過去的建立時間被「到期須晚於現在」與「有效期不超過 1 小時」夾住)。
  - 2000 到 2100 的範圍在這兩道限制下沒有可繞過的組合(created 最遠 now+5m,expires 最遠 now+1h,最早 now-1h)。
- 同一時刻的不同時區寫法得到同一個雜湊,重送回 200 replayed,不會多寫。
- 額外檢查:Host、Origin、Content-Type、Content-Length 與 X-Fault 的處理沒有繞過路徑。全部走 httpkit,拒絕都在讀資料庫之前。

### 1. 一次 12 秒的爆發就能把收件口塞滿約 2 小時,且可持續接力
severity: minor
blocking: 否 只癱瘓分析行程自己的流程,沒有越權寫入,也沒有繞過驗證;與作者已承認的名額佔滿同屬一類,回復時間有上限且會自己恢復。
引句:「MAX_ROWS = 5000  # 收件表總列數上限:取代鏈不佔名額,沒有這個上限就能無限寫入吃光磁碟」
實測(無認證的單一呼叫者):
- 100 個任務、每個 50 修訂,每筆有效期只有 0.01 秒,共 5000 筆全部收下(201),耗時 11.7 秒。
- 每筆過期後待處理數為 0,所以 8 個待處理名額擋不住;總列數卻達 5000。
- 之後所有新任務回 503 inbox_full(retryable:true)。
  - 到 118 分鐘仍全滿,121 分鐘才恢復(5000 列降到 1951 列)。
- 清理只看最後一次收件時間,攻擊者每小時補一批就能無限期維持。相比之下,原本佔 8 個待處理名額要每小時續約,這個路徑更省力。
- 同類:對某個任務編號灌到 50 修訂,該任務編號在 2 小時內不能再修訂。
- 與作者已承認的缺口是同一類,不升級。影響是「合法提案被長期擋住」。
- 建議(不擋放行):Phase 3 加呼叫者配額。過渡期可以考慮把 inbox_full 的恢復條件改成「已過期列可提前回收」。已過期列在 RETENTION 前不能刪,是為了「重送必為過期」;若改成只留 (task_id, 最高修訂) 一筆墓碑列,就能兼顧。
file: `src/rtb/executor/inbox_store.py:217`

### 2. 清理之後同一個(任務編號, 修訂)可以用不同內容再次被收下
severity: minor
blocking: 否 收件口本身不執行任何東西,「同一個收件鍵內容不變」只在保留期內成立;風險在 Phase 3 的執行端是否把這個鍵當永久唯一。
引句:「只清整個任務,不清單一修訂:修訂連號是拿最高修訂算的,清一半會讓序號倒退。保留期限比」
實測:
- 任務 z 的 revision 1(預算 100)收下,2 小時多之後被清掉。
- 這時 z 的 revision 2 回 409 revision_out_of_order,highest_revision=0。
- z 的 revision 1 改成預算 999 卻回 201 收下。
- 分析行程被誘導後,可以在等 2 小時之後,用同一個任務編號與修訂號送出完全不同的內容。
- 正常的呼叫者想在 2 小時後續改舊任務,會被迫從 1 重來。
- 這是設計上的取捨,但需要寫成 Phase 3 的前提:
  - 執行前授權、冪等鍵不可以只用(task_id, revision)。
  - 至少要帶上 content_hash,或用收件流水號。
- 建議:在共用行程基礎/提案收件口節點的已知缺口加一行,附回頭日期。
file: `docs/rtb-production-agent-demo-knowledge/Systems/提案收件口.md:1`

## 已讀檔案
- file: `/Users/enzo/rtb-production-agent-demo/governance/review-reports/code-proposal-inbox/r4-snapshot.patch:1`
- file: `src/rtb/domain/proposal.py:1`
- file: `src/rtb/executor/inbox_server.py:1`
- file: `src/rtb/executor/inbox_store.py:1`
- file: `src/rtb/executor/ruff.toml:1`
- file: `src/rtb/dsp/ruff.toml:1`
- file: `src/rtb/dsp/server.py:1`
- file: `src/rtb/httpkit.py:1`
- file: `src/rtb/sqlitekit.py:1`
- file: `tests/domain/proposal_samples.py:1`
- file: `tests/domain/test_proposal.py:1`
- file: `tests/domain/test_proposal_hash.py:1`
- file: `tests/executor/conftest.py:1`
- file: `tests/executor/test_inbox_server.py:1`
- file: `tests/kit/test_httpkit.py:1`
- file: `tests/kit/test_shared_base.py:1`
- file: `tests/kit/test_sqlitekit.py:1`
