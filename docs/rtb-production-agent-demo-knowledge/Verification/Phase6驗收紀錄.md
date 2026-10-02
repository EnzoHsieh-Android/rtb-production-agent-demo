---
type: verification
status: pass
date: 2026-09-24
valid_under: "Python 3.14.6 與 sqlite3 3.53.3、pytest 9.1.1、ruff 0.16.8,macOS 本機;程式版本為提交 078e7e8(合併事故 F7 轉正、增量 4 查詢三之後);總曝險 24 小時窗口、比例上限五成與最小加額 1、全表未結案 20 把都是暫用值;核可是對稱簽章;分析端與執行端同一次部署一起重啟"
revalidate_when: "改動開始一筆的額度計算、執行前檢查與比例判斷的順序、核可有效判斷或範圍指紋、處理待核可那一步、簽發器的租戶設定或最晚到期、分析端的寫入掃描時重跑全套;調整窗口長度、比例或門檻時重驗 F7 端到端;核可改成非對稱簽章時重驗核可金鑰相關合約;Phase 8 的新檢查合進來時重驗核可放回的例外"
tags:
  - type/verification
  - status/pass
plan_refs:
  - "[[Projects/RTB_Phase6權限護欄與總曝險_計劃]]"
---
# Phase6驗收紀錄

驗證對象:[[Systems/執行迴圈]]、[[Systems/寫入能力憑證]]、[[Systems/提案收件口]]、[[Systems/外部寫入嘗試紀錄]]、[[Systems/可觀測查詢]]、[[Systems/分析行程流程與檢查點]];依據計劃 [[Projects/RTB_Phase6權限護欄與總曝險_計劃]]。

## 結論

Phase 6 完成:四個增量(總曝險預留、單筆護欄表、人工核可、可觀測查詢)各自設計審與代碼審跑完,都已合併推送;事故 F7 的預告合約照實改寫原文後轉正、搬到 [[Systems/執行迴圈]],綁 15 支測試與 15 條殺傷力配方,獨立審計第五次同意。下面的缺口與偏離如實標明,不宣稱比這更多。

## 對照交接文件 Phase 6 完成條件

- 沒有寫入能力的分析行程呼叫不了寫入:分析行程讀不到簽發金鑰與核可金鑰、也匯入不了簽發模組;分析行程原始碼的匯入閉包裡,共用 HTTP 用戶端的請求函式只准用在查 DSP 與送提案兩處;DSP 端沒有憑證一律拒收。[test:test_the_analyzer_can_neither_read_the_signing_key_nor_import_the_signer]、[test:test_the_analyzer_has_no_write_call_besides_submitting_a_proposal]
- 護欄以表格驅動測試涵蓋邊界值:六條單筆規則(廣告不存在、未投放、版本已變、不屬於租戶、超過單一廣告上限、比例上限)在剛好通過、差 1、剛好超過三組邊界上逐列驗證,順序也釘住(硬規則一律先判,可核可的比例排最後)。[test:test_every_guardrail_holds_at_its_boundaries]、[test:test_the_first_failing_guardrail_wins]
- 核可在範圍、到期、提案雜湊改變時失效:核可綁提案內容雜湊、任務與修訂、關卡、金額上限、到期,以及範圍指紋(租戶名稱與三欄設定、比例三個常數);提案的政策版本要等於目前版本;範圍指紋七樣材料各自改動都讓舊核可失效。[test:test_an_approval_is_void_when_scope_expiry_hash_or_stage_changes]、[test:test_an_approval_is_void_once_any_scope_field_changes]、[test:test_an_approval_is_void_across_policy_or_tenant_changes]
- F7 在並行工作者下也不能超額:8 個工作者執行緒、3000 個廣告各加一成,DSP 收到的加預算總額不超過門檻;兩個工作者用柵欄逼出競態,只有一方通過。[test:test_f7_many_small_increases_stop_at_the_aggregate_limit]、[test:test_two_workers_cannot_race_past_the_aggregate_limit]
- 總曝險擋下、人工核可與額度使用率可觀測:停下次數(依租戶、廣告、時間篩)、表滿延後份數、待核可份數與已核可放行次數、額度使用率、稽核明細都是唯讀查詢,有索引、不寫任何東西。[test:test_aggregate_stop_count_filters_by_tenant_campaign_and_time]、[test:test_approval_counts_cover_waiting_and_applied]、[test:test_utilization_matches_the_reservation_ledger]、[test:test_observability_queries_write_nothing]

