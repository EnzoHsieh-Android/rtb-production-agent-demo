severity: major

## F1 廣告篩選漏比對租戶，會把其他租戶的停下紀錄接到核可使用列
severity: major
blocking: 是 — 違反主線指定的四欄對照，租戶改掛後會回傳錯誤的已核可放行數

引句:「AND w.content_hash = u.content_hash AND w.kind = u.stage」

`approval_use_count` 的 JOIN 只比對任務、修訂、內容雜湊與關卡，漏掉 `w.tenant = u.tenant`：file: `src/rtb/executor/inbox_store.py:964`。這與設計明定的「租戶、任務、修訂、內容雜湊」四欄不符：file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md:380`。

這不是只能靠竄改資料造成。廣告改掛租戶後，既有 `write_stops` 仍保留舊租戶；重新取得有效核可並放行時，`approval_uses` 會記新租戶。此時以新租戶加廣告查詢，現行 JOIN 仍會借用舊租戶的停下列並計成一次放行。

以公開寫入方法在記憶體資料庫建立「舊租戶停下列＋新租戶核可使用列」後查詢，輸出為：

```text
actual=1 required_four_fields_plus_stage=0
```

應在 JOIN 補上 `w.tenant = u.tenant`，並增加一個停下列與核可使用列租戶不同的回歸案例。現有測試的配對列都使用相同租戶，因此沒有抓到這個缺口：file: `tests/executor/test_observability.py:455`。

其餘核對項目符合材料：查詢沒有讀取 `approvals`，不受重複 `approval_id` 影響；關卡有對應停下種類；兩個入口都有核對交易歸屬，且查詢本身沒有寫入。
