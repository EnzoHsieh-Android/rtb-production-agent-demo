severity: minor

## 發現 1:「撞頂續寫」的判法沒有上限，也不要求真的撞頂的證據，工具使用偵測因此被放寬到裁定範圍以外
severity: minor
blocking: 否

引句:「    return isinstance(turns, int) and not isinstance(turns, bool) and turns > 1 and denials == []」

使用者的裁定是：撞頂後續寫最多 3 次、總輸出不超過上限的 4 倍。正式判讀 `_continued` 卻只看兩件事：對話輪數大於 1、權限被拒清單是空的。
- 一次工具被允許、也成功執行的呼叫，形狀也是「多輪、沒有被拒」。這種呼叫以前會標成工具使用，評估整批停下、印錯誤;現在會安靜地歸成 `output_continued`。
- 實測命令列那邊已經有上限(file: `src/rtb/modelverify.py:211`,輪數在 1 到 1+3 之間),正式判讀沒有同樣的上限。
- 能不能被利用:攻擊者用廣告名稱做提示注入，沒有工具可用(`--tools ""`,實測會擋)。所以沒有直接可利用的路徑，損失的是縱深防禦的警報。
  - 撞頂之後結果是讀不懂、退回程式規則，方向是安全的。
  - 錄製批次裡 `output_continued` 算失敗類，入庫前會擋下。注入最多讓批次錄不過，不能讓批次被接受。
- 例子:
  - 輸入：成功形狀的回應,`num_turns=9`、`permission_denials=[]`。
  - 預期：超出裁定的 1+3 輪，照舊標工具使用、停批。
  - 實際:`UnreadableModelResponse output_continued tool_use= False`,評估照跑。
- 重現：在複本裡用 `PYTHONPATH=src` 執行 `cc.judge_output(0, json.dumps({...,'num_turns':9,'permission_denials':[],...}).encode())`,純判讀，不起行程。
- 修法：輪數限在 `1 + OUTPUT_RECOVERY_ATTEMPTS` 以內，而且輸出 token 要到上限附近(或停止原因是 max_tokens)才算續寫;其餘照舊標工具使用。

## 發現 2:入庫根不存在時，入庫目錄判斷放行(實作者自報的已知風險，已實測確認)
severity: minor
blocking: 否

引句:「        mc.check_recordings_dir(directory, batch_id)」

`_inside_committed` 讀入庫根的 `os.stat` 失敗時直接回 False,見 file: `src/rtb/modelrecording.py:170`。
- 例子:
  - 輸入：複本裡刪掉 `recordings/model` 後，呼叫 `check_recordings_dir(<根>/recordings/model/phase13-demo, "phase13-demo-20260925")`。
  - 預期：在入庫目錄底下，拒絕。
  - 實際：印出 `missing: False False`、`check passed for committed path`(根目錄在的時候是 `True True`)。
- 重現：只呼叫路徑判斷函式，不開閘道、不呼叫模型。實驗在 /tmp/sec-opus-p13i4r2 做，已刪。
- 能不能被利用：前提是攻擊者已經能改 checkout(刪掉 recordings/model),或是新 clone 缺這個目錄。
  - 後果只是一批沒過入庫前檢查的即時錄製寫進預設入庫位置。git 會顯示成新檔，不會覆蓋既有的錄製。
  - 錄製檔名受 `_RECORDING_NAME` 白名單限制;`--dir` 只能由命令列給;即時展示的錄製目錄是 `root/live-recordings/<代碼>`,代碼經白名單。所以錄製指令沒辦法被誘導寫到任意路徑。
- 建議修，而且便宜：入庫根不存在時改成比字面。把 `realpath(target)` 與 `realpath(default_recordings_dir())` 都轉成小寫後比前綴;或乾脆規定入庫根不存在就拒絕即時加錄製。

## 發現 3:另存的單檔報告現在帶腳本，卻沒有任何內容安全政策
severity: minor
blocking: 否

引句:「                             f'<script>{FLOW_SCRIPT}</script></body></html>')」

伺服器送出的頁面有雜湊綁定的 CSP 標頭。另存的報告檔是 file: `src/rtb/demo/server.py:316` 寫的(`inline_styles=True`),檔裡沒有 `<meta http-equiv="Content-Security-Policy">`。
- 以前報告檔沒有腳本，缺 CSP 無所謂。現在帶了腳本，用 file:// 打開時沒有任何 CSP。
- 這一輪沒找到跳脫漏洞，所以列縱深防禦。一旦將來有一處漏跳脫，另存檔裡的模型理由、廣告名稱、收據值就能在本機檔案環境執行腳本。
- 例子:
  - 輸入：假設某個欄位漏了 `escape_text`,廣告名稱是 `<img src=x onerror=...>`。
  - 預期:CSP 擋下。
  - 實際：伺服器頁會擋，另存檔不會擋。
- 修法：在 `_render_head` 帶 meta CSP,腳本用同一個 `script-src 'sha256-…'`;樣式要另加內嵌 `<style>` 的雜湊。

## 看過、沒有發現的面向(不列為發現)
- **CSP 雜湊**:
  - 雜湊只算 `FLOW_SCRIPT` 這一段，沒有 `'unsafe-inline'`、`'strict-dynamic'`,也沒有 nonce。
  - 兩處 `<script>` 都原樣內嵌同一個常數。報告最後的 `_public_page_html` 字串替換碰不到腳本內容(腳本裡沒有那些中文字),雜湊不會失配。
