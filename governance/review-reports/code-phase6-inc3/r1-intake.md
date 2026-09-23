# 代碼審第 1 輪收貨與重現紀錄(2026-09-23)

## 收貨
- 七席收齊才動工作目錄:s1 正確性、s2 測試假綠、s3 相容與回歸、s4 設計符合度、sarch 架構對齊、資安-sonnet、x1 外家 Codex(沒有撞到用量限額)。受審版本:phase6-inc3 的 eae2c7c。s1 與資安席 clean。共 10 條:4 條 major、6 條 minor,沒有阻擋級。
- report-normalize:七份都已是正規化格式。quote-check:s2-2 的引句取自計劃合約文字、s4-1 的引句是模組說明跨行,凍結 patch 裡錨不到;兩條都由編排者機械重現(見下表),s4-1 與 sarch-1 是同一件事。
- 席位的 git 實驗都在自己的臨時目錄;repo 的 reflog 核對過沒有被動。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| x1-1 | 新測試 test_an_approval_signed_after_the_lookup_is_honoured_before_writing 在修正前會開嘗試並寫 DSP(暫存副本拿掉修正即翻紅) | HIT,折入(開始一筆交易裡核對查好的那張仍是最新,不是就放掉租約下一輪重判) |
| x1-2 | 新測試 test_a_resend_drops_an_approval_whose_tenant_scope_changed,拿掉修正翻紅 | HIT,折入(重送的封頂重簽也比對兩次租戶設定) |
| s3-1 | 新測試 test_an_unneeded_aggregate_approval_does_not_shorten_the_capability,拿掉修正翻紅(憑證只剩核可的 30 秒) | HIT,折入(總曝險那張只在照當下已用額度會超過門檻時才留) |
| s2-1 | 新測試 test_a_hard_rule_at_the_capping_signature_still_blocks,把分支改成沿用第一次簽發即翻紅 | HIT,折入(補測試;程式本來是對的) |
| s2-2 | 新測試 test_a_resend_needs_the_approval_it_used_even_after_a_newer_one 重現:用過的核可過期、補簽足額新核可仍不重送 | HIT,折入(保留保守行為:核可使用紀錄記的那張才是放行依據;補測試並寫進執行迴圈筆記) |
| s3-2 | 新測試 test_running_the_approval_tool_twice_in_the_same_second_is_harmless,改回一般寫入即翻紅 | HIT,折入(同一張核可再寫一次就略過) |
| sarch-1 | 讀 src/rtb/executor/approval.py 匯入清單,確有收件口模組 | HIT,折入(模組說明、系統筆記、計劃三處改成精確敘述) |
| s4-1 | 同 sarch-1 | HIT,跟 sarch-1 同一件,折入 |
| sarch-2 | 讀三處相同的租戶查找運算式 | HIT,折入(簽發器模組新增共用的租戶查找,三處改呼叫它) |
| sarch-3 | 讀 find_proposal 裸讀 | HIT,折入(包進立即交易、忙碌轉成可重試的例外) |

## 處置
- 折入 10,放行 0、駁回 0。修正後六道對應變異在暫存副本逐一翻紅;執行端測試 457 條全過。
