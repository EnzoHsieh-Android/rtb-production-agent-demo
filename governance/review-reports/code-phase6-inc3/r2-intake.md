# 代碼審第 2 輪收貨與重現紀錄(2026-09-23)

## 收貨
- 三席收齊才動工作目錄:s1 前輪修正驗收與回歸、資安-sonnet、x1 外家 Codex(沒有撞到用量限額)。受審版本:phase6-inc3 的 498e040(含第 1 輪 10 條修正)。資安席 clean;共 4 條,全部 major,沒有阻擋級。
- report-normalize:三份都已是正規化格式。quote-check:s1-1 的引句裡小於等於號在回傳時被轉成跳脫字元,凍結 patch 錨不到;由編排者機械重現(見下表)。
- 本輪起在記帳上補定錨分級 high、編排者 claude(第 1 輪漏了先定錨,那一輪七席裡已含資安席)。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| s1-1 | 新測試 test_a_capability_shortened_by_an_unused_approval_recovers_with_a_full_lifetime 重現:預判之後額度被釋放,送出的憑證只剩核可的 30 秒 | HIT,折入(不改判斷點:要在寫入鎖裡讀設定檔重簽,違反開始一筆不讀設定檔的規則;後果有界,測試釘住「DSP 回憑證過期時重簽重送拿回正常效期、不記核可使用」,寫進執行迴圈筆記與計劃實務隱患並附回頭條件) |
| x1-1 | 新測試 test_a_resend_honours_an_approval_signed_while_resigning,拿掉修正翻紅(DSP 被寫第二次) | HIT,折入(重送的封頂重簽之後,核對用到的每張仍是該關最新,不是就不重送) |
| x1-2 | 新測試 test_reissuing_an_earlier_approval_makes_it_the_latest_again,把核可編號改回唯一即翻紅 | HIT,折入(第 1 輪的「重複就略過」撤掉:每次下達都寫一列、編號不設唯一,最新照寫入順序) |
| x1-3 | 新測試 test_the_approval_tool_reports_a_busy_database_while_finding_the_proposal,拿掉修正翻紅 | HIT,折入(管理工具在入口統一把忙碌轉成固定代碼) |

## 處置
- 折入 4,放行 0、駁回 0。修正後在暫存副本做 5 道變異:3 道翻紅;2 道存活是等價寫法(編號不唯一之後「寫入略過重複」永遠不觸發、讀出的重複核可內容相同),已把那兩處多餘寫法拿掉。全套 1435 條通過。
