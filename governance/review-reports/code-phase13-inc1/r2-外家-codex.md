severity: major

第 1 輪驗收：

| 原發現 | 結果 |
|---|---|
| 1. 即時加錄製缺批次編號 | 已修：兩支命令列傳入 `batch_id`，呼叫模型前會檢查。 |
| 2. 錄製檔的鍵與檔名不符 | 原情境已修；目錄檢查仍會放行另一種讀不回的檔，見發現 1。 |
| 3. 模型文字的數字未核對 | 一般正數已核對；負數仍可錯誤通過，見發現 2。 |

## 發現 1:開錄前檢查仍放行無法重播的錄製檔
severity: major
blocking: 是

引句:「+    if path.name != f"{recording.key}.json":」

新檢查核對了檔名、型別與批次，卻沒有核對 `outcome` 和 `settlement` 是否為合法列舉值；實際讀取時會核對並拒絕。這違反開錄前拒絕讀不回檔案的目的。file: `src/rtb/modelrecording.py:142`、`src/rtb/modelrecording.py:203`、`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase13AI參與決策_計劃.md:796`

例子：同批次、鍵與檔名相符的錄製 JSON，把 `outcome` 改成 `"unknown"`、`text` 設為 `null` → 預期開錄前拒絕 → 實際目錄檢查通過，重播時才丟 `NoRecording`。重現方式：在可寫的 `/tmp/外家-codex-p13i1r2` 副本修改一份合法錄製檔，依序呼叫 `check_recordings_dir` 與 `load_recording`；前者通過、後者拒絕。子行程測試須設 `PYTHONPATH=src`。

## 發現 2:負數會被當成正數通過證據核對
severity: major
blocking: 是

引句:「+_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")」

正規式不包含負號。證據中的 `5` 與模型文字中的 `-5` 都被轉成數值 `5`，因此方向相反的數字仍會顯示或存成成功說明。file: `src/rtb/modelclient.py:150`、`src/rtb/modelclient.py:168`、`src/rtb/analyzer/narrate.py:174`、`src/rtb/ops/hypothesis.py:294`

例子：證據只有「變動=5」，模型回答「變動為 -5。」→ 預期刪掉該句 → 實際原句通過。重現方式：在上述副本以 `PYTHONPATH=src` 呼叫 `traceable_sentences("變動為 -5。", "變動=5")`，檢查回傳文字。

## 發現 3:假說成功呼叫後可能被第二次目錄檢查改判為參數錯
severity: major
blocking: 是

引句:「+    if refusal is not None:  # 告警照常印了;入口的參數跟模式對不上,以參數錯結束」

假說在呼叫前檢查一次錄製目錄，輸出結果後又檢查一次。兩個不同鍵的同批次行程若都先通過檢查，其中一個完成模型呼叫時，另一個仍留下佔位檔，完成者就會在已付費、已產生結果後回傳參數錯。file: `src/rtb/ops/hypothesis.py:338`、`src/rtb/ops/hypothesis.py:347`、`src/rtb/ops/hypothesis.py:385`、`src/rtb/ops/hypothesis.py:390`、`src/rtb/modelrecording.py:137`

例子：A、B 共用新錄製目錄與批次，兩者在寫佔位前都通過前置檢查；A 的假模型先回覆，B 的佔位仍在 → 預期 A 回報成功 → 實際 A 印出成功假說後以參數錯結束。重現方式：在上述副本用兩個不同輸入鍵、同步屏障和假執行檔控制兩個行程的檢查與回覆順序；全程不得呼叫真 `claude`。

本席未實跑重現：唯讀沙箱拒絕建立指定的 `/tmp` 實驗目錄（`Operation not permitted`），以上依 patch、原始碼與測試靜態核對；沒有啟動模型，也沒有留下實驗行程或目錄。

3 條,blocking 3。