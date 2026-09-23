# 代碼審第 3 輪收貨與重現紀錄(2026-09-23)

## 收貨
- 三席收齊才動工作目錄:s1 前輪修正驗收與回歸、資安-sonnet、x1 外家 Codex(沒有撞到用量限額)。受審版本:phase6-inc3 的 54230e3(含第 2 輪 4 條修正)。s1 clean(並判定第 2 輪 s1-1「寫明後果有界、不改判斷點」的處置站得住);共 4 條,全部 major,沒有阻擋級。
- report-normalize:三份都已是正規化格式;quote-check 兩份有引句的全數錨定。
- 這是上限的第 3 輪:本輪 4 條的修正沒有再經一輪審查,由推送前的閘與之後的合併測試兜底(比照增量 1 設計審)。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| sec-1 | 新測試 test_only_actionable_awaiting_proposals_are_read_each_round:三份待核可、只有一份有這一關的核可,修正前每輪全讀 | HIT,折入(處理待核可只讀有事做的:到期、有更新修訂、這一關有人簽過核可、同任務開過嘗試;待核可不佔待處理名額維持原設計,總量上限跟改版前擋下列相同,寫進提案收件口筆記) |
| x1-1 | 新測試 test_the_approval_tool_signs_only_the_stage_the_proposal_waits_at,拿掉修正翻紅(可預簽總曝險、跳過第二次停下) | HIT,折入(管理工具只找待核可的提案、只准簽它現在停的那一關) |
| x1-2 | 新測試 test_a_resend_checks_the_latest_approval_in_the_same_transaction_as_going_in_flight,拿掉修正翻紅(DSP 被寫第二次) | HIT,折入(「仍是最新」的核對搬進轉嘗試中的同一個交易;不成立時憑證過期路徑照既有做法判失敗或先作廢,對帳路徑先作廢再判失敗) |
| x1-3 | 新測試 test_an_awaiting_revision_whose_operation_already_ran_is_released:修訂 1 呼叫 DSP 期間處理修訂 2,修正前修訂 2 停在待核可到期 | HIT,折入(處理待核可發現同一把鍵已開過嘗試就放回待處理,取件照那把鍵的狀態確認) |

## 處置
- 折入 4,放行 0、駁回 0。修正後在暫存副本做 8 道變異,首跑 6 道翻紅、2 道存活(預篩的關卡比對、管理工具只找待核可),補兩處斷言後全部翻紅。全套 1439 條通過。
