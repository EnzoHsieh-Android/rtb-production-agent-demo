preflight-4: ran
r2 為 delta 審,前掃沿用 r1(r1-intake.md);本輪材料是第 2 版全文與 r1→r2 差異。

# r2 收貨機械重現(2026-09-24)

引句錨定:前輪驗收(clean)、新段落全數錨定;外家-codex 5 句有 1 句(x4)機械錨不到,原因是抄引句時拿掉原文內層「」;協調者對照 r2-snapshot.md 接入點 2「觸發」那條原文在(帶「已送進收件口」內層引號)——HIT,採信。

| id | 重現 | 結果 |
|---|---|---|
| x4 | grep 快照「對還沒有說明的逐一產生」 | HIT 採信 |
| n7 | PYTHONPATH=src python -c 匯入 rtb.eval.scoring 與 rtb.eval.record 後 sys.modules 含 sqlite3、rtb.sqlitekit、rtb.analyzer.task_store | HIT 採信 |
| n1 | python -c json.dumps('中') 輸出 中(6 位元組) | HIT 採信 |
