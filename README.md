# 廣告預算調整 Agent 示範

這是本機廣告預算調整示範：分析端提出建議，獨立執行端重查後才寫入 Mock DSP（模擬廣告平台）。它不連真實廣告帳戶。[分析流程](src/rtb/analyzer/flow.py) · [執行流程](src/rtb/executor/execution.py)

## 怎麼運作

![分析端先由程式篩選，AI 可選唯讀查詢並回頭取證、下結論或退回程式規則；提案經收件與執行端重查才寫入模擬平台，F6 重放與 F7 核可回到佇列。](docs/assets/agent-flow.gif) [SVG 靜態圖](docs/assets/agent-flow.svg)

開啟 `--ai-judge` 時，程式先檢查資料新鮮、齊全且預算花得偏慢；AI 才能從固定選項選擇逐日趨勢、較長時間窗、操作歷史、過去調整等唯讀查詢，或決定提案、不提案、證據不足。查詢後回到蒐證；結論必須引用收據（程式產出的資料摘要），由程式逐項核對。呼叫或核對失敗就改用程式規則。[AI 判斷](src/rtb/analyzer/ai_judge.py) · [選項與收據](src/rtb/analyzer/investigation.py)

金額、廣告與動作始終由程式依公式決定。AI 寫的說明和告警原因假說只給確認的人參考，不參與決策。提案經本機 HTTP 收件口存入 SQLite（本機檔案資料庫）的待處理佇列；執行端重查權限、版本、時效、單筆及總額限制，必要時等人工核可，再用受限憑證寫入。[收件](src/rtb/executor/inbox_server.py) · [檢查](src/rtb/executor/guardrails.py) · [展示流程](src/rtb/demo/flow.py)

## 安全宣稱與證據

[五份宣稱清單](claims/)分別涵蓋：結果不明時同碼查證、權限與金額檢查、並行去重、不可信文字不能影響金額與寫入欄位、24 小時總加額限制。`tools/verify_claims.py` 核對清單、程式與測試證據並執行測試；[CI](.github/workflows/ci.yml)（推送後的自動檢查）也執行同一命令。這是機械驗證，證據是否充分仍須人工判斷。

## 七種事故情境

- **F1 回應逾時：**查原識別碼確認是否已寫入，不換碼加第二次。
- **F2 寫入後當機：**重啟後依紀錄恢復，不重複寫入。
- **F3 重複投遞：**分析工作爭用與執行端去重，平台只改一次。
- **F4 版本已舊：**拒絕舊提案，另依最新狀態分析。
- **F5 名稱藏指令：**不可信廣告文字不能擴權；展示另比較正常名稱與誘導名稱的 AI 答案。
- **F6 工作停下：**人工逐筆重放後重新檢查，舊決策不能直接寫入。
- **F7 小額累積超標：**超過總額時等待人工核可；核可後仍重查。[端到端測試](tests/executor/test_f7_end_to_end.py)

## 怎麼跑

需要 Python 3.14。在 repo 根目錄安裝開發工具：

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

一鍵展示預設重播[已入庫錄製](recordings/model/phase13-demo/)，不呼叫付費模型；開啟頁面後可一次跑七種情境。詳情依序呈現「這次情境 → 處理流程（滑過或點格子看判斷）→ 處理結果概述」。下例把暫存和報告都放在 `/tmp`：

```sh
PYTHONPATH=src .venv/bin/python -m rtb.demo.server --work-dir /tmp/rtb-demo --reports /tmp/rtb-demo-reports
```

要讓 `--live F1,F5` 等列出的情境真的即時呼叫模型，啟動時還要帶開關 `RTB_MODEL_LIVE=1`（例：`RTB_MODEL_LIVE=1 PYTHONPATH=src .venv/bin/python -m rtb.demo.server --work-dir /tmp/rtb-demo --live F1,F5`）；沒帶開關時一律退回錄製，而列在即時清單的情境讀的是這次展示專屬的空錄製目錄，AI 那一步會改由程式規則決定。F7 不接受即時模式。即時前須由使用者本人先用 `claude setup-token` 取得長期權杖，放進環境變數 `CLAUDE_CODE_OAUTH_TOKEN`（讓隔離的空家目錄也能登入，權杖不寫進任何檔案或紀錄），再執行 `PYTHONPATH=src .venv/bin/python -m rtb.modelverify`，全部實測通過才啟用。[伺服器參數](src/rtb/demo/server.py) · [即時檢查](src/rtb/modelverify.py)

由使用者本人錄製新批次時，先用全新目錄，再重播驗收後入庫；CI 只重播，不錄製。展示批次：`PYTHONPATH=src .venv/bin/python -m rtb.demo.recordings --record --dir <新目錄> --batch-id phase13-demo-YYYYMMDD --work-dir <暫存目錄>`；調查評估批次：`RTB_MODEL_LIVE=1 RTB_MODEL_RECORD=1 PYTHONPATH=src .venv/bin/python -m rtb.eval.investigation_eval --demo-id <展示編號> --recordings-dir <新目錄> --batch-id phase13-eval-YYYYMMDD`。評估錄製驗收時關閉即時開關並加 `--verify`；不錄製時，`PYTHONPATH=src .venv/bin/python -m rtb.eval.investigation_eval --verify` 重播已入庫的 72 筆案例。[展示批次命令](src/rtb/demo/recordings.py) · [評估命令](src/rtb/eval/investigation_eval.py) · [錄製與入庫說明](recordings/model/README.md)

```sh
.venv/bin/python -m pytest -q
.venv/bin/python tools/verify_claims.py claims/
```

## 現況與限制

[72 筆合成評估](governance/eval/phase13-investigation-adoption.md)中，名稱正常的 36 筆，AI 最終類別正確 17 筆、程式規則 12 筆；模型格式失敗率 32.5%，延遲 p95 約 7.2 秒。**結論是不採用 AI 作正式決策**；展示仍可重播 AI 回答。開 AI 時，廣告名稱裡的誘導文字仍可能翻動「提不提案」（評估的誘導切片有 5 筆結論跟名稱正常時不同），但翻不動金額、廣告與動作。合成案例不代表真實投放效果。

本專案是單機示範：分析與執行行程沒有作業系統層的強制隔離；SQLite 不是正式訊息服務；Mock DSP 提供的依鍵查詢等能力未必存在於真平台。人工核可與花費帳也不是抵抗本機高權限操作者的安全邊界。[架構筆記](docs/rtb-production-agent-demo-knowledge/Projects/RTB_Agent_Phase0架構.md)

F7 端到端測試的時間上限依使用者裁定為 **120 秒**。[測試](tests/executor/test_f7_end_to_end.py)
