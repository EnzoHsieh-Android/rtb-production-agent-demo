severity: major

## 發現 1：F4／F6 的原任務有提案，錄製驗收卻容許完全沒有說明錄製

severity: major  
blocking: 是  
引句:「return {code: (details is not None and details.narrative_json is not None)」

`proposed_scenarios()` 用「已有說明 JSON」推定「曾有提案」，使驗收條件依賴它要驗的結果。F4／F6 的原任務 `t1` 已建立並送出提案；情境結尾改追蹤不提案的接續任務，因此 `_narrate()` 不處理原提案，`narrative_json` 為空，整個情境可以沒有帳本。`replay_problems()` 隨後收到 `proposed=False`，放行缺少 `NARRATIVE` 呼叫的批次。這違反計劃 [S1428]「每個有提案的 F1–F6 情境都應有 NARRATIVE 錄製」；接續任務本身不提案，不能抹掉原任務的提案。位置：`src/rtb/demo/recordings.py:114`、`src/rtb/demo/driver.py:805`、`src/rtb/demo/driver.py:1393`。

最小重現（唯讀）：

```sh
PYTHONPATH=src .venv/bin/python -c 'from pathlib import Path; from rtb.demo.recordings import replay_problems; observed = replay_problems((("F4", "done", None),), {"F4": Path("/definitely-absent-phase14-ledger.db")}, {"F4": False}); print("observed:", observed); assert observed[1], "F4 原任務已提案，卻沒有 NARRATIVE 呼叫，應拒絕入庫"'
```

輸出為 `observed: (0, ())`，隨後 `AssertionError`。應從任務歷史獨立判定哪些任務曾提案，並驗證各提案的說明呼叫；F4／F6 接續任務仍可沒有帳本。

## 發現 2：舊展示紀錄的 AI 決定會被顯示為「程式規則（九條）」

severity: minor  
blocking: 否  
引句:「RULE_DECIDES: Final = "程式規則（九條）」」

本次保留舊展示狀態庫的讀取能力，卻在讀取時略過舊的 `decided_by`，頁首又無條件寫「程式規則（九條）」。載入 Phase 13 由 AI 下結論的舊展示時，頁面會錯誤歸因。位置：`src/rtb/demo/state_store.py:185`、`src/rtb/demo/page.py:905`。唯讀重現以舊結果摘要「AI 判值得加」呼叫 `_render_decision_hero()`，輸出同時含「AI 判值得加」與「這次誰決定：程式規則（九條）」。舊紀錄應保留可辨識的歷史歸因，或明確標示無法還原。

## 發現 3：F7 仍在 240 秒提早判逾時

severity: minor  
blocking: 否  
引句:「if not world.watch(all_settled, 240,」

[S1411] 與情境總時限採 300 秒，移除 AI 輪數放寬後，`_f7_settled()` 卻固定只等 240 秒。若 300 件純規則工作在第 250 秒完成，仍會被判「時限內沒有全部走完」，即使尚未達展示的 300 秒基準。位置：`src/rtb/demo/driver.py:1129`。應讓這段等待與 F7 的時限分配一致，並測 240–300 秒完成的邊界。

## 固定席圖譜核對

| 節點 | 判定 |
|---|---|
| Mock-DSP | 本 diff 未改冪等鍵、版本衝突或寫入交易；未見其合約受損。 |
| 一鍵展示 | **受發現 1、3 影響**：錄製驗收漏掉原提案，F7 可提早失敗。 |
| 任務流程領域模型 | 終點狀態與證據新鮮度判法未改；未見合約受損。 |
| 分析行程流程與檢查點 | runner 固定接 `rule_round.decide`，未見模型通往正式提案；租約與提交檢查點仍在。 |
| 展示頁面 | **受發現 2 影響**：舊 AI 結論被錯標為九條規則。 |
| 模型用戶端 | 錄製模式改用共用暫存帳本函式；未見即時帳本或模型呼叫邊界被改壞。 |
| 正式九條判斷領域規則 | 本 diff 未改九條本體與精確門檻；未見合約受損。 |
| 評估與 Jev 決策點 | `Judge` 改由評估直接呼叫；未見重新接入正式提案路徑。 |
| 服務水準與燒損告警 | 假說入口仍在，告警計算未改；未見合約受損。 |
| 追蹤檢視 | 本 diff 未改操作追蹤的資料來源；未見合約受損。 |
| 共用行程基礎 | HTTP 白名單、重新導向與故障注入邊界未改；未見合約受損。 |
| 執行迴圈 | 同鍵對帳、重新投遞與寫入守衛未改；未見合約受損。 |
| 提案收件口 | 去重、版本與死信重放守衛未改；未見合約受損。 |
| 確定性指標計算 | 缺值不得當零的計算未改；未見合約受損。 |

逐 hunk 審閱後，未找到正式 runner 讓 AI 決定是否提案的殘留出口。上述重大問題在錄製入庫驗收。pytest 因唯讀沙箱無可用暫存目錄而無法啟動；兩個不寫檔的 Python 重現已執行。