severity: major

## 發現 1:符號連結後接 `..` 可繞過入庫目錄檢查
severity: major
blocking: 是

引句:「+    candidate = Path(os.path.abspath(target))」

`abspath` 會先按字面消去 `..`，但實際開檔時，檔案系統會先跟隨符號連結。共用檢查因此可能認為目標在入庫目錄外；目標子目錄尚不存在時，後續內容檢查也會放行。模型用戶端最終會在入庫目錄建立錄製檔，違反「入庫目錄只供重播」的合約。file: `src/rtb/modelrecording.py:171`、`src/rtb/modelrecording.py:204`、`src/rtb/modelrecording.py:278`

具體例子（輸入 → 預期 → 實際）：在臨時副本中令 `link` 指向 `recordings/model`，把 `link/../model/new` 作為即時錄製目錄 → 開錄前拒絕 → `abspath` 把它視為副本根下的 `model/new` 而放行；實際開檔位置是 `recordings/model/new`。

重現方式：先複製專案到 `/tmp/外家-codex-p13i3r3`，只在該副本建立上述符號連結；設定 `PYTHONPATH=src`，以 Python 比較該路徑的 `os.path.abspath` 與 `os.path.realpath`，再呼叫 `check_recordings_dir(目標, "phase13-eval-20260925")`。全程無須呼叫模型。本席依程式路徑唯讀推理，未執行此實驗。

其餘指定的第 2 輪修正，包括批次內容檢查的呼叫、預設目錄搬移、`UNSENT` 值與四個入口的共用檢查，未見另一條發現。

1 條,blocking 1。