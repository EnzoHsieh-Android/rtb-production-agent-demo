severity: major

k1 已驗：批次驗收現在會檢查情境是否跑完、花費帳是否存在及是否有呼叫紀錄。  
k2 已驗：F7 現在逐件核對處理路徑。  
k3 已驗：頁面改用每件工作最後的調查結論判定由誰決定。  
k4 已驗：登入預檢後才輸出模式；預檢失敗會標為程式規則。

## 發現 1:入庫根目錄尚不存在時，即時錄製會寫進預設入庫位置

severity: major  
blocking: 是

引句:「開錄前的檢查:批次編號格式、目錄不存在或是空的、不在入庫目錄底下(共用的開錄前目錄檢查);」

`_inside_committed` 對入庫根執行 `os.stat` 失敗就直接回傳 `False`；`check_one_batch` 又允許目標目錄不存在。於是新 checkout 尚無 `recordings/model` 時，開錄前檢查會放行預設入庫位置，首次寫錄製檔便建立該目錄。這違反「入庫位置只供重播、驗過才搬入」的守衛。file: `src/rtb/modelrecording.py:163`、`src/rtb/modelrecording.py:184`、`src/rtb/modelrecording.py:205`、`src/rtb/modelrecording.py:276`、`src/rtb/analyzer/modelgate.py:116`

例子：乾淨 checkout 缺少 `recordings/model`，即時加錄製未指定 `--recordings-dir`、已帶批次編號 → 預期開錄前拒絕 → 實際檢查放行，第一次錄製直接建立並寫入預設入庫目錄。重現方式：在 pytest 的隔離複本中，將 `modelrecording.default_recordings_dir` 替換成 `tmp_path / "recordings" / "model"`，保持該路徑不存在，再對它呼叫 `check_recordings_dir(root, "b1")`；呼叫會正常返回，而預期應丟 `MixedRecordingsDir`。此處依唯讀程式碼推理，未啟動模型。

1 條,blocking 1。