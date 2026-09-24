# code-phase10-inc2 第 3 輪收貨與重現紀錄(2026-09-24,standard 上限)

## 收貨
- 3 席收齊:regress、arch(sonnet),finder(Codex)。受審 cce84fd..8c004ad 修正段(全量 6308080..8af1bbf)。quote-check 三份全數錨定。
- regress 席:第 2 輪 x1-2、x1-3、x1-4、x1-5、arch-1 已修並逐條變異翻紅;全套 1815 綠;五層禁 importlib 沒有擋到合法用途。
- 共 5 條:3 major、1 major 重複、1 minor,合併成 4 件。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| regress-1 | 讀 src/rtb/eval/scoring.py:209-211 只要每格路徑集合跟候選路徑有交集就過;指標與下界用整格樣本;席位在 /tmp 用 1 筆候選加 72 筆現行規則重現五格全數驗證 | HIT,折入(正式報告任何一筆都不得是現行規則路徑;正式評估時每一筆都要交給候選,退回算候選的結果) |
| x1-1 | 同 regress-1 | HIT,同一件,折入 |
| x1-2 | 讀 tests/eval/test_evaluation.py:360-368 只認呼叫位置名稱是 __import__;loader = __import__ 再呼叫、from builtins import __import__ as 別名都看不到 | HIT,折入(受保護五層任何引用 __import__ 名稱或從 builtins 匯入都算違規) |
| arch-1 | 讀 src/rtb/eval/scoring.py:30 自刻 _SHA256;src/rtb/domain/evidence.py:21 已有同一條 HASH_PATTERN | HIT,折入 |
| arch-2 | 讀 src/rtb/domain/ruff.toml:25、:31 importlib 與 importlib.import_module 兩條重疊 | HIT,折入 |
| regress-2 | 同 arch-2 | HIT,同一件,折入(minor) |

## 處置
- 全部折入,放行 0、駁回 0。已達 standard 上限,不開第 4 輪;修正差異由編排者逐行讀過再收,收尾報告照實說明未經另一組獨立審查席。
