# code-phase9-inc4 第 1 輪收貨與重現紀錄(2026-09-24)

## 收貨
- 分級 light(改動只有測試與筆記)。照規定派架構對齊一席,另加一席殺傷力(sonnet)。兩席收齊才判讀;殺傷力席的實驗在 /tmp 自建副本,沒動工作樹。
- quote-check 兩份全數錨定。共 4 條,全部 major。
- 殺傷力席另驗過實演三種探針(拿掉故障、對調租戶、縮短事故)都會紅或被前提擋下,換名合併沒有改到既有 _strings 語意。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| arch-1 | 讀 tests/executor/test_dead_letter.py:447 TABLE_WRITE 要求動詞緊貼表名;對 UPDATE OR REPLACE dead_letters 回 False;新七表守衛用同段共現 | HIT,折入(九張表共用一套) |
| arch-2 | 讀 tests/ops/test_investigation_drill.py:43 VirtualClock 另起一類,自動前進 1 毫秒、帶鎖;既有 tests/executor/conftest.py 的 Clock 只手動前進 | HIT,折入(包裝既有 Clock;從系統時間起跳的理由保留:DSP 提交時間用系統時間) |
| tests-1 | 讀 tests/executor/write_scan.py:128-144 text_of 不認 .format 與 %;src/rtb/executor/attempt_store.py:673-676 已有 .format 組 SQL 先例 | HIT,折入 |
| tests-2 | 讀 tests/executor/test_audit_tables.py:49-65 只核對 inbox_store._ADDED_COLUMNS;另開登記表補欄位兩道都看不見 | HIT,折入(全庫只准一處動態補欄位,否則測試炸) |

## 處置
- 全部折入,放行 0、駁回 0。修法交回增量 4 實作員(另一個工作階段)。
