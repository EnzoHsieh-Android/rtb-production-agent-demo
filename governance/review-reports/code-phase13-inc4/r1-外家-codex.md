severity: major

## 發現 1:展示錄製批次可在情境沒跑完時通過

severity: major  
blocking: 是

引句:「return self.missing == 0 and not self.problems」

批次檢查收集了 F1–F6 的 `verdicts`，但 `passed` 完全不看它們；情境在呼叫模型前失敗時，花費帳甚至不存在，找不到錄製的筆數仍算 0。file: `src/rtb/demo/recordings.py:48`、`src/rtb/demo/recordings.py:72`、`src/rtb/demo/recordings.py:103`

例子：乾淨的同批錄製目錄，F1 啟動失敗而回報 `incomplete` → 預期批次檢查失敗 → 實際可回報 `passed=True`、命令列結束碼 0。重現：以 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -c 'from rtb.demo.recordings import BatchCheck; print(BatchCheck(0, (), (("F1", "incomplete", "startup failed"),)).passed)'` 執行，輸出為 `True`。

## 發現 2:F7 完全略過逐任務路徑核對

severity: major  
blocking: 是

引句:「declined = _f7_settled(world, tasks)」

設計要求 F1–F7 每件工作按提案或不提案結局呼叫 `require_streams`；F7 只核對最終數量、平台寫入與總額，從未核對分析端及收件口路徑。file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase13AI參與決策_計劃.md:550`、`src/rtb/demo/driver.py:1215`、`src/rtb/demo/driver.py:1237`

例子：一件 F7 工作在成功寫入前意外多走一次不允許的 `x_reclaimed`，最後寫入數與金額仍正確 → 預期路徑核對判「沒跑完」 → 實際 F7 的斷言不會檢查該節點。重現：在縮小規模的 `make_f7` 測試世界中，為一件完成的工作加入該路徑事件並保持最終狀態不變；檢查 `make_f7` 的呼叫路徑，可確認沒有 `world.require_streams(...)`。

## 發現 3:AI 只選過查詢、最終由規則決定時，頁面仍標 AI 決定

severity: major  
blocking: 是

引句:「if any(r.decided_by == inv.DecidedBy.AI for r in records):」

這裡以「曾有任一輪 AI 選擇」判整個情境由誰決定。AI 選查詢會記成 `decided_by=AI`；下一輪若退回，最終提不提案是規則決定，橫幅卻顯示「AI(展示模式)」。file: `src/rtb/analyzer/ai_judge.py:129`、`src/rtb/analyzer/ai_judge.py:166`、`src/rtb/demo/driver.py:1417`、`src/rtb/demo/page.py:739`

例子：F1 第 1 輪選 `check_longer_window`，第 2 輪沒有錄製而退回規則 → 預期「AI 退回程式規則」 → 實際 `_decided_by` 回傳 `ai`。重現：用一筆 `QUERY/AI` 加一筆 `FALLBACK/RULE` 的 `InvestigationRecord` 呼叫 `_decided_by`；唯讀呼叫實測回傳 `ai`。

## 發現 4:登入預檢失敗仍把「即時呼叫」顯示為已發生

severity: major  
blocking: 是

引句:「shown = {"mode": gate.mode.value, "notices": list(gate.notices)}」

runner 在登入預檢之前印出模式行。即時模式的預檢若失敗，後續每件工作都退回規則、沒有即時模型呼叫，但展示頁依這行顯示「即時呼叫」。file: `src/rtb/analyzer/runner.py:262`、`src/rtb/analyzer/runner.py:276`、`src/rtb/demo/driver.py:1448`

例子：F1 列在即時清單、`RTB_MODEL_LIVE=1`，假閘道回報登入預檢失敗 → 預期頁面說明這趟沒有即時呼叫並由規則決定 → 實際模式行先報 `live`，頁面寫「即時呼叫」。重現：以假 `Gate` 令 `mode=live`、`preflight_login()` 回 `FAILED` 執行 runner，核對輸出的 `MODEL` 行及 `_mode_reason`；全程不需呼叫真模型。

4 條,blocking 4。