## 怎麼驗的(2026-09-24)

- 驗收指令 `.venv/bin/python -m pytest -q`:1480 條通過;`ruff check`、`mypy src` 乾淨;推送後 CI 綠燈。
- 設計審:增量 1、3、4 各三輪,增量 2 依規則跑完;卷證在 governance/review-reports/rtb-phase6權限護欄與總曝險-增量1 到 -增量4。
- 代碼審:增量 1、3 各 high 三輪(本工作區),增量 2、增量 4、查詢三各自在另一個工作區跑完;卷證在 governance/review-reports/code-phase6-*。增量 3 三輪共 18 條全折,第 3 輪的修正沒有再審。
- 變異檢查:增量 1 十六道加十一道、增量 3 四十三道加 6、5、8 道、增量 4 二十道,每道拿掉防護後對應測試翻紅;事故 F7 十五條殺傷力配方由 `lumos guard kill` 在隔離副本跑過,全部翻紅。
- 事故 F7 轉正:獨立審計五次,前四次分別指出「分批」缺端到端測試、核可到期邊界沒人守、政策與租戶兩種失效沒綁、範圍指紋材料只驗到一部分,都補上後第五次同意;卷證 governance/review-reports/rtb-phase6權限護欄與總曝險-增量3/f7-audit-1.md 到 f7-audit-5.md。

## 偏離與缺口

- 核可是對稱簽章:執行端也讀得到核可金鑰、能自己簽;照「防忘記不防繞過」,不防有權限的人繞過(計劃實務隱患附回頭日期)。
- 政策版本是分析端與執行端共用的程式常數;兩個行程分開啟動,只重啟一邊時版本可能暫時不同,靠部署時一起重啟(計劃實務隱患附回頭條件)。
- 用不到的總曝險核可在一個窄窗裡會壓短憑證效期;後果有界,DSP 回憑證過期時照既有做法重簽重送(有測試釘住),不改判斷點。
- 「分批」只做到額度釋放後新任務能再通過,沒有自動排程;被擋下的提案不會自己重來(F7 合約原文照實寫明,使用者裁定)。
- 稽核明細在一小時內 30 萬筆已驗證寫入時約 3.4 秒、握著寫入鎖;目前沒有自動呼叫的入口,回頭條件寫在 [[Systems/可觀測查詢]]。額度查詢「還沒結案」那段用不上索引(增量 1 既有成本),這次沒動。
- 收件口回給分析行程的擋下原因,權限類(含總曝險已滿、比例過大)合併成「不允許」,分析行程看不到細分原因(使用者裁定)。
- 寫入停下紀錄時的時間轉換不檢查時區、讀取路徑會檢查,兩邊嚴格度不一樣;已決定統一,等 Phase 8 合進主線後補一個小提交。(2026-10-02 更正:已在 Phase 8 統一,寫入走 `src/rtb/executor/attempt_store.py` 的 iso,沒帶時區就拒絕,見 [[Verification/Phase8驗收紀錄]]。)
- F7 第五次審計的兩條次要建議沒做:合約措辭可以列出任務與修訂、可以加綁待核可並行放回的測試;合約文字已審過,不再改。
- 合併增量 4 查詢三時,編排者直接還原治理帳而沒先存,那次合併提交的檢查紀錄少了幾行,程式與筆記不受影響。
- 指標只是唯讀查詢,沒有告警與時間序列(Phase 9)。(2026-10-02:條件已成立——Phase 9 已加多窗口燒損告警,見 Systems/服務水準與燒損告警 筆記;時間序列仍沒有,見 [[Verification/Phase9驗收紀錄]] 的 REVISIT。)
