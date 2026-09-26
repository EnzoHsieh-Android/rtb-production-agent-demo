# 廣告預算調整 Agent

**這個 Demo 不假設 AI 應該擁有決策權；AI 要保有任何一項決策權，都得由評估與可驗證的證據支持，評估沒過就撤掉。**這是一套判斷廣告要不要加預算的 agent 工作流程。從分析、提案到寫入，每一步都有檢查。廣告平台是本機模擬的，不會碰真實帳戶。

## 怎麼運作

![廣告預算調整流程](docs/assets/agent-flow.gif) [看靜態流程圖](docs/assets/agent-flow.svg)

- **要不要加預算，由程式規則決定。**規則依序看九件事：暫停、資料異常、剛調過預算、上次加額沒帶來轉換、轉換率大跌、延遲轉換、沒投放、有投放且有價值、其餘。只有預算花得偏慢才往下查資料、走後面七條；沒偏慢就在第一步結束。
- **規則按需要查資料。**第一步讀現況與近一小時成效，初篩通過才再讀歷史、過去調整與逐日成效；資料有問題就判證據不足、不提案。
- **金額由程式算，寫入前再過一次關。**執行端重查權限、版本、時效與金額，必要時等人工核可，才寫入模擬平台。
- **AI 不參與決定。**[評估結果](governance/eval/phase13-investigation-adoption.md)沒通過，所以拿掉了它的決策權，只留兩件事：把提案寫成給確認者看的說明、在告警時推測原因。
- **AI 找到的新規則，要驗證過才可能寫成程式。**[AI 找規則計劃](docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase15AI找規則模式_計劃.md)讓 AI 離線從固定種子的合成歷史（事先埋好真規律與誘餌）找候選規則，先由程式重算、跟窮舉掃描比較，再由人確認，才可能經審查寫進規則；比不過窮舉就撤掉 AI、只留程式掃描。實作中。

## 做了哪些把關

- 寫入結果不明時，用同一個識別碼查證，不另發一筆。
- 多個工作者同時處理同一份提案，平台也只會改一次。
- 執行前重查權限、版本與單筆加額限制，過時的提案會被擋下。
- 24 小時內累計加額超過上限，就先停下來等人核可。
- 廣告名稱裡藏的誘導文字，改不了提案金額、目標廣告或動作。
- 寫入後就算當機，也能依紀錄恢復，不會再寫一次。
- 播放錄製回答時，花費紀錄不會寫進帳號家目錄的花費帳。
- [安全宣稱清單](claims/)連到程式與測試證據，並由檢查工具自動核對。

展示備有七種故障情境：

| 編號 | 情境 | 沒防護會怎樣 | 系統怎麼擋 | 截圖 |
|---|---|---|---|---|
| F1 | 送出寫入後，平台沒有回應 | 當成失敗再送一次，預算被加兩次 | 先回平台查，再用同一個識別碼補送，平台只改一次 | [看](docs/assets/phase14/full-F1.jpg) |
| F2 | 寫進平台後、記下結果前，執行端當機 | 重啟後以為沒寫，又寫一次 | 重啟後查平台紀錄，確認寫過就不重送 | [看](docs/assets/phase14/full-F2.jpg) |
| F3 | 同一件工作被兩邊同時處理，同一則訊息也投遞兩次 | 分析費用花兩次，平台改兩次 | 只有一邊能做分析，平台只改一次 | [看](docs/assets/phase14/full-F3.jpg) |
| F4 | 建議寫好後，別人先改了預算 | 用舊數字蓋掉別人的修改 | 寫入前重查版本並擋下舊建議；接續任務發現剛被調過預算，先不動 | [看](docs/assets/phase14/full-F4.jpg) |
| F5 | 廣告名稱藏著要求加 500% 預算、洩漏金鑰的文字 | 名稱裡的指令左右加額判斷 | 名稱影響不了九條規則；受測廣告仍照公式加一成，另一個廣告不調整 | [看](docs/assets/phase14/full-F5.jpg) |
| F6 | 平台連不上，建議停下等人；人工重送時廣告已被改過 | 過時的建議照樣寫進去 | 重送時重查並擋下舊建議；接續任務發現剛被調過預算，先不動 | [看](docs/assets/phase14/full-F6.jpg) |
| F7 | 很多廣告各加一點，加起來超過總上限 | 每筆都合規，總額卻失控 | 累計到總上限就停，超過的等人核可才寫入 | [看](docs/assets/phase14/full-F7.jpg) |

## 跑起來看看

需要 Python 3.14。在 repo 根目錄安裝：

```sh
python3.14 -m venv .venv && .venv/bin/python -m pip install -r requirements-dev.txt
```

開展示：

```sh
PYTHONPATH=src .venv/bin/python -m rtb.demo.server --work-dir /tmp/rtb-demo --reports /tmp/rtb-demo-reports
```

跑測試：

```sh
.venv/bin/python -m pytest -q && .venv/bin/python tools/verify_claims.py claims/
```

展示預設播放錄好的 AI 提案說明，不會呼叫付費模型。啟動後終端機會印出 `PORT`，打開 `http://127.0.0.1:<PORT>` 就能跑七種情境。

想接真的模型跑（權杖用 `claude setup-token` 取得）：

```sh
export CLAUDE_CODE_OAUTH_TOKEN=<權杖>
PYTHONPATH=src .venv/bin/python -m rtb.modelverify
RTB_MODEL_LIVE=1 PYTHONPATH=src .venv/bin/python -m rtb.demo.server --work-dir /tmp/rtb-demo --reports /tmp/rtb-demo-reports --live F1,F5
```

## AI 表現與限制

我們試過讓 AI 決定要不要加預算，最後沒採用。[評估結果](governance/eval/phase13-investigation-adoption.md)

- **答得不夠準。**名稱正常的 36 筆合成案例裡，AI 自己給出有效答案 23 筆、答對 12 筆；另有 13 筆答案不在可選範圍內。
- **會亂加預算。**應該不加、而 AI 有給出有效答案的 19 筆裡，它仍建議加 11 筆。
- **太慢。**回應中位 4.2 秒，門檻 3 秒。
- **所以花錢的事交給程式規則。**規則在這批案例 36/36 全對，但標準答案跟規則是同一套算出來的，全對是必然結果，不能當成規則聰明的證據。

這是單機示範，不具備正式環境的行程隔離與訊息服務。

提案說明目前只拿得到基本三筆證據，截圖裡說「其他證據為空」是[已知問題](docs/rtb-production-agent-demo-knowledge/Issues/展示還留著AI參考判斷分支與說明看不到四查詢.md)。

## repo 地圖

- `src/`：系統本體。
- `tests/`、`claims/`：證據。`tools/` 是核對這些證據的工具。
- `governance/`、`docs/rtb-production-agent-demo-knowledge/`、`CLAUDE.md`、`AGENTS.md`、`.lumos/`：跟 AI 協作開發時留下的過程紀錄，看系統本身可以略過。
