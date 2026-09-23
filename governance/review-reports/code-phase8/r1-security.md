severity: major

## F1 decision_created_at 全靠分析行程自報,DECISION_STALE(S511/S512)可被劫持的分析行程無限繞過

severity: major
blocking: 是 — 這是這次 Phase 8 新增的核心安全機制(決策新鮮度 15 分鐘),在「分析行程被劫持、提案內容算攻擊者可控」的威脅模型下形同沒有防護,而且沒有任何測試觸及這個角度,直接讓 [S511]/[S512] 想擋的「過時決策寫進 DSP」在版本沒變的情況下完全失守。

引句:「執行端看不到證據本身,用決策建立」

`guardrails.decision_stale`(`src/rtb/executor/guardrails.py`)的判斷是:

```python
def decision_stale(proposal: Proposal, now: datetime) -> bool:
    return now - proposal.decision_created_at > DECISION_FRESHNESS
```

`proposal.decision_created_at` 是提案內容的一個欄位,由分析行程在建立決策時填入(`src/rtb/analyzer/policy.py:88` `decision_created_at=now`),隨提案payload一起送進收件口。收件口收件時只驗證這個欄位跟「現在」的相對關係,而且只驗上界:

file: `src/rtb/executor/inbox_store.py:396-401`
```
    if proposal.decision_expires_at <= now:
        raise ProposalExpired()
    if proposal.decision_expires_at > now + MAX_DECISION_LIFETIME:
        raise ExpiryTooFar()
    if proposal.decision_created_at > now + CLOCK_SKEW:
        raise CreatedInFuture()
```
`CLOCK_SKEW` 只擋「建立時間比現在還晚太多」(`timedelta(minutes=5)`,見 `src/rtb/executor/inbox_store.py:48`),完全沒有下界檢查,也沒有任何地方拿收件口自己的時鐘(`received_at`,伺服器端可信)去核對 `decision_created_at` 是不是真的接近真實建立時間。

攻擊路徑:被劫持的分析行程送出的提案,`decision_created_at` 這欄本來就是攻擊者可控字串(反序列化格式檢查只驗「是不是一個能解析的時間」,見 `src/rtb/domain/proposal.py:175` 附近的欄位檢查),攻擊者只要每次送出新修訂(或甚至重放同一份)時把 `decision_created_at` 灌成「現在」(或現在減一點點),`decision_stale()` 就永遠回 False——不論這份決策背後依賴的證據、政策判斷實際上有多舊。三個判斷點(簽發之後 `Executor._stale`、開始一筆交易 `_stale_in`、重跑轉嘗試中 `_stale_on_rerun`)全部只吃這同一支 `decision_stale`,沒有第二支獨立、以伺服器時鐘為準的核對(對照:版本已變、政策已變兩項是跟 DSP/常數即時比對的權威值,拿不到假的;決策新鮮度這一項完全沒有這種權威錨點)。

這正好命中設計計劃裡「現況」一節自己承認的邊界(`docs/rtb-production-agent-demo-knowledge/Systems/執行迴圈.md`,非本次改動但一路沿用):執行端本來就「只看得到決策建立時間與到期時間」,Phase 8 是第一次把這個自報欄位用在一個新的安全門檔(S511/S512)上,但沒有補上任何抗偽造機制。換句話說:只要廣告版本、政策版本都沒變(F6 事故裡「證據已經改變但廣告與政策沒變」的那個子情境),被劫持的分析行程可以永遠讓一個實際上證據早就過時的決策看起來「剛建立」,新鮮度檻形同沒設。

沒有回頭條件(REVISIT)或機械測試涵蓋這個角度——`governance/review-reports/code-phase8/r1-snapshot-tests.patch` 裡沒有任何測試用「decision_created_at 被灌成現在但底層證據其實舊」這種情境去驗證新鮮度檻擋不擋得住,變異檢查也測不出「檢查邏輯本身信任的輸入被偽造」這類問題。

建議方向(僅供參考,不要求本輪就修):新鮮度判斷改成以收件口自己記的 `received_at`(伺服器時鐘,不是攻擊者能填的欄位)為基準之一,或至少對 `decision_created_at` 加下界比對(不能比 `received_at` 早太多也不能太晚太多之外,還要防止同一個實際決策被「重新蓋時間戳」後當新的送進來)。

---

以下是確認過沒有問題、或屬於既有設計已承認並接受的觀察,供覆核對照:

