判定:不同意

**已核對範圍**
- 通讀了 `docs/rtb-production-agent-demo-knowledge/Systems/執行迴圈.md` 裡 F7 的 KEY/WHY 行與 `kill_recipes`(9 筆屬於「★INVARIANT★ 事故 F7」)、`Projects/RTB_Phase6權限護欄與總曝險_計劃.md` 增量 1、3 全文(S330–S343、S350–S379)、`tests/executor/test_aggregate_limit.py`、`test_f7_end_to_end.py`、`test_approval.py` 全文(93 條測試)、交接文件 `RTB_PRODUCTION_AGENT_DEMO_HANDOFF.md` 第 16 節 F7 原始描述,以及既有三輪 `governance/review-reports/rtb-phase6權限護欄與總曝險-增量3/f7-audit-{1,2,3}.md`(前三輪已同意的部分只驗證是否仍成立,不重查)。
- 全程在 `/tmp/rtb-audit/repo`(複製自本 repo,含完整 `.venv`)做改壞實驗;原 repo 未寫入,git 全程唯讀,實驗完已清除臨時目錄。

**前三輪抓到的洞現況:已修好,我逐一重驗屬實**
- 「分批」正面半句缺端到端證明 → 現有 `test_a_new_task_passes_once_the_budget_is_released` 確實走完整 `h.submit()`+`h.process()` 路徑、涵蓋兩種釋放(24 小時出窗、轉人工判失敗),不再只是底層單元測試組合推論。
- 核可「剛好在開始一筆那一刻到期」的競態邊界 → 把 `execution.py` 的 `now.timestamp() < found.expires_at` 改成 `<=`,`test_an_approval_that_expires_mid_flight_lets_nothing_through` 立刻翻紅,邊界測試是真的。
- 政策版本、租戶身分兩種失效沒綁進 F7 清單、測試名字誤導 → 現在 `test_an_approval_is_void_across_policy_or_tenant_changes`([S375])已綁進 F7 的 `[test:...]`,測試也改名拿掉了 `_or_policy`;我把 `holds()` 的 `proposal.policy_version == POLICY_VERSION` 拿掉、把 `scope_fingerprint()` 材料裡 `"tenant": tenant.name` 拿掉,這支測試都立刻翻紅。
- 比例常數改了核可要失效沒有行為測試 → 新增 `test_an_approval_is_void_once_the_ratio_constants_change` 已綁進清單。

**(1)/(2)/(3) 新發現:範圍指紋材料裡有 4 個欄位完全沒有任何測試守著,已用改壞實驗證實(全部 93 條測試、含 F7 綁定的 15 支都不會翻紅)**

`src/rtb/executor/approval.py` 的 `scope_fingerprint()`:

```python
material = {
    "tenant": tenant.name, "campaigns": sorted(tenant.campaigns),
    "max_budget": tenant.max_budget, "aggregate_limit": tenant.aggregate_limit,
    "ratio": [guardrails.MAX_INCREASE_NUMERATOR, guardrails.MAX_INCREASE_DENOMINATOR,
              guardrails.MIN_INCREASE_STEP],
    "policy_version": POLICY_VERSION,
}
```

我逐一把下列欄位從 `material` 拿掉,各自單獨跑 `tests/executor/test_aggregate_limit.py test_f7_end_to_end.py test_approval.py`(93 條)全套,結果全部維持綠燈:

| 拿掉的欄位 | 結果 |
|---|---|
| `campaigns`(廣告清單) | 93 條全綠 |
| `aggregate_limit`(總額上限,**F7 門檻本身**) | 93 條全綠 |
| `MAX_INCREASE_NUMERATOR`、`MAX_INCREASE_DENOMINATOR`(只留 `MIN_INCREASE_STEP`) | 93 條全綠 |

對照:全專案(`grep -rn "MAX_INCREASE_NUMERATOR\|MAX_INCREASE_DENOMINATOR" tests/`)找不到任何一支測試碰過這兩個常數;`test_an_approval_is_void_once_the_ratio_constants_change` 與 `test_admin_and_executor_agree_on_the_scope_fingerprint`([S379])都只 `monkeypatch.setattr(guardrails, "MIN_INCREASE_STEP", 2)`,三個比例常數只驗了一個。同理,兩支「範圍改變」測試(`_scope_changed` 只改 `max_budget`;"moved" 情境只改租戶名稱)都沒有任何一支單獨改動 `campaigns` 或 `aggregate_limit`。

這跟 `policy_version` 那個已經被第三輪審計合理排除的情況不同——`policy_version` 在 `holds()` 裡有一道獨立的直接比對(`proposal.policy_version == POLICY_VERSION`)恆先擋下,所以拿掉指紋裡的 `policy_version` 測不出差別,措辭也誠實地把它排除在「範圍指紋」的宣稱之外。但 `campaigns`、`aggregate_limit`、比例的分子分母,**沒有任何其他直接比對頂著**;指紋是保護它們的唯一機制。指紋材料裡少了或打錯這幾個欄位,整套測試(含 F7 綁定的 15 支)完全接不住——尤其 `aggregate_limit` 正是 F7 的門檻本身:租戶調降總額上限後,一張已經核發的「總曝險已滿」舊核可理論上應該失效,但目前沒有任何測試證明程式真的做到這一點。

