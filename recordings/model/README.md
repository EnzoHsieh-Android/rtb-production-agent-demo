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
