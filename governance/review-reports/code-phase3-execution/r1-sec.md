severity: clean

我以資安鏡頭(站攻擊者角度,只找可被利用的洞)通讀了凍結 patch 涉及的六支檔:`src/rtb/domain/attempt.py`、`src/rtb/executor/attempt_store.py`、`src/rtb/executor/dsp_client.py`(新檔)、`src/rtb/executor/execution.py`(新檔)、`src/rtb/executor/inbox_store.py`、`src/rtb/executor/runner.py`(新檔)、`src/rtb/httpclient.py`,並對照 `RTB_Phase3外部寫入安全_計劃.md` 增量 3 的合約 S39–S69 與 `RTB_Agent_Phase0架構.md` 的威脅模型(防忘記不防繞過;分析行程吃不可信廣告文字)逐條核對,連帶讀了 `governance/review-reports/code-phase3-execution/r1-snapshot-tests.patch` 確認關鍵合約是用真程式(非造假替身)驗證,不是「假綠」。

逐項查證結果:

- **越權寫入 / 鎖死大量廣告**:`execution.py` 的 `_pick()` 會跳過已有未結案嘗試的廣告,`attempt_store.begin()` 對同廣告與全表(`MAX_UNRESOLVED=20`)各有一道鎖檢查,且租戶/金額上限在簽發端(`capability_signer.sign`)強制;提案本身經 `domain/proposal.py` 嚴格白名單驗證(欄位、型別、正整數預算、ASCII 編號),被提示注入的廣告文字不會流入判斷或簽章欄位。
- **讓執行迴圈停機(DoS)**:`RESPONSE_TABLE`(`execution.py`)對「其他 4xx → 轉人工並停機」是設計明文要求的「出事就停」機制(S61,已在 r1/r2 設計審由資安席過),不是本次 diff 私自引入的漏洞,且 DSP 回應內容無法被提示注入操控(攻擊者只能透過合法提案欄位間接影響,而這些欄位已受嚴格上限與租戶檢查約束),未找到攻擊者能單靠提案內容觸發此停機路徑的方式。
- **錯誤結果代碼掩蓋事實**:逐條核對 `RESPONSE_TABLE` 的比對順序(`react()` 用 `next()` 取第一個相符規則),`idempotency_conflict` 排在 `validation_rejected` 之前、`capability_expired` 排在 `capability_rejected` 之前、`capability_not_configured` 的 503 排在通用 5xx 之前,順序與判定表(計劃 272–282 行)一致;`_after_expiry`、`_verify`/`_check_applied` 的分支也與設計逐句對得上,S47–S51 有逐列與端對端測試覆蓋。
- **金鑰/憑證外洩**:追蹤了所有例外訊息與 stderr 輸出(`runner.py`、`dsp_client.py`、`capabilitykit.py`),沒有一處把金鑰位元組、原始 token 或 HTTP 回應本文寫進例外訊息或日誌;`DspUnavailable` 只帶例外類別名稱,`CapabilitySigner`/`read_key` 失敗只回固定字串。
- **DSP 回應內容未過濾**:`WriteAnswer.error`(DSP 提供、不可信)只用於與封閉常數字串的相等比對(`_status()`),從未寫入 `detail` 欄位或任何顯示欄位;`domain/attempt.py` 的 `is_clean_detail`(ASCII 可列印、有長度上限)在 `attempt_store.transition/resolve` 強制檢查,而 `execution.py` 這次增量的所有 `_write` 呼叫都沒有傳 `detail`,不存在未過濾就顯示的路徑。`CampaignView.status` 雖是 DSP 給的原始字串,但只做字面相等比較(`"active"`/`"paused"`),不進顯示層、不被解析成指令。
- **鎖檔/設定檔檔案系統攻擊面**:`RunnerLock`(`runner.py`)以資料庫真實路徑的 device+inode 命名鎖檔、每輪核對 inode 是否被替換(S68 已測),符合設計「防忘記」的定位;租戶設定檔的符號連結/權限檢查屬於 `capability_signer.py`,不在本次 diff 範圍內。這一塊的已知限制(同一作業系統使用者可繞過)在設計文件中已明列為刻意排除在威脅模型外,不重複列為本次發現。

沒有找到符合判準(越權寫入、資料損壞、可被利用的拒絕服務、結果代碼掩蓋事實、金鑰外洩、未過濾的不可信內容外流)的 major 以上問題。
