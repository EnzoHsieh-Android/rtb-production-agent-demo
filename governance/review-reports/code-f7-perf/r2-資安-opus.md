severity: major

## 第 1 輪驗收

| 第 1 輪條目 | 這輪改了什麼 | 驗收結果 |
|---|---|---|
| 1. 候選裡有沒記租戶的舊列時,退回原算法之前先白掃一遍已驗證列 | 加了便宜偵測 `legacy_candidate_query`:最多看 64 筆沒記租戶的第一列,找到候選就直接走原算法 | **只修好一部分**。卡住的舊列排在最新的 64 筆以內,才有效。超出 64 筆,退回路徑的虛擬機步數仍是原算法的兩倍多(見發現 1)。|
| 2. S19 綁定測試的匯入漏洞 | 新的 `connection_offenders` 先把相對匯入換成絕對名稱,再比對取出的名字,另外補上 `connect_read_only`、`begin_snapshot`、`read_snapshot` | **已修好**。第 1 輪列的 4 種繞法現在都寫成測試裡的探針,而且都判紅。只剩 `importlib.import_module` 加字串這種刻意繞法抓不到。這支測試本來就只防無心的寫法,不列為發現。|

## 發現 1:卡住的舊列只要比最新的 64 筆舊列還舊,退回路徑的工作量仍是原算法的兩倍
severity: major
blocking: 是

引句:「"WHERE f.seq = 1 AND f.tenant IS NULL ORDER BY f.written_at DESC LIMIT ?",」
引句:「LEGACY_PROBE_LIMIT = 64」
引句:「不像沒記租戶的舊列會長期留著(那種有獨立的便宜偵測,先查、查到直接走原算法)」
file: `src/rtb/executor/attempt_store.py:649`、`src/rtb/executor/attempt_store.py:662`

**問題在哪**
- 便宜偵測按第一列的時間,由新到舊只看 64 筆沒記租戶的列。
- 第 1 輪指出的情形正好不在這 64 筆裡:一把 Phase 6 之前開的鍵卡在轉人工,會長期留在未結案。
- 這把鍵通常比其他舊鍵早開。只要它之後、Phase 6 升級之前又開過 64 把以上的鍵,並且都已結案,偵測就看不到它。
- 結果又回到第 1 輪的路徑:先把窗內所有已驗證列加總一遍,發現舊列,再跑一次原算法。這些都在全域寫入鎖裡。

**文件和測試沒反映這個缺口**
- 驗收紀錄的〈殘餘風險〉寫成「舊列有獨立的便宜偵測、先查、查到直接走原算法」,好像這種情形已經解決。只有計劃的〈實作解讀〉提到超過 64 筆會判不出來,但沒寫出這時的成本。
- 新測試 `test_falling_back_on_a_legacy_row_does_no_more_work_than_the_reference` 把卡住的舊列放成最新的一筆(`at=NOW-1h`),剛好測不到這個情形。

**具體例子**
- 輸入:
  - 窗內有 2 萬筆 t-a 已驗證列。
  - 1 筆沒記租戶、轉人工、沒有終點的舊列,時間是 500 天前。
  - n 筆比它新、窗外已結案、沒記租戶的舊列。
- 預期:退回路徑的工作量不超過原算法(第 1 輪的判準;新測試斷言 `fast_steps <= slow_steps + 5`)。
- 實際:n ≥ 64 時,偵測判不出來,步數是原算法的 2.1 倍,時間慢 36%。

重現:在 `/tmp/資安-opus-f7cr2` 複本,借用 `test_aggregate_fast_path.py` 裡的 `Rows`、`written`、`_vm_steps` 寫了一支一次性測試(已刪除):

```
newer_closed_legacy=0   probe_seen=True  fast_steps=11000 ref_steps=11000 fast=0.0821s ref=0.0826s equal=True
newer_closed_legacy=64  probe_seen=False fast_steps=23058 ref_steps=11015 fast=0.1120s ref=0.0826s equal=True
newer_closed_legacy=65  probe_seen=False fast_steps=23057 ref_steps=11016 fast=0.1124s ref=0.0821s equal=True
newer_closed_legacy=500 probe_seen=False fast_steps=23257 ref_steps=11116 fast=0.1127s ref=0.0821s equal=True
```

