# 模型錄製回應(Phase 11B)

這個目錄放模型用戶端的錄製回應與批次紀錄。錄製由協調者在本機用使用者的 Claude Code 跑一次即時加錄製模式
產生後入庫(吃訂閱額度,記進家目錄的花費帳,在每次展示 1 美元、每月 20 美元的上限內)。實作者不呼叫真的
模型,增量 1 入庫時這裡是空的:CI 與沒開即時模式的展示都走錄製模式,找不到錄製就標「錄製不全」、不採用,
不會改走即時呼叫。

即時模式要先有啟用紀錄(~/.rtb/live-verification.json),由實測命令列逐項實測、全部通過才寫:

    PYTHONPATH=src python -m rtb.modelverify

然後錄一批(接入點 3「值不值得加」的評估):

    RTB_MODEL_LIVE=1 RTB_MODEL_RECORD=1 PYTHONPATH=src python -m rtb.eval.record --demo-id <這次的展示編號>

- `<sha256>.json`:一次呼叫的錄製(結果文字、當時的延遲、token 數、結果類別、花費估計與原價、後端種類、批次編號)。
- `batches/<批次編號>.json`:那一批每個情境一列的批次紀錄(不可變)。比較表的模型列只從它算。
- 換一批要整批重錄:舊批的錄製檔與批次紀錄整批刪掉再錄;別的批次已有同一個鍵時,即時錄製會在呼叫前拒絕。

## AI 調查的評估錄製(Phase 13 增量 3)

入庫位置是這一層底下的 `phase13-investigation-eval/`(整個目錄只放同一批 `phase13-eval-YYYYMMDD` 的錄製檔,
不放批次紀錄或其他檔)。CI 只重播它、不啟動 claude;目錄存在時,找不到錄製或失敗類錄製都會讓測試紅。
錄製由協調者用真的 Claude Code 產生:先錄進全新目錄、用 `--verify` 重播驗收、驗過才整個搬進來(重錄時整個
替換,不在舊目錄上疊錄)。指令見 [Phase 13 計劃](../../docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase13AI參與決策_計劃.md)
〈實作解讀〉增量 3 的最後一條。

## 規則模式探索的評估錄製(Phase 15 增量 3)

入庫位置是這一層底下以評估版本命名的目錄(現行 `phase15-rule-mining-v3/`):`manifest.json` 是批次清單
(三種子、資料雜湊、評估版本與版本雜湊、每一次嘗試的展示編號、批次、模型、預期鍵、錄製鍵、暫存錄製目錄、
預期錄製檔的 SHA-256、時間、結果與狀態),每個固定種子一個子目錄,只放那一批唯一一次呼叫的錄製檔,內容雜湊
要等於清單記的。CI 只重播它、不啟動 claude;錄製入庫前,`tests/eval/test_rule_mining_cli.py` 的
`test_committed_rule_mining_recordings_replay_all_three_seeds` 是紅的。換評估版本就是換目錄,舊批原地保留。

錄製由協調者用真的 Claude Code 產生,一個種子一次呼叫;命令列只在使用者環境明確設兩個開關時才即時,
缺錄製絕不自動改走即時。程式只寫批次清單,**不寫入庫的錄製檔**:判定通過後由協調者照印出的指令手動搬
(比照上面兩節)。同一時間只准一個錄製或判定(入庫目錄裡的 `.recording.lock`,主人握著檔鎖)。錄前會
唯讀查帳號家目錄的花費帳,這個種子有清單沒記的呼叫(例如在別的 checkout 錄過)就拒絕。

暫存錄製目錄要用**持久的**路徑:下面用 repo 根的 `.rule-mining-staging/`(已在 `.gitignore`),不要放
`/tmp`(系統可能清掉)。錄完到手動搬完之前,不要刪、不要改裡面的錄製檔;第一次成功的錄製一旦遺失,依計劃
不准用下一個序號重抽,要換評估版本並記理由。暫存目錄裡多出的雜檔(例如 `.DS_Store`)會被忽略。

1. 錄前預檢:`PYTHONPATH=src .venv/bin/python -m rtb.modelverify` 的啟用紀錄有效、價目表查核未過 90 天;
   先跑一次 `PYTHONPATH=src .venv/bin/python -m rtb.eval.rule_mining_eval --baseline-only` 確認純基準照常。
2. 錄一個種子(展示編號序號從 1 起、每次嘗試加一、不重用;錄製目錄要是全新的空目錄,程式建成 0700):

       RTB_MODEL_LIVE=1 RTB_MODEL_RECORD=1 PYTHONPATH=src .venv/bin/python -m rtb.eval.rule_mining_eval \
         --demo-id phase15-seed-15001-1 --batch-id phase15-rule-mining-15001-$(date -u +%Y%m%d) \
         --recordings-dir .rule-mining-staging/15001-1

   結果不是 ok 就記「呼叫失敗」、不入庫;同種子改用 `phase15-seed-15001-2` 與新目錄重錄,不換種子。
   行程被中斷而停在「呼叫中」時,先 `--abandon --demo-id phase15-seed-15001-1`:暫存目錄裡已有可用的成功
   錄製就轉「待驗收」(接著照常判定),沒有才判成呼叫失敗;兩種都會記原因。`--abandon` 也清主人已不在的
   鎖檔(主人還握著鎖就不清)。
3. 驗收判定(只重播、不搬檔;目錄要是錄製時記的那個真目錄,錄製檔雜湊要跟錄製當下一樣,對不上是參數錯、
   清單不動;判定只在讀進來的快照上做):

       PYTHONPATH=src .venv/bin/python -m rtb.eval.rule_mining_eval --check-in \
         --demo-id phase15-seed-15001-1 --recordings-dir .rule-mining-staging/15001-1

   通過就在清單標成「可入庫」並印出搬檔指令;照印出的 `mkdir`、`cp` 手動搬進 `phase15-rule-mining-v3/15001/`。
   錄製內容本身沒過才記「驗收沒過」,同種子用下一個序號重錄。
4. 三個種子(15001、15002、15003)都搬好後,驗收並重產報告,連同 `manifest.json` 與錄製一起提交:

       PYTHONPATH=src .venv/bin/python -m rtb.eval.rule_mining_eval --verify > governance/eval/phase15-rule-mining.md

每種子只准第一次成功錄製入庫;判定可入庫後不得同版本重錄替換(入庫檔的雜湊綁在清單上),要換就換評估版本
並記理由。
