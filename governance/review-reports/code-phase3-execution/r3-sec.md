severity: clean

已用資安(攻擊者)視角把這輪(第 3 輪)r3-delta.patch 逐行審過,聚焦兩處:啟動程式 `runner.py` 的「os.open O_CREAT 建檔案、先鎖後開 SQLite」新順序,以及 `execution.py` 把到期判斷搬進「開始一筆」交易內的改法;另外通讀 r3-snapshot.patch 確認沒有新開的攻擊面(dsp_client.py、httpclient.py 的 `X-Capability` 標頭、campaign_id 在送往 DSP 前已受 `ID_PATTERN` 白名單約束等,均非本輪改動且未見新洞)。

具體驗證方式:把整個 repo 複製到臨時目錄(不動 repo 本體,`git` 只在臨時目錄操作),對兩處關鍵防線各做一次「拿掉修正」的突變實驗:
- 把 `runner.py` 還原回舊順序(`InboxStore(args.db).close()` 先於拿鎖),`test_a_locked_out_runner_never_opens_the_database_with_sqlite` 立刻轉紅(鎖不到的第二個執行迴圈會在拿到鎖之前就經 SQLite 開資料庫)——證明這條防線是真的在守,不是假綠。
- 把 `execution.py` `_take()` 內新加的到期覆核拿掉,`test_a_proposal_that_expires_while_the_dsp_is_read_is_not_executed` 與新增的 `test_a_proposal_that_expires_while_signing_is_not_executed` 都轉紅,而且失敗方式是「過期的提案被實際送去執行(Result.EXECUTED)」——證實這條覆核確實擋住了「讀 DSP 或簽發期間跨過到期時間仍被執行」這個攻擊視窗,不是裝飾性檢查。

另外針對 `os.open(args.db, os.O_RDONLY | os.O_CREAT, 0o600)` 這行新增的權限收緊做了實測:同目錄下實驗證實 SQLite 在 WAL 模式下建立的 `-wal`、`-shm` 輔助檔會沿用主檔的權限位元,收緊到 0o600 後三個檔案（主檔、-wal、-shm）在連線存活期間都是 0o600(先前預設是 0o644,其他系統使用者可讀);所以這個新增的檔案建立方式沒有留下權限不一致的縫。

檢查過但認定「非本輪新洞、屬既有已承認限制」而不重報的項目:硬連結/符號連結的刻意繞過(docs/rtb-production-agent-demo-knowledge/Projects/RTB_Agent_Phase0架構.md 明寫「威脅模型是防忘記,不防繞過」,且 runner.py 檔頭同樣聲明)、同一 OS 使用者下分析行程可讀寫執行行程檔案(已知限制,有 REVISIT 追蹤)、憑證到期時間(120 秒)兜底寫入延遲窗口(r2 審查已結論接受)。這些都是既有設計取捨,不是這輪修正帶進的新行為。

未發現會做錯行為、破壞合約、資料損壞或假綠測試的洞。
