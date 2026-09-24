preflight-4: ran

# r1 收件與前掃(rtb-phase13ai參與決策)

前掃員(sonnet)只讀逐項掃;編排者照下列修改真檔,修改前→後:

| # | 類 | 修改前 | 修改後 | 動到裁定/核心原則 |
|---|---|---|---|---|
| 1 | ② 壞引用 | `[[Systems/模擬DSP]]` | `[[Systems/Mock-DSP]]` | 否 |
| 2 | ③ 矛盾 | 評估集 6 格 ≈ 48 筆 × 3 ≈ 144 次,分展示編號跑 | 9 格 ≈ 72 筆 × 3 ≈ 216 次;裁定 13 不設上限,不分編號 | 否(落地段跟上裁定 12、13) |
| 3 | ③ 矛盾 | 過去調整收據沒有「距今幾天」,裁定 12 第一條與 S1133 引用不到 | 收據加「距今幾天」 | 否(設計節) |
| 4 | ③ 矛盾 | 逐日趨勢收據列轉換、營收、點擊率,缺轉換率(選項表有) | 收據加轉換率 | 否 |
| 5 | ③ 矛盾 | 租約守衛「2 + 2 次讀取 → max(4×3,25)×2」,沒算一輪選滿 3 個查詢 | 「2 + 4 次(較長窗讀兩次)→ max(6×3,25)×2 = 50」,並寫 DSP 逾時拉長時改由讀取次數主導 | 否(結論 50<60 不變) |
| 6 | ④ 語意 | SIGTERM 期間「變成例外從 except Exception 漏出」 | 寫明是 `CallTerminated`(BaseException 子類),`except Exception` 根本接不到,runner 要另外明接;證據 `src/rtb/modelclaude.py:333`、`src/rtb/analyzer/runner.py:169-174` | 否 |

④其餘 10 項語意逐項屬實(租約 60 秒無續租、CALLS_PER_STEP 只算 DSP、_no_action_reason 呼叫 policy.explain、主執行緒限制、證據種類封閉三種、照種類找第一筆、DSP 只讀 1h、metrics 表結構、種子只種 1h、流程圖列舉覆蓋測試、basis/kind/operation_key 仍只在頁面分支)。
