severity: major

c1 已驗：入庫根不存在或讀不到時，開錄前檢查現在會拒絕；根目錄存在時，入庫位置及其子目錄仍會被擋下。

## 發現 1:工具呼叫的總輸出達門檻時會被誤判成撞頂續寫
severity: major
blocking: 是

引句:「and not isinstance(tokens, bool) and tokens >= (turns - 1) * max_output_tokens)」

`_continued` 把總輸出 token 數達門檻當作撞頂證據，但多輪工具呼叫也可能達到這個數量。它會先於工具使用判定執行，將這類呼叫標成 `output_continued`，不設 `tool_use`；評估因此不會依工具使用守衛停下。file: `src/rtb/modelclaude.py:527`、`src/rtb/modelclaude.py:578`、`src/rtb/eval/model_candidate.py:347`

例子：單輪上限 100 token，回應有 2 輪、權限被拒清單為空、總輸出 150 token，期間實際用過工具 → 預期標記工具使用並停止評估 → 實際標成普通撞頂續寫，評估繼續。重現方式：在 pytest 隔離環境用假 Claude 回傳上述成功形狀的 JSON，經 `judge_output(..., max_output_tokens=100)` 檢查所拋例外的 `tool_use`；目前會是 `False`。此項依程式碼推理，未呼叫模型。

## 發現 2:鍵盤使用關閉按鈕後，焦點沒有回到流程格
severity: minor
blocking: 否

引句:「if (event.target.closest?.('.flow-popover-close')) { hide(); return; }」

這輪新增了鍵盤開框後將焦點移入框內的行為，但關閉按鈕只藏起框；只有 Esc 路徑會把焦點還給流程格。鍵盤使用者從框內按 Tab 到關閉按鈕、再按 Enter，會失去原流程格的焦點位置。file: `src/rtb/demo/page.py:124`、`src/rtb/demo/page.py:133`、`src/rtb/demo/page.py:153`

例子：聚焦流程格 → Enter 開框 → Tab 到關閉按鈕 → Enter 關框；預期焦點回到原流程格，實際沒有。重現方式：在 pytest 的靜態頁面 Playwright 測試中，接在開框後操作關閉按鈕，斷言 `document.activeElement` 是原流程格；現有瀏覽器測試只覆蓋 Esc 還焦點的路徑。

2 條,blocking 1。