合約文字「以及範圍指紋:租戶名稱與設定、比例常數」用的是概括詞(「設定」「比例常數」),但實際只有租戶名稱與 `max_budget`(設定三欄之一)、`MIN_INCREASE_STEP`(比例三常數之一)真正被行為測試覆蓋,`campaigns`、`aggregate_limit`、`MAX_INCREASE_NUMERATOR`、`MAX_INCREASE_DENOMINATOR` 這四項是「程式做了、指紋算了,但沒有測試證明拿掉會被抓到」。`test_an_approval_is_void_once_the_ratio_constants_change` 這個測試名字(複數「常數」)因此比它實際驗到的範圍(單一常數)說得更滿,屬於題目要找的「測試名字宣稱了它沒測到的東西」。

**要補什麼**
1. 把 `test_an_approval_is_void_once_the_ratio_constants_change` 擴充(或新增平行測試),分別 `monkeypatch.setattr(guardrails, "MAX_INCREASE_NUMERATOR", ...)` 與 `"MAX_INCREASE_DENOMINATOR", ...`,核發核可後改常數、斷言 `settle(h) == 0`。
2. 仿照 `_scope_changed`(改 `max_budget`)另加一組:核發 AGGREGATE 階段的核可後,只改租戶的 `aggregate_limit`(`write_config(h.config, aggregate_limit=...)`),斷言 `settle(h) == 0`——這支尤其該綁進 F7 合約行,因為它直接對應 F7 的門檻。
3. 再加一組只改 `campaigns`(廣告清單增減一筆,租戶名稱與其他數值不變)的情境,斷言核可失效。
4. 把以上補上的測試綁進 `執行迴圈.md` 第 24 行 F7 合約的 `[test:...]` 清單,並各補一筆 `kill_recipes`(把 `scope_fingerprint()` 材料裡對應的 `campaigns`、`aggregate_limit`、`MAX_INCREASE_NUMERATOR`/`MAX_INCREASE_DENOMINATOR` 拿掉),讓這類回歸跟現有 `tenant`、`max_budget`、`MIN_INCREASE_STEP` 一樣有機械化的殺傷力配方守著。

**其餘核對均通過,已用改壞實驗逐一驗證**
- 9 筆現有 `kill_recipes` 全部依筆記記載的 old→new 改壞後,對應的 F7 綁定測試翻紅(門檻比較拿掉、未結案不算額度、無核可當有核可、待核可不驗核可放回、憑證活得比核可久、到期邊界差一秒、政策版本/租戶身分兩種改壞、比例常數改壞)。
- `approval.holds()` 裡未被個別列入 `kill_recipes` 的其餘條件(`task_id`、`revision`、`content_hash`、`stage`、`amount <= max_increase`)我也各自單獨拿掉重跑,`test_an_approval_is_void_when_scope_expiry_hash_or_stage_changes` 的對應 `spoil` 分支都準確翻紅。
- 「剛好等於門檻仍放行、超過才擋」(`used + reservation.amount >= reservation.limit` 改壞測過)、「多個工作者同時處理也不會超過門檻」(`test_two_workers_cannot_race_past_the_aggregate_limit` 精細柵欄 + `test_f7_many_small_increases_stop_at_the_aggregate_limit` 3000 筆 8 執行緒真並行)、「已驗證算 24 小時窗口、未結案一直算」邊界都紮實,措辭跟程式行為一致,沒有誇大。

**次要:文件可追溯性的小問題**
- `執行迴圈.md` 第 25 行 WHY 段落寫「補比例常數的行為測試、綁上、加配方,第四次審計見卷證 `governance/review-reports/rtb-phase6權限護欄與總曝險-增量3/f7-audit-*.md`」,但該目錄只有 `f7-audit-1.md`、`f7-audit-2.md`、`f7-audit-3.md` 三份,沒有 `f7-audit-4.md`。第四輪審計的結論沒有對應卷證檔案留存,建議補上或修正這句的輪次描述(也許本次審計可以作為那份卷證)。

**涉及檔案**
- `/Users/enzo/rtb-production-agent-demo/docs/rtb-production-agent-demo-knowledge/Systems/執行迴圈.md`(第 24、25、49 行:F7 合約、WHY、kill_recipes)
- `/Users/enzo/rtb-production-agent-demo/src/rtb/executor/approval.py`(第 55–65 行 `scope_fingerprint()`、第 116–127 行 `holds()`)
- `/Users/enzo/rtb-production-agent-demo/tests/executor/test_approval.py`(第 454–462 行 `test_an_approval_is_void_once_the_ratio_constants_change`、第 561–582 行 `test_admin_and_executor_agree_on_the_scope_fingerprint`、第 205–207 行 `_scope_changed`、第 438–451 行 `test_an_approval_is_void_across_policy_or_tenant_changes`)
