severity: clean

實跑：唯讀沙箱無可寫暫存目錄，pytest 未能啟動（`FileNotFoundError: No usable temporary directory`）；結論基於完整 diff、規格與真代碼逐 hunk 靜態查證。

總結：最高 severity clean，blocking 0 條。
