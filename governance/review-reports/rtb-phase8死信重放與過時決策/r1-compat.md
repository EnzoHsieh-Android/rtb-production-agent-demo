severity: major

## F1 決策新鮮度檢查會在核可放回待處理後把已核可的提案當場擋下,核可等於白簽

severity: major
blocking: 是 — 照字面實作,增量 3 的人工核可機制對任何需要真人審閱時間(現實上幾乎都會超過 15 分鐘)的個案會系統性失效,而且失效原因對操作者不可見(顯示的是「決策已過時」,不是「核可不算數」)。

引句:「順序:放進看 DSP 現況的硬規則(執行前檢查),排在「版本已變」之後;增量 3 之後的流程是硬規則先判、再簽發、再判比例上限(可核可),所以兩項新檢查都在簽發與比例之前。」

引句:「兩項對所有提案都做,不只對重放做:死信佇列不是第二條通道,重放與正常投遞走同一道閘;正常情況下提案從送出到被取件只要幾秒,不受影響;DSP 長時間中斷後才被取件的提案,本來就該被當成過時。」

程式碼核對(`/Users/enzo/rtb-production-agent-demo`,非重放路徑,一般核可流程本身即成立):
- `src/rtb/executor/execution.py:404`:`signed = precheck(proposal, view) or self._sign(proposal)`——precheck 排在簽發與 `_gate`(比例/總曝險)之前,每次「取件→處理一筆」都會重跑,包括核可放回待處理後被重新取件的那一次。
- `src/rtb/executor/execution.py:529-546`:`_settle_awaiting` 只驗核可是否 `holds()`(`src/rtb/executor/approval.py:114-121`),不看、也不能重設 `decision_created_at`;放回待處理後,提案物件不變,`decision_created_at` 仍是分析端當初蓋的時間。
- `src/rtb/executor/execution.py:384-408`(`process_one`/`_process`):下一輪把它從待處理取出時,重新完整跑一次 `precheck()`(含新增的兩項),沒有任何「這筆已經核可過,免驗新鮮度」的旁路。
- `src/rtb/executor/approval.py:issue`(第 88-101 行)與 `holds`(第 122-131 行):核可到期 `expires_at` 硬性限制 `<= proposal.decision_expires_at`(即分析端蓋的 `decision_created_at + DECISION_LIFETIME`,demo 政策 `src/rtb/analyzer/policy.py:23` 定為 30 分鐘);換句話說,核可制度本身允許核准動作發生在決策建立後最長接近 30 分鐘之內都合法、都拿得到還沒過期的核可。
- 對照:新檢查的門檻是「現在 − 決策建立時間 > 15 分鐘」就擋下(spec S505),與核可自己的 30 分鐘上限沒有任何關聯或退讓機制。

**實際影響換算成數字**:
- 待核可能等多久(現況,增量 3 既有邏輯):上限是 `proposal.decision_expires_at`,demo 政策下等於「決策建立後 30 分鐘」;超過就在 `process_awaiting`(`execution.py:534`)被判 `EXPIRED`,不需要新檢查介入。
- 核可有效期:由發卡人(`issue()` 呼叫端)自訂 `issued_at`/`expires_at`,唯一硬限制是不得晚於 `decision_expires_at`;理論上核可可以核發在決策建立後第 29 分鐘、有效到第 30 分鐘。
- 提案有效期(demo 政策):30 分鐘。
- 兩項新檢查生效後,「待核可」這條路實際只剩「決策建立後 15 分鐘內完成核可**且**被下一輪取件重新跑過 precheck」才用得上;超過 15 分鐘核可、或核可雖在 15 分鐘內完成但排隊等下一輪取件時已經跨過 15 分鐘,precheck 一律先擋成「決策已過時」,原本 30 分鐘的等待預算被砍半以上,而且砍多少完全不透明(取決於執行迴圈輪詢間隔)。
- 這件事目前的「實務隱患」清單只提到 DSP 中斷情境(「DSP 中斷超過 15 分鐘後才取到的提案會被當成過時、重新規劃」),沒有提到核可放回待處理這條路徑會被同一個檢查攔下;核可流程本來的設計目的(讓人可以override量的護欄)在多數真實審批耗時下會被這個新檢查悄悄蓋掉。

