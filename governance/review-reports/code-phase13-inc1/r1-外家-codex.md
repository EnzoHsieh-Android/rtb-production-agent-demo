severity: major

## 發現 1:兩支新命令列在即時加錄製模式無法呼叫模型

severity: major  
blocking: 是

引句:「+                              timeout_seconds=TIMEOUT_SECONDS, demo_id=args.demo_id)」

假說命令列建立請求時沒有 `batch_id`；說明命令列經閘道建立請求，也沒有提供批次編號。兩支命令列都沒有 `--batch-id` 參數。共用用戶端在 `record=True` 時會於送出前要求批次編號，否則直接丟未被命令列處理的 `ValueError`。證據見 `src/rtb/ops/hypothesis.py:270`、`src/rtb/analyzer/narrate.py:199`、`src/rtb/modelclient.py:249`。

例子：告警已響、即時模式有效且設 `RTB_MODEL_RECORD=1` → 預期產生假說並錄製 → 實際因「即時加錄製模式要帶批次編號」中止，沒有呼叫模型；說明命令列亦同。重現方式：將專案複製到 `/tmp/外家-codex-p13i1`，以假後端建構 `record=True` 的 `Settings` 和未帶 `batch_id` 的請求，呼叫 `_check_live`；它會丟出上述 `ValueError`，無須啟動 claude。

## 發現 2:開錄前目錄檢查會放行鍵與檔名不符的錄製檔

severity: major  
blocking: 是

引句:「+    return recording.batch_id」

`check_recordings_dir` 對檔案只驗欄位型別和批次，沒有驗 `recording.key` 是否等於檔名中的鍵；實際讀取函式卻會因此拒絕該檔。開錄前檢查遂可能宣告目錄可用，等前面幾筆即時呼叫已送出後，才在撞到壞檔時失敗。證據見 `src/rtb/modelrecording.py:127`、`src/rtb/modelrecording.py:142`、`src/rtb/modelrecording.py:195`；設計要求開錄前拒絕讀不回的檔，見 `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase13AI參與決策_計劃.md:520`。

例子：目錄有一份檔名為鍵 A、內容 `key` 為鍵 B、其餘欄位合法且 `batch_id` 相同的 JSON → 預期開錄前拒絕 → 實際 `check_recordings_dir` 通過，之後讀取鍵 A 才拒絕。重現方式：在 `/tmp/外家-codex-p13i1` 用一份合法錄製檔改寫其 `key` 欄但保留檔名，先呼叫 `check_recordings_dir`，再以檔名鍵呼叫 `load_recording`；前者通過、後者丟 `NoRecording`。

## 發現 3:沒有核對模型文字中的數字就標為成功

severity: major  
blocking: 是

引句:「+    return bool(text.strip()) and len(text) <= MAX_NARRATIVE_CHARS and text.isprintable()」

說明只驗長度與字元；假說也只驗 JSON 形狀、字數和下一步代碼，然後直接輸出。設計明定兩者文字中提到的數字要能對回證據，對不上的句子不得顯示。證據見 `src/rtb/analyzer/narrate.py:165`、`src/rtb/ops/hypothesis.py:232`、`src/rtb/ops/hypothesis.py:283`、`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase13AI參與決策_計劃.md:233`。

例子：證據沒有「花費 999999 美元」，模型回「昨天花費999999美元，轉換率100%。」→ 預期不顯示該句 → 實際說明驗證回 `True`，假說解析也成功，文字會被存下或印出。重現方式：複製到 `/tmp/外家-codex-p13i1` 後，以 `PYTHONPATH=src` 在 Python 中分別呼叫 `valid_narrative("昨天花費999999美元，轉換率100%。")`，以及用相同文字和合法 `next_step` 呼叫 `parse_answer`；兩者都接受。

3 條,blocking 3。