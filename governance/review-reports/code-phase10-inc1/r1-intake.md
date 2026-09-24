# code-phase10-inc1 第 1 輪收貨與重現紀錄(2026-09-24)

## 收貨
- 3 席收齊:correct、arch(sonnet),finder(Codex,沒撞到限額)。分級 standard,因為動到決策函式另加外家席。受審 72e7e1f..bfd3cfd。
- correct 席在 /tmp 用 20 多組固定資料沒涵蓋的輸入比對凍結舊規則與新決策函式,全部一致;全套 1825 綠。
- quote-check:arch 席 2 句有 1 句錨不到(「timeout_seconds: float = 0.0,」):原文在快照第 319 行與 src/rtb/analyzer/policy.py:174,機械重現 HIT。其餘兩份全數錨定。
- 共 6 條(1 minor),合併成 4 件事。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| correct-1 | 讀 src/rtb/domain/worth.py 的 _is_amount 六欄共用一條;src/rtb/analyzer/dsp_client.py:50-81 白名單逐欄不同(計數只收整數、預算非負整數、花費營收有限數無上限) | HIT,折入 |
| x1-1 | 同 correct-1,另指合法大額營收會被判歸不了格、跳過候選 | HIT,同一件,折入(逐欄檢查抽成跟 DSP 用戶端共用) |
| arch-1 | 同上一件的另一面:_is_amount 不呼叫 math.isfinite,靠比較副作用擋 NaN;src/rtb/domain/evidence.py:96 記過同樣寫法被代碼審抓過 | HIT,同一件,折入 |
| arch-2 | 讀 src/rtb/analyzer/policy.py:174 explain 的 timeout_seconds 預設 0.0;src/rtb/httpclient.py:4 規定逾時沒有預設值 | HIT,折入 |
| x1-2 | 讀 src/rtb/analyzer/policy.py:72 ValidatedCells 是公開資料類別,任何呼叫者可直接建構非空清單 | HIT,折入(私有建構權,正式路徑只拿得到空清單) |
| correct-2 | 讀 src/rtb/analyzer/policy.py:130 except Exception 連 MemoryError、RecursionError 也吞 | HIT,折入(minor,本輪有 major 不放行) |

## 處置
- 全部折入,放行 0、駁回 0。修法交增量 1 實作員(另一個工作階段)。
