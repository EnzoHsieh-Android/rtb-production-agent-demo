# code-phase10-inc1 第 3 輪收貨與重現紀錄(2026-09-24,standard 上限)

## 收貨
- 2 席收齊:regress、arch(sonnet)。受審 61947ef..fa4559c(第 2 輪修正與評分表改 5 格)。
- regress 席:第 2 輪 3 條全部修好(replace 丟 TypeError、copy 回同一物件、setattr 被擋;只有刻意的 object.__setattr__ 拿得到,照威脅模型防忘記不防繞過只記觀察);5 格評分另寫獨立評分函式窮舉 6912 組,0 筆不一致;[S700] 仍成立。全套 1783 綠。
- 共 3 條:1 major、2 minor。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| arch-1 | 讀 src/rtb/analyzer/policy.py:95-111 手刻 __setattr__、__eq__、__hash__、__repr__、__copy__、__deepcopy__;先例 src/rtb/executor/attempt_store.py:189-227 只有 __slots__ 與一般屬性;正式程式只讀 allowed.cells | HIT,折入(照先例最小形狀拿掉;無意間改不到 cells 的保證改由沒有 replace、屬性唯讀命名承擔,刻意改內部屬性不防) |
| regress-1 | pickle 往返在 loads 時被 __setattr__ 擋下;全庫沒有 pickle 呼叫 | HIT,折入(minor;拿掉 __setattr__ 後自然消失,測試改驗往返拿到的清單內容一樣) |
| arch-2 | 讀 Systems/任務流程領域模型.md:76 寫 4 個評分格,:80 寫 5 格 | HIT,折入(minor) |

## 處置
- 全部折入,放行 0、駁回 0。已達 standard 上限,不開第 4 輪;修正差異由編排者逐行讀過再收,收尾報告照實說明未經另一組獨立審查席。
