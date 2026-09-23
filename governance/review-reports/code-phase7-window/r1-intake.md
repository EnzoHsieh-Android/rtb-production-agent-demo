# code-phase7-window r1 收貨紀錄(2026-09-23)

standard 分級(`lumos pitfalls --diff 004a7de..HEAD`)。兩席(通才、架構對齊)全到;外家否決缺席:Codex 用量上限到 17:48,standard 只要求缺席留痕。本輪 3 條都是 minor、沒有 major,全部附理由放行,程式不再改。

| id | 重現命令 | 輸出摘錄 | 結論 | 處置 |
|---|---|---|---|---|
| arch-F1 | 讀 src/rtb/analyzer/dsp_client.py 的 STATE_FIELDS 狀態檢查 | 狀態用 isinstance(value, str) and value in …;時間窗只寫 value == REQUESTED_WINDOW | HIT | 放行:跟字串常數比相等本身就擋掉所有非字串(JSON 解出的字串不會是子類別),補型別檢查是冗餘;行為相同 |
| reviewer-F1 | `grep -n '1H\|1h' src/rtb/analyzer/policy.py src/rtb/analyzer/dsp_client.py` | policy.py:30 ELAPSED_FRACTION_1H = 1/24;dsp_client.py REQUESTED_WINDOW = "1h" | HIT | 放行:兩處型別與層次不同(配速換算的分數 vs 請求與白名單的字串),都刻意固定 1 小時、註解互相指向;硬綁會讓決策規則依賴 DSP 用戶端 |
| arch-F2 | 同上 | 同上 | HIT(與 reviewer-F1 同一件事) | 放行(同上理由) |

refuted:none。
