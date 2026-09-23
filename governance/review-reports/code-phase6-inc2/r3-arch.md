severity: clean

這輪修正(r3-delta.patch)只動三處,都不是新做法、也沒有跨層:

1. `docs/rtb-production-agent-demo-knowledge/Systems/分析行程流程與檢查點.md` 的 RULE 行,補上一句「動態匯入只看名稱、刻意從嚴:閉包裡任何叫 import_module 的名字或屬性都算,撞到同名普通函式就改名,不要放寬判斷(代碼審第 2 輪)」。這只是把第 2 輪代碼審已經定案、原本就用 AST 名稱比對實作的判斷規則寫進筆記,沒有引入新的偵測手法(仍是同一套 `ast.walk` 名稱比對),也沒有改變合約範圍。

2. `tests/analyzer/test_boundaries.py` 的 `_network_offenders` docstring 補了兩行說明,講清楚「只看名稱、不追它綁到哪裡」與「寧可誤報、不漏」的取捨,以及代價(閉包裡不能有同名的普通函式)。函式本體邏輯完全沒動——比對這次 diff 中 `_network_offenders` 的程式碼區塊(context 行,無 `+`/`-`),第 1 輪就已經是 `ast.Name`/`ast.Attribute`/`ast.alias` 三種形式都比對 `import_module` 這個名字。這次純粹是把既有實作的意圖寫成註解,不是新增或替換判斷邏輯。

3. `tests/executor/test_guardrails.py` 新增一列參數化測試案例:引句:「(_campaign(status="paused", version=4),), 150, BlockCode.CAMPAIGN_NOT_ACTIVE),  # > 版本已變」。這一列補的是「不在投放」與「版本已變」同時成立時,優先序表(`GUARDRAILS`)該擋在哪一格,驗證的是既有優先序機制(單一有序表、逐列比對到第一個命中),沒有引入第二套判斷路徑或另一層檢查;測試本身仍留在 `tests/executor/`,跟受測的護欄表在同一層,沒有越界去碰分析行程或收件口那一側的程式碼。

三處改動的共同性質是「補說明文字 + 補一組測試交叉」,判斷邏輯與資料流都維持第 1、2 輪代碼審後的既有樣子,沒有出現第二種做法,也沒有跨越分析端/執行端或程式碼/測試/文件之間原有的分工邊界。

機械反查(受影響測試、共改夥伴、呼叫者三格皆空)與本輪觀察一致:這次改動範圍侷限在文件敘述與測試資料,沒有牽動被其他模組呼叫的介面,不構成需要另外標記的架構相依風險。
