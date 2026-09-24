severity: major

第 2 輪本席三條驗收：

| 原發現 | 結果 |
|---|---|
| 1. 開錄前放行讀不回的錄製檔 | 原例的非法 `outcome`、`settlement` 已拒絕；仍有另一種讀不回的檔會通過，見發現 1。 |
| 2. 負數被當正數 | 已修；`-5` 不再對得回證據中的 `5`。另發現科學記號的證據會被拆錯，見發現 2。 |
| 3. 假說呼叫後再次檢查目錄 | 已修；目錄只在入口檢查一次。 |

## 發現 1:非法呼叫者的錄製檔仍通過開錄前檢查
severity: major
blocking: 是

引句:「+        recording = validated(path, data)  # 跟讀取同一套驗證:讀不回來的檔開錄前就拒絕」

共用驗證只檢查 `caller` 是字串，目錄檢查因此接受 `caller: "unknown"`；重播卻要求它等於封閉列舉中的呼叫者。這份檔不可能被任何合法呼叫者讀回，違反開錄前拒絕讀不回錄製檔的要求。file: `src/rtb/modelrecording.py:95`、`src/rtb/modelrecording.py:192`、`src/rtb/modelrecording.py:225`、`src/rtb/modelledger_view.py:39`

例子：將一份同批次、鍵與檔名相符的合法錄製 JSON 的 `caller` 改為 `"unknown"` → 預期 `check_recordings_dir` 拒絕開錄 → 實際檢查通過，`load_recording` 對任何合法呼叫者都丟 `NoRecording`。重現方式：在 `/tmp/外家-codex-p13i1r3` 副本用假後端產生錄製檔、只改該欄，再依序呼叫兩函式；子行程設 `PYTHONPATH=src`，結束後刪除該副本。

## 發現 2:證據中的科學記號被拆成可冒用的數字
severity: major
blocking: 是

引句:「+    allowed = _numbers(evidence)」

`_numbers` 用十進位正規式掃證據，將 `1e-05` 拆成 `1` 與 `5`；科學記號拒絕檢查只作用在模型句子。假說輸入的數值格式化確實可能產生科學記號，因此模型捏造的 `5` 可被當作有證據的數字顯示。file: `src/rtb/modelclient.py:152`、`src/rtb/modelclient.py:168`、`src/rtb/modelclient.py:186`、`src/rtb/ops/hypothesis.py:98`

例子：證據只有「比率=1e-05」，模型回答「比率為 5。」→ 預期刪掉該句 → 實際 `_numbers(evidence)` 含 `5`，整句通過。重現方式：在上述副本以 `PYTHONPATH=src` 呼叫 `traceable_sentences("比率為 5。", "比率=1e-05")`，核對回傳仍含原句。

本輪受唯讀沙箱限制，以上重現結果依程式路徑靜態核對，未執行實驗；沒有呼叫真模型或留下臨時目錄。

2 條,blocking 2。