**可能的處理選項(不代替使用者選)**:
1. 維持現狀字面實作(兩項新檢查對放回待處理的核可提案照樣生效):後果是核可機制的實際可用窗口被壓縮到遠低於 30 分鐘、且上限不透明,大多數需要人工審閱時間的核可會在下一輪取件時失敗,分析端要重新規劃、人要重新走一次核可,原本的核可成本白費;好處是規則統一、precheck 不必分流、no特例。
2. 核可放回待處理的那一次「取件→precheck」跳過或重新定義新鮮度基準(例如改用核可核發時間 `approval.issued_at` 或放回待處理的時間,而不是 `decision_created_at`,只針對「因核可而放回」這條路):能保住核可機制的實際效力,但需要修改 precheck 的呼叫介面或另開一個變體,打破「兩項新檢查對所有提案一視同仁、precheck 只有一支函式」的簡單性,且要另外定義這個例外的安全邊界(例如仍要求核可本身在 `decision_expires_at` 內有效,只是不因為 15 分鐘新鮮度而多擋一次)。
3. 把「決策新鮮度」與「決策有效期」的關係明訂為耦合(例如新鮮度上限與該提案的 `decision_expires_at` 或核可流程掛鉤,而非寫死 15 分鐘常數):可以避免核可流程被意外腰斬,但偏離了使用者已裁定的「15 分鐘、對所有提案生效」的取捨,且 15 分鐘的理由(「跟分析端的證據年齡上限同一個值」)是證據新鮮度的類比,不是核可等待時間的類比,兩者混在一起需要重新論證。
4. 縮短核可流程本身的容許等待、或明確要求核可要在決策建立後 15 分鐘內完成並被下一輪取件消化(把限制寫進操作規範,而不是程式邏輯):不改程式,但等於把「30 分鐘決策有效期」對核可情境實質改成不到 15 分鐘,需要另外告知核可操作者、且輪詢延遲仍會吃掉這個窗口,無法保證。

## F2 憑證過期後重讀這條重跑路徑,新兩項檢查一旦命中且是首次送出,會在不查 DSP 操作紀錄的情況下直接判定「沒發生」

severity: major
blocking: 是 — 這條路徑目前的「送過一次就不作廢直接判失敗」邏輯,原本命中的原因都來自剛讀到的 DSP 現況(廣告不存在/未投放/版本已變);兩項新檢查一旦併入同一支 `precheck()`,會多出兩種與 DSP 是否真的套用該次寫入完全無關的觸發原因,卻沿用同一個「不查證直接判沒發生」的處置。

引句:「決策新鮮度:現在減決策建立時間超過 15 分鐘(使用者 2026-09-23 裁定)就擋下,原因「決策已過時」(decision_stale)。執行端看不到證據本身,用決策建立時間代替:分析端決策時證據最多 15 分鐘,所以證據年齡最多 30 分鐘。」

引句:「政策版本:提案的政策版本不等於現行版本(`rtb.domain.proposal.POLICY_VERSION`)就擋下,原因「政策已變」(policy_version_changed)。」

程式碼核對:
- `src/rtb/executor/execution.py:782-793`(`_after_expiry`,憑證過期後重讀):`checked = precheck(proposal, view)`;業務上不過時呼叫 `self._not_resent(proposal, row, receipt, _version_changed_or_none(live, checked))`。
- `src/rtb/executor/execution.py:326-331`(`_version_changed_or_none`):只有 `checked is BlockCode.VERSION_CHANGED` 才回傳非空的 block_code,新增的 `policy_version_changed`、`decision_stale` 都會落到回傳 `None` 這一支。
- `src/rtb/executor/execution.py:808-816`(`_not_resent`):`if row.send_count > 1: return self._void_then_fail(...)`;`send_count == 1` 時直接 `self._write(row, A.FAILED, receipt, code=C.NOT_HAPPENED, block_code=block)`,完全不呼叫 `self.dsp.operation_record(...)` 或任何作廢動作。
- `_after_expiry` 是在 `_record()` 的 `reaction.capability_expired` 分支同步呼叫(`execution.py:775-776`),不經過 `reconcile_all()`/`_reconcile_unknown()`(`execution.py:930-940`)那條「先查 `operation_record` 再決定」的路徑;一旦這裡把 row 寫成終態 `FAILED`,`reconcile_all()` 只掃 `unresolved_keys`/`in_progress_keys`(`execution.py:866-867`),終態的鍵不會再被撿回去對帳。
- 對照組:同樣情境下 `_reconcile_not_found`(`execution.py:942-969`)在業務不過時一律走 `_void_then_fail`(不論 send_count),會先確認 DSP 一定不會提交這把鍵才判失敗;`_after_expiry` 的 `send_count == 1` 分支沒有這層保護。