**數額**
- 回傳值都跟原算法相同(`equal=True`)。
- 不會多放行,也不會多擋。問題只在握鎖時間。

**建議**
- 未結案的鍵全表最多 20 把(`MAX_UNRESOLVED`)。偵測「未結案裡有沒有沒記租戶的舊列」可以從未結案的鍵出發,不要從舊列按時間倒著讀。這樣工作量有上限,也不會漏掉。
- 如果維持 64 筆上限,要把「超過 64 筆就退化到第 1 輪的成本」照實寫進驗收紀錄的〈殘餘風險〉。新測試也要補一個「卡住的舊列比 64 筆已結案舊列還舊」的情形。

## 發現 2:租約時間讀不懂時,讀租約的函式會丟例外,不照說明回空值;碰上忙碌會讓執行迴圈直接崩潰
severity: minor
blocking: 否

引句:「return datetime.fromisoformat(row[0])」
引句:「except sqlite3.Error:」
file: `src/rtb/executor/inbox_store.py:1316`、`src/rtb/executor/runner.py:76`

**問題在哪**
- `lease_until` 只攔 `sqlite3.Error`。解析時間字串的錯誤沒攔:
  - 字串不是 ISO 格式,丟 ValueError。
  - 字串沒帶時區,在 `_lease_allows` 相減時丟 TypeError。
- 函式說明和計劃都寫「讀取出錯回空值」,S690 也寫「讀不到就不再試、照舊往外丟忙碌」。
- 收件表其他地方比較租約時間都是比字串,不解析。所以這是第一處讀到壞值就崩潰的地方。
- 執行迴圈的 `_loop` 只攔 `InboxBusy` 和 `ExecutorHalted`。ValueError 和 TypeError 會直接讓整個行程帶著堆疊結束,不是休息一輪。

**具體例子**
- 輸入:處理中那一列的 `lease_until` 是 `'garbage'` 或 `'2026-09-25T10:00:00'`(沒帶時區),同時寫結果時開交易鎖不到一次。
- 預期:讀不到就不再試,往外丟 `InboxBusyNotStarted`,執行迴圈休息一輪。
- 實際:丟 `ValueError: Invalid isoformat string: 'garbage'`,或 `TypeError: can't subtract offset-naive and offset-aware datetimes`,而且一次都沒退避。

重現:用 `BusyBegin` 加 `_worker`,在 DSP 寫入回呼裡改壞 `lease_until`,再造忙碌一次:

```
lease_until='garbage' -> raised ValueError: Invalid isoformat string: 'garbage' sleeps=[]
lease_until='2026-09-25T10:00:00' -> raised TypeError: can't subtract offset-naive and offset-aware datetimes sleeps=[]
```

**影響範圍**
- 要先有毀損資料,再剛好碰上忙碌才會觸發。
- 結果是停機,屬於保守失敗,不會多寫,也不會多放行。所以列為 minor。
- 建議把解析包進 try,解析失敗也回 None,或者丟既有的 `CorruptedInboxRow`。

## 重點題目的結論(沒找到可利用的洞)

**1. 設定檔快取能不能繞過安全讀檔?不能。**
- `read_tenants` 每次都在鎖內先跑 `_read_config_securely`,位元組完全相同才用快取。
- 程式裡沒有其他地方不經安全讀檔就讀 `_config_cache`。
- 這個檔第 1 輪之後沒再改過。

**2. 快路徑能不能被毀損資料騙成少算已用額度?不能。**
- 便宜偵測只會讓程式改走原算法,不會讓它跳過精確偵測。偵測漏判時,還有加總那一趟的精確偵測接著判。
- 這輪新加的型別偵測(時間欄與鍵不是文字)只會增加退回原算法的次數。
- 入口只收字串租戶。
- 兩種算法篩選列用的是同一份時間字串比較,時間格式的花招對兩邊一樣有效。
- 把差分隨機測試加大到 3 個種子、各 3000 份,包含新的型別異常、奇怪的租戶和中間狀態:新舊算法的回傳值或丟出的例外 0 份不同。
- 正式程式開始一筆時一定帶預留,新寫的列一定有租戶,攻擊者造不出新的沒記租戶的列。

