# code-phase10-inc1 第 2 輪收貨與重現紀錄(2026-09-24)

## 收貨
- 2 席收齊:regress、arch(sonnet)。受審 bfd3cfd..61947ef 修正段(全量 72e7e1f..61947ef)。quote-check 兩份全數錨定。
- 前輪 6 條:驗收席判 5 條已修(白名單逐欄共用、明確 isfinite、逾時沒預設、嚴重錯誤往外丟;25 組邊界值比對修正前後 DSP 白名單結果完全一致),x1-2 未修乾淨。全套 1782 綠。
- 共 3 條,全部 major。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| regress-1 | 讀 src/rtb/analyzer/policy.py:81-95:_issuer 是有預設的 dataclass 欄位,dataclasses.replace 會沿用已簽發物件的 _issuer,換掉 cells 仍過檢查;席位在 /tmp 實測拿到全部格 | HIT,折入 |
| arch-2 | 讀 src/rtb/executor/attempt_store.py:186-195 既有先例:普通類別加 __slots__、哨兵是建構式必填參數、直接比對、沒有工廠;ValidatedCells 是 dataclass 加預設哨兵加條件檢查加工廠 | HIT,同一件的做法面,折入(照先例改成普通類別:不是 dataclass 就沒有 replace,哨兵必填、一律檢查、拿掉工廠) |
| arch-1 | 讀 src/rtb/domain/_checks.py 檔頭規定判斷函式標 TypeGuard、參數 object;新三支用 Any 與 bool;src/rtb/domain/evidence.py:123 同構的缺值或整數判斷用 TypeGuard[int | None] | HIT,折入 |

## 處置
- 全部折入,放行 0、駁回 0。修法交增量 1 實作員。