**已送 DSP 的怎麼辦(依字面實作)**:`_after_expiry` 觸發的前提就是「DSP 明確沒寫」判定不出來(`capability_expired`,結果不明)且這是第一次送出。若此時 `checked` 命中的是 `policy_version_changed` 或 `decision_stale`(純粹因為政策常數不同步、或純粹因為牆鐘時間超過 15 分鐘,兩者都與 DSP 那筆寫入是否已套用無關),系統會直接把這筆結果不明的嘗試判成 `NOT_HAPPENED` 並確認結案,不再有任何後續機制去問 DSP「這把鍵到底寫進去了沒」。如果 DSP 端其實已經套用,帳面上會少記一筆已生效的預算異動(對照 F4 事故——蓋掉別人較新現況——是相反方向的疏漏:這裡是漏記自己已生效的寫入),而且因為狀態已是終態,不會再被對帳追回。

## 決策建立時間由分析端填、政策版本部署視窗、時鐘偏差:目前程式碼與 spec 的既有機制

以下三點依 spec 與程式碼核對,沒有發現字面實作會產生錯誤行為(不算 ## F),列成觀察供併入判斷:

**政策版本部署視窗**(對應第 3 點):`POLICY_VERSION` 定義在 `src/rtb/domain/proposal.py:25`,分析端 `src/rtb/analyzer/policy.py` 與執行端 `src/rtb/executor/approval.py` 都從同一個模組匯入,是同一份原始碼裡的同一個常數,不是兩端各自維護的設定值。但分析行程與執行行程是分開部署的行程(spec「現況」段:「執行端一般流程不驗政策版本……只有核可那一關會比對」),滾動式部署下必然會有一段視窗兩邊實際載入的常數值不同。這個特性在增量 3 就已經存在(核可的 `scope_fingerprint` 已把 `POLICY_VERSION` 算進雜湊,`src/rtb/executor/approval.py:56-65`,版本不同步時舊核可已經會失效),新檢查只是把同一種「版本不同步就擋下」的效果從「只影響核可」擴大到「影響所有提案」,屬於既有失效模式（fail closed）的延伸,不是新的行為類別。部署視窗內會發生的具體現象:哪一端先升級,用舊版常數的那一端會把對端用新版常數蓋出來的提案(或反過來)一律判成 `policy_version_changed` 擋下,直到兩端版本重新一致——spec 的「回退」與「實務隱患」段目前沒有寫到這個部署排序的操作面後果,值得在落地時一併交代,但不構成字面實作的行為錯誤。

**時鐘偏差與未來時間**(對應第 4 點):收件口既有的 `CLOCK_SKEW = timedelta(minutes=5)`(`src/rtb/executor/inbox_store.py:44`)只在**收件當下**擋下 `decision_created_at` 比現在晚超過 5 分鐘的提案(`CreatedInFuture`,`inbox_store.py:243`);一旦收下,`decision_created_at` 不會再變。新鮮度檢查算的是「執行端當下時鐘 − decision_created_at」:如果分析端時鐘合法地領先執行端(在 5 分鐘容許範圍內),執行端算出來的年齡會比真實經過時間少,等於把 15 分鐘的門檻不知不覺放寬到最多 20 分鐘;因為容許的偏移量有界(5 分鐘,已被收件口擋住),不會讓新鮮度檢查整個失效,也不會出現「該過時卻永遠不過時」或「還沒過時卻被誤擋」的情況——只會讓門檻比文件講的 15 分鐘略寬,寬多少取決於當時分析端與執行端的實際時鐘差。spec 沒有明講這個交互作用,但威脅模型明講「防忘記,不防繞過」,這個量級的寬限落在既有設計容忍範圍內,不構成字面實作的錯誤行為。
