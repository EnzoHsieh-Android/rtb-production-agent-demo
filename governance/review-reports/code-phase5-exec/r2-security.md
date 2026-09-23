severity: clean

# 資安鏡頭審查(r2-delta,phase5-exec)

範圍:`/Users/enzo/rtb-3b` 的 `r2-delta.patch`(`src/rtb/executor/execution.py`、
`src/rtb/executor/inbox_server.py`、對應測試)。攻擊者:被劫持的分析行程(能送任意提案、能重送、
能對任意廣告編號送)、能改廣告的另一方。

## 背景:這輪修的是什麼洞

r1 資安席在 `r1-security.md` 判過 major/blocking 的 F1:收件口回應把 `block_code` 原樣回傳,
`over_budget_cap` 與 `campaign_not_allowed` 兩個值直接外顯,讓送提案的一方(分析行程)可以對同一
`campaign_id` 反覆送不同 `new_budget` 二分逼近出租戶的 `max_budget` 精確值,或對任意 `campaign_id`
送一次觀察 `campaign_not_allowed` 探出廣告歸屬。這次 delta 就是修這個洞:把這兩個值在回應本文合併
成 `not_permitted`,收件表內部仍記細分代碼。逐點覆核如下。

## 檢查 1:除了回應本文的 block_code,還有沒有別的管道能反查預算上限或廣告歸屬

**結論:沒找到,clean。**

- 收件口只有一個 HTTP 端點,`inbox_server.py:80` 的 `handle_request` 只接受
  `POST {PROPOSALS_PATH}`,其他方法或路徑一律 404;沒有另開查詢端點回報任務狀態或統計。
- `block_code` 進回應只有一個出口:`_accepted_body`(`inbox_server.py:142-149`)。首次收下
  (201)與重送(200)共用同一段程式碼(`inbox_server.py:103`:
  `return (200 if result.replayed else 201), _accepted_body(result)`),都經過
  `_answered_block_code` 這道閘,沒有繞過閘直接讀 `result.block_code` 的第二條路。
  引句:「"block_code": _answered_block_code(result.block_code)}」
- 拒收(409/422/503)的回應體(`REJECTION_STATUS`、`error_extras`)不含 `block_code`,只有
  `error` 代碼與 `highest_revision`,跟權限判斷無關,不構成另一個 oracle。
- HTTP 狀態碼不分流:不管擋下原因是哪一種,已接受但被擋的提案一律走「accepted 家族」
  的 200/201,不會因為是 `over_budget_cap` 還是 `campaign_not_allowed` 而回不同狀態碼。
- 死信(`dead_letter_reason`)是獨立的封閉列舉(只有 `delivery_limit`),不含權限類原因,
  也沒有被接進 `_accepted_body`,不是另一個外洩管道。
- 內部驗證:`test_a_resend_hides_which_permission_blocked_it`(`test_version_conflict.py`)
  把 `OVER_BUDGET_CAP`、`CAMPAIGN_NOT_ALLOWED` 直接寫進收件表,斷言回應本文都是
  `not_permitted`,同時斷言 `SELECT block_code FROM proposals` 仍是細分代碼(稽核不失真)。
  本地重放邏輯確認實作與測試一致,合併點是單一函式 `_answered_block_code`,沒有第二處判斷
  分岔。

殘留但屬於 r1 已知、這次 delta 沒動也不必動的推論性小洞(F2,r1 判 minor,不擋):`attempts`
表本身只增不改、沒有清理機制,另一方反覆觸發版本衝突可以無上限增列,但不繞過執行閘、也不透過
本次改動的回應欄位外顯,維持 minor、非本輪範圍。

## 檢查 2:新增的兩條「重跑檢查回版本已變」路徑,能不能被另一方拿來讓分析端無限重新規劃或放大成本

**結論:沒找到新增的可利用洞,clean。**

- 新函式 `_version_changed_or_none`(`execution.py:149-153`)只吃 `precheck()` 的回傳值,而
  `precheck()`(`execution.py:138-146`)只會回 `CAMPAIGN_NOT_FOUND` / `CAMPAIGN_NOT_ACTIVE` /
  `VERSION_CHANGED` / `None` 四種——权限類代碼(`over_budget_cap`、`campaign_not_allowed`)是
  之後 `_sign()`/`capability_signer` 才算出來的,`precheck()` 根本碰不到,所以這兩條新路徑
  結構上不可能把權限資訊誤標成版本已變、也不可能把版本已變誤標成權限類,兩類原因在型別層就
  分開,不會因為這次改動而互相污染。
- 這兩條新路徑(`_after_expiry` 的憑證過期重讀、`_missing_key`/對帳的查不到鍵重讀)本來就只在
  既有重試窗口內觸發(憑證到期後的一次重簽、或 DSP 對帳查不到操作紀錄時的一次重讀),不是新開
  的可由送提案的一方直接觸發的端點;要讓這裡回報「版本已變」,前提仍是廣告的版本在這個窄窗口內
  真的被改過(即威脅模型裡「能改廣告的另一方」的正當寫入能力),跟原本三處(終點確認、開始一筆
  撞鍵、取件撞鍵)本來就能觸發版本已變是同一種能力,不是新賦予的權力,只是修正了之前這兩條路徑
  誤標成「同一操作先前已失敗」導致分析端漏重新規劃的 bug(這正是 r1 外家席抓到、要補的洞:
  之前是漏防,不是這次新增攻擊面)。
- 放大成本的上限不在這支 diff 裡,但也沒被這支 diff 動到:回應文件與計劃筆記都寫明「代數上限不
  分原因」「清掉之後統一重新規劃…最多 3 代」,規劃計劃裡「不做的事」也明講「不做分析行程的啟動
  程式與多任務排程」,重新規劃次數本來就有界,這次 delta 沒有新增或移除任何代數判斷,單純換一個
  block_code 標籤,不影響既有的重新規劃代數上限。
- 併發測試修正(執行緒例外收回主執行緒,`test_version_conflict.py` 的 `run()`)只是讓測試斷言
  失敗時看得到真正原因,不改變任何執行期行為,不構成資安面的新增或移除防護。

## 小結

r1 F1 的攻擊路徑(靠回應本文的 `over_budget_cap` / `campaign_not_allowed` 反查租戶預算上限與廣告
歸屬)已被單一、無分岔的合併點堵住,且沒找到繞過這個合併點的第二管道(別的端點、狀態碼、死信欄位、
時間差都排查過,没有可觀察差異)。新增的兩條版本已變重跑路徑在型別上就與權限類原因分開,不會重新
打開 F1,也沒有繞過既有的重新規劃代數上限造成成本放大。此輪沒有新的可利用洞,判 clean。
