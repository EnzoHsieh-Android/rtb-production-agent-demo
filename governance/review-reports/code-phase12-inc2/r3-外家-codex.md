severity: major

## 第 2 輪驗收

本輪以 `r3-delta.patch` 為主，對照完整 diff、現行程式及各席第 2 輪報告；以下是唯讀驗收，沒有執行測試或瀏覽器重現。

| 第 2 輪發現 | 驗收 |
|---|---|
| 外家、頁面 N2：逐情境出處標錯 | 已修。`demo_runs` 記下每次是完整執行或單一情境重跑，頁面依結果所屬展示判定。 |
| 頁面 N1：瀏覽器表單一律 403 | 已改為 `Referrer-Policy: same-origin`；尚未以瀏覽器複驗。 |
| 頁面 N3–N7：虛線、編號、窄螢幕框線、回頭箭頭、AI 費用 | 均有對應修正與測試；畫面幾何尚未複驗。 |
| 資料席兩項：人工放行根據被藏、F7 規模寫死 | 均有對應修正與測試。 |
| 伺服器第 1 項：取消被記成情境失敗並另存 | 已加入取消原因及取消後不更新最新展示、不另存的路徑。 |
| 伺服器第 2 項、資安第 1 項：訊號與收尾漏掉行程 | SIGHUP、重複訊號及情境開跑前取消已有修正；**啟動與收尾仍有競態，見發現 1**。 |
| 伺服器第 3 項：簽發時長握狀態庫寫鎖 | 簽發已移到交易外；三步流程與回應之間出現新競態，見發現 2。 |
| 伺服器第 4 項：重複確認停在英文錯誤頁 | 已確認過的提交會轉回展示；「仍在簽發」也被當成成功轉回，見發現 2。 |
| 資安第 2、3 項：壞請求標頭、報告目錄權限 | 均有對應修正與測試。 |
| 架構席：流程圖仍在 `page.py` | 已拆到 `flow_svg.py`。 |

## 發現 1:收尾可在展示建立途中返回，隨後才啟動未受取消的行程
severity: major
blocking: 是

引句:「run, thread = self.current, self.thread」

`start()` 在鎖內設 `running=True` 後就放鎖，接著才建立狀態庫、驅動、`current` 和執行緒。`stop()` 可以在這段期間設 `closing=True`，讀到 `current=None`、`thread=None` 後直接返回。原本的 `start()` 不再檢查 `closing`，仍會啟動 daemon 驅動執行緒；伺服器主行程收尾時便不會等待或取消它。這使第 2 輪「關伺服器不留子行程」尚未修完。佐證：file: `src/rtb/demo/server.py:144`、file: `src/rtb/demo/server.py:198`。

具體例子：POST `/run/scenario` 已通過開始檢查、正在建立驅動 → 此時關閉伺服器，預期取消該展示並收掉其子行程 → `stop()` 看不到執行緒而返回，請求執行緒之後仍可啟動展示，子行程可能在主行程退出後留下。

重現方式：在 `/tmp/外家-codex-p12r3` 複本中，以事件閘暫停 `driver_factory`；另一執行緒呼叫 `start()`，待它進入工廠後呼叫 `stop()`，再放開事件閘。檢查 `stop()` 已返回但驅動仍開始執行。修正需讓收尾等待建立中的 `start()`，或讓 `start()` 發布執行緒前在同一把鎖內重新處理 `closing`。

## 發現 2:「簽發中」的第二次確認被回覆為成功，即使第一次最終失敗
severity: major
blocking: 是

引句:「_DONE_HERE = frozenset({ALREADY_CONFIRMED, CONFIRMATION_IN_PROGRESS})」

第一個確認請求預留 `signing_at` 後，第二個正確請求會收到 `confirmation_in_progress`；路由把它與 `already_confirmed` 一樣轉成 303。第一個請求若因收件口忙碌等原因簽發失敗，程式才清掉 `signing_at`，因此第二個 303 並不表示有任何核可簽成。佐證：file: `src/rtb/demo/state_store.py:371`、file: `src/rtb/demo/state_store.py:380`、file: `src/rtb/demo/server.py:387`、file: `src/rtb/demo/server.py:433`。

具體例子：F7 確認窗仍開著，第一次提交卡在收件口並最終回 503；人在等待時再次按「確認並送出」 → 預期第二次提交能完成簽發，或明確告知仍在處理 → 實際第二次先收到 303 回展示，第一次隨後失敗，收件口沒有核可；若確認窗到期，F7 最終仍判「沒有人確認」。

重現方式：在 `/tmp/外家-codex-p12r3` 複本中，用事件閘讓第一次 `_sign` 停在預留之後，送第二張正確表單，確認它收到 303；再讓第一次 `_sign` 丟出忙碌例外，檢查沒有核可且 `approved_at` 為空。`confirmation_in_progress` 應與確定簽成的狀態分開處理。

## 環境與清理

唯讀沙箱不允許建立 `/tmp` 複本，因此上述重現步驟**未執行**；本席沒有建立臨時目錄、報告檔或行程。`~/.rtb` 前後均為同一個空目錄，沒有寫入。前後 `ps -ax` 檢查均回覆 `operation not permitted`，故無法用行程清單複核；本席沒有啟動需清理的行程。

2 條,blocking 2。