**3. 重試能不能被外部造忙碌拖住,讓租約過期、被別人接手之後重複寫?不能。**
- 最多會被拖住約 20 秒。
- 每次重試都是一個新交易,第一步就用條件寫入續租,要求租約序號、擁有者都對得上,而且還沒到期。
- 別人接手也要拿全域寫入鎖,不可能跟我們握鎖時的核對交錯。
- 實測:在租約判斷通過之後、下次開交易之前,模擬別人接手(序號加 1、擁有者換人)。結果是 `result=lease_lost`,嘗試紀錄沒多寫,DSP 還是 1 次寫入,收件表照舊。
- 重試不會重新呼叫 DSP。

**4. `lease_until` 能不能讀到別人的租約?不能。**
- 查詢條件跟續租用的 `_held` 完全一樣,只少了時間條件:任務、版次、內容雜湊、處理中、租約序號、擁有者都要相符。
- 租約序號在每次取件和接手時都加 1,不會重置。就算擁有者字串相同,也對不上別人的序號。
- 讀到已過期的自己的租約,剩餘時間是負的,照樣判定不再試。

實驗都在 `/tmp/資安-opus-f7cr2` 複本裡做,已刪除;沒有改動被審的工作樹,也沒有留下行程。

## 看過的檔(r2-snapshot.patch 全份,含 r2-delta.patch)
- `/Users/enzo/rtb-f7-rev/governance/review-reports/code-f7-perf/r2-snapshot.patch`
- `/Users/enzo/rtb-f7-rev/governance/review-reports/code-f7-perf/r2-delta.patch`
- `/Users/enzo/rtb-f7-rev/claims/aggregate-blast-radius.json`
- `/Users/enzo/rtb-f7-rev/claims/concurrency.json`
- `/Users/enzo/rtb-f7-rev/claims/idempotency-unknown-outcome.json`
- `/Users/enzo/rtb-f7-rev/claims/permission-guardrail.json`
- `/Users/enzo/rtb-f7-rev/claims/prompt-injection.json`
- `/Users/enzo/rtb-f7-rev/docs/rtb-production-agent-demo-knowledge/Issues/F7端到端在CI上偶爾超過60秒.md`
- `/Users/enzo/rtb-f7-rev/docs/rtb-production-agent-demo-knowledge/Projects/F7效能_計劃.md`
- `/Users/enzo/rtb-f7-rev/docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md`
- `/Users/enzo/rtb-f7-rev/docs/rtb-production-agent-demo-knowledge/Systems/共用行程基礎.md`
- `/Users/enzo/rtb-f7-rev/docs/rtb-production-agent-demo-knowledge/Systems/執行迴圈.md`
- `/Users/enzo/rtb-f7-rev/docs/rtb-production-agent-demo-knowledge/Systems/外部寫入嘗試紀錄.md`
- `/Users/enzo/rtb-f7-rev/docs/rtb-production-agent-demo-knowledge/Systems/寫入能力憑證.md`
- `/Users/enzo/rtb-f7-rev/docs/rtb-production-agent-demo-knowledge/Systems/提案收件口.md`
- `/Users/enzo/rtb-f7-rev/docs/rtb-production-agent-demo-knowledge/Verification/F7效能驗收紀錄.md`
- `/Users/enzo/rtb-f7-rev/src/rtb/executor/attempt_store.py`
- `/Users/enzo/rtb-f7-rev/src/rtb/executor/capability_signer.py`
- `/Users/enzo/rtb-f7-rev/src/rtb/executor/execution.py`
- `/Users/enzo/rtb-f7-rev/src/rtb/executor/inbox_store.py`
- `/Users/enzo/rtb-f7-rev/src/rtb/executor/runner.py`
- `/Users/enzo/rtb-f7-rev/src/rtb/sqlitekit.py`
- `/Users/enzo/rtb-f7-rev/tests/executor/test_aggregate_fast_path.py`
- `/Users/enzo/rtb-f7-rev/tests/executor/test_approval.py`
- `/Users/enzo/rtb-f7-rev/tests/executor/test_attempt_store.py`
- `/Users/enzo/rtb-f7-rev/tests/executor/test_config_cache.py`
- `/Users/enzo/rtb-f7-rev/tests/executor/test_read_only.py`
- `/Users/enzo/rtb-f7-rev/tests/executor/test_result_write_retry.py`
- `/Users/enzo/rtb-f7-rev/governance/review-reports/code-f7-perf/r1-資安-opus.md`

2 條,blocking 1。