重放方法(`InboxStore.replay`/`_replay`/`_replay_refusal`,`src/rtb/executor/inbox_store.py:864-914`)整段查條件與寫回都在 `immediate_transaction` 裡完成,`_replay_refusal` 的四個拒絕原因(`NOT_IN_INBOX`/`NOT_DEAD_LETTER`/`EXPIRED`/`SUPERSEDED`/`INBOX_FULL`)覆蓋了「不是死信」「已過期」「被更新修訂取代」「待處理已滿」四種情況,兩人同時重放因為拿同一把寫入鎖只有一個能把 `disposition` 從 `dead_letter` 改回 `NULL`(UPDATE 的 WHERE 帶 `disposition = ?`),不會重複放回、也不會讓已被取代或已過期的死信插隊。重放本身不重簽、不碰 DSP、不讀租戶設定,放回之後完全走一般執行迴圈的既有檢查鏈(執行前檢查→簽發→新鮮度→比例→總曝險),沒有任何跳過檢查的旁路。

重放工具驗操作人格式用 `is_id`(`src/rtb/domain/_checks.py:12`,正則 `[A-Za-z0-9._:-]{1,128}`),只限制字元集與長度,不是身分驗證——但這跟既有核可管理工具同一個模式(`approval.py` docstring 已自己承認「核可是對稱簽章……擋不住有權限的人自己簽」),Phase 8 沒有讓這個既有的信任邊界變得更寬,不算新增的洞。稽核表 `dead_letter_ops` 的可變欄位只有 `operator`(受上述格式限制)跟固定列舉 `action`/`reason`,不會被灌入攻擊者任意字串(對照 `_BLOCK_CODES`/`_REPLAN_ON_BLOCK` 這種白名單機制,收件口回應的 `block_code` 不在白名單內就當「讀不懂」,不會原樣寫進只增不改的歷史表——這是既有的注入防線,Phase 8 的兩個新原因碼 `policy_version_changed`、`decision_stale` 也乖乖走這個白名單)。

所有新的 SQL(死信信封表、稽核表、`replay` 系列查詢)都用參數化 `?` 綁定,沒有任何字串拼接攻擊者可控值進 SQL 的地方;唯一用 f-string 組 SQL 的是既有的固定條件片段(`PENDING`/`OPEN`,标了 `# noqa: S608 - 固定條件`),不是這次新增,也不含變數輸入。

寫入鎖期間的新查詢(`_record_dead_letter`、`_replay`/`_replay_refusal`)都是靠 `(task_id, revision)` 主鍵或 `dead_letters_by_proposal` 索引的等值查找,沒有全表掃描;`_check_capacity` 沿用既有的兩個 `COUNT(*)`(其中一個帶 `WHERE state = 'pending' AND disposition IS NULL`,無索引),但這是既有的既有成本(每次 `submit` 就會付),重放只是多了一個呼叫點,受 `MAX_ROWS=5000` 上限限制,不是本次新增的效能坑。

有效核可豁免新鮮度那一段(`Executor._stale_in`)只認「這份提案(task_id+revision+content_hash 全等)、這一關、此刻仍算數」的核可(`approval.holds` 核對 content_hash、stage、scope_fingerprint、policy_version、到期、金額上限),不能拿別份提案或已被取代的舊核可套用;`_stale_on_rerun` 用「開始一筆時是否用過核可」判斷是否跳過新鮮度,但真正放行前 `_resign`/`_read_approval` 還是會重新核對那張核可此刻是否還在有效期內,不會讓一張已過期的核可繼續免檢查。

看過的檔:
- /Users/enzo/rtb-3b/src/rtb/analyzer/flow.py
- /Users/enzo/rtb-3b/src/rtb/analyzer/task_store.py
- /Users/enzo/rtb-3b/src/rtb/executor/execution.py
- /Users/enzo/rtb-3b/src/rtb/executor/guardrails.py
- /Users/enzo/rtb-3b/src/rtb/executor/inbox_store.py
- /Users/enzo/rtb-3b/src/rtb/executor/replay.py
- /Users/enzo/rtb-3b/src/rtb/executor/approval.py(審材外查證用)
- /Users/enzo/rtb-3b/src/rtb/domain/_checks.py(審材外查證用)
- /Users/enzo/rtb-3b/src/rtb/domain/proposal.py(審材外查證用)
- /Users/enzo/rtb-3b/src/rtb/analyzer/policy.py(審材外查證用)
