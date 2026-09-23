# code-phase7-inc3 r1 收貨紀錄(2026-09-23)

standard 分級(`lumos pitfalls --diff 21e8e15..HEAD`)。四席:通才、架構對齊、資安(自願加派:這次是事故 F5 的邊界證明)、外家否決(原定 Codex,用量上限到 17:48、派工時 15:37,由 opus 頂替)。四席全到才讀、才動程式。架構、否決兩席 clean;0 條 major。

| id | 重現命令 | 輸出摘錄 | 結論 | 處置 |
|---|---|---|---|---|
| reviewer-F1 | 對照 tests/analyzer/test_trust_boundary.py 的 _batch 與 src/rtb/analyzer/dsp_client.py 的 _campaign_text | 測試另抄一份截斷規則 | HIT | 折:測試改呼叫用戶端的 _campaign_text,不另抄 |
| reviewer-F2 | 讀 S212 斷言;token 是聲明的 base64url 編碼,網址只由廣告編號與固定路徑組成 | `MARKER not in token`、`MARKER not in url` 沒有獨立辨識力 | HIT | 折:拿掉這兩句,留寫入本文與解開的聲明兩句 |
| security-F1 | 讀 dsp_client._campaign_text 與歷史表寫入 | 控制字元與雙向覆寫原樣存進證據表 | HIT(推論,今天沒有人讀的出口) | 放行:設計刻意不過濾、不改寫(使用者情境題選 b、選項 c 的教訓);顯示給人時標明不可信文字並跟系統欄位分開,是 Phase 6 人工處置畫面的責任(計劃增量 1〈分析端 DSP 用戶端〉已寫) |
| security-F2 | 變異:拿掉 policy._payload 的信任標記條件 | 測試全綠(存活) | HIT | 放行:設計〈回退〉已寫「行為不變,因為成對規則仍在」;計劃狀態行與分析行程筆記都照實記了這道變異存活,真正咬得住的是差異測試 |

refuted:none。
修正後重跑變異:決策讀名稱、名稱影響金額、素材少一類、執行側讀理由摘要、寫入本文與憑證聲明夾帶理由摘要(含字串拼接繞過掃描)六道全紅;拿掉「只讀可信」條件照預期存活。