- **DOM XSS**:
  - 腳本不用 innerHTML、不讀 location、hash 或網址參數。
  - 只用 `getElementById(node.dataset.flowPopover)`,這個 id 由情境代碼(白名單)加流程圖節點 id(固定)組成，也經過 escape_text。
  - 動態內容只改 hidden、aria 屬性和 CSSOM 位置。
  - 浮出框內容由 `_flow_node_details`、`_decision_card`、`_ai_basis`、`_render_hypothesis`、`_render_ai_node_card` 產生，每個欄位都經 `escape_text`。escape 過的內容做不出元素，所以沒有 DOM clobbering。
- **側邊欄連結與錨點**:
  - `href="?scenario={code}#flow"`、`#flow-{code}`、`/?scenario=…&tick=…#flow` 都只用列舉值。
  - 側邊欄連結都是站內相對連結，沒有新增重導路由。meta refresh 也經過 escape。
- **長期權杖**:
  - 只走環境變數，不在命令列上，ps 看不到。
  - 只給 EMPTY_HOME 隔離的 claude 子行程。啟動器端 `MODEL_VARIABLES` 限在列入即時清單的分析端和模型入口，其他角色與工具環境白名單都不含它。
  - 批次檢查會把它剔除。分析、說明、假說三支入口沒有另起繼承 os.environ 的子行程。實測紀錄的 notes 只存標籤。
  - 子行程輸出在進日誌、錄製、例外之前都先遮蔽。
  - 權杖字元集是 base64url 加連字號,JSON 或 URL 編碼不會改變它的樣子。模型本身看不到權杖。
  - 殘餘：遮蔽只比對完全相同的位元組。環境值如果帶首尾空白或換行(實測 `'tok\n'` 會原樣傳給子行程),或輸出只印出截斷的權杖，都遮不到。來源是操作者自己設的環境，外部攻擊者碰不到，所以不列發現;修法是遮蔽前先 `strip()`。
- **撞頂當普通失敗**:
  - 攻擊者靠注入讓模型撞頂，只會讓那一次讀不懂、退回程式規則，逃不掉任何守衛。
  - 錄製批次裡撞頂算失敗類，要重錄。其他讀不懂的錄製重播時同樣退回規則，方向是安全的。

k1 已驗：分析端改帶 `--recorded-ledger`,閘道只在判成錄製時用它;判成即時時照設計記在帳號家目錄那一本(`modelgate.py:127`)。
k2 已驗:`OFFICIAL_BACKENDS` 擋掉 fake 後端,`BATCH_PATTERN` 核批次格式。
k3 已驗:`replay_problems` 把帳檔不在、沒有呼叫紀錄、情境沒跑完都算進問題。

看過的改動檔:claims/aggregate-blast-radius.json、claims/concurrency.json、claims/idempotency-unknown-outcome.json、claims/permission-guardrail.json、claims/prompt-injection.json、docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11B大模型接入_計劃.md、Projects/RTB_Phase12一鍵展示與HTML報告_計劃.md、Projects/RTB_Phase13AI參與決策_計劃.md、Systems/一鍵展示.md、Systems/分析行程流程與檢查點.md、Systems/展示頁面.md、Systems/服務水準與燒損告警.md、Systems/模型用戶端.md、Systems/評估與Jev決策點.md、Verification/Phase13增量4驗收紀錄.md、src/rtb/analyzer/ai_judge.py、src/rtb/analyzer/modelgate.py、src/rtb/analyzer/narrate.py、src/rtb/analyzer/runner.py、src/rtb/demo/basis.py、src/rtb/demo/driver.py、src/rtb/demo/faults/ruff.toml、src/rtb/demo/flow_svg.py、src/rtb/demo/launcher/__init__.py、src/rtb/demo/launcher/ruff.toml、src/rtb/demo/observe.py、src/rtb/demo/page.py、src/rtb/demo/present.py、src/rtb/demo/recordings.py、src/rtb/demo/ruff.toml、src/rtb/demo/state.py、src/rtb/demo/state_store.py、src/rtb/demo/static/demo.css、src/rtb/eval/investigation_eval.py、src/rtb/modelclaude.py、src/rtb/modelclient.py、src/rtb/modelrecording.py、src/rtb/modelverify.py、src/rtb/ops/hypothesis.py、src/rtb/stepbudget.py、tests/analyzer/test_runner_model_line.py、tests/conftest.py、tests/demo/fake_recordings.py、tests/demo/test_ai_demo.py、tests/demo/test_ai_launcher.py、tests/demo/test_ai_page.py、tests/demo/test_driver.py、tests/demo/test_launcher.py、tests/demo/test_model_entry_contracts.py、tests/demo/test_page.py、tests/demo/test_present.py、tests/demo/test_record_demo_batch.py、tests/demo/test_server.py、tests/eval/test_investigation_eval.py、tests/eval/test_model_candidate.py、tests/model/test_claude_backend.py、tests/model/test_claude_process.py、tests/model/test_verification.py、tests/test_suite_isolation.py。

本席沒有呼叫模型，沒有起 rtb 行程，也沒有碰 ~/.rtb。實驗只在 /tmp/sec-opus-p13i4r2 呼叫純函式(路徑判斷、遮蔽、判讀),該目錄已刪。

3 條，blocking 0。
