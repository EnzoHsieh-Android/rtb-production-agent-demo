# code-phase7-inc1 r1 收貨紀錄(2026-09-23)

風險分級 standard(`lumos pitfalls --diff` 判定:有程式檔改動、沒命中風險型樣)。編制:單席通才審查員、架構對齊、外家否決;另因這次改動本身就是信任邊界,編排者自願加派一席資安(不是閘的要求)。
外家否決席原派 Codex(`codex exec -m gpt-5.6-sol --sandbox read-only`),撞到用量上限沒有產出報告(rc=1,錯誤訊息「You've hit your usage limit … try again at 5:48 PM」,原始輸出存 r1-codex-veto-failed.log);照主線交代改派 opus 模型的代理頂替,報告 r1-veto-opus.md。
四席全到才讀、才動程式。通才、資安、否決(opus)三席判 clean;架構席 2 條。reviewer 報告跑過 report-normalize --write(只搬檔級行位置)。

| id | 重現命令 | 輸出摘錄 | 結論 | 處置 |
|---|---|---|---|---|
| arch-F1 | `grep -n 'MAX_INT' src/rtb/domain/proposal.py src/rtb/analyzer/policy.py` | proposal.py:29 MAX_INT = 2**63 - 1;policy.py 已 import 它 | HIT | 折:DSP 用戶端改 import 提案模組的 MAX_INT,刪掉重複常數 |
| arch-F2 | 對照 `src/rtb/domain/proposal.py:163` 的 CHECKS | 提案白名單的檢查只收值;DSP 用戶端的檢查多收 TaskRow | HIT | 折:檢查改成只收值,「廣告編號要等於任務的」拉到 _trusted 單獨核對 |

refuted:none。

否決席(opus)列的三點「交編排者」觀察,編排者判讀:
- 可信證據只擋值、沒擋鍵(實測可用中文當鍵建起可信證據):跟模組說明「由型別保證」不符,折進程式——可信證據的鍵也必須是短代號,新測試 test_trusted_evidence_keys_are_short_codes_too 先紅後綠;不計為席位 finding(該席判非 blocking、未列 severity)。
- 可信欄位不合格時兩個端點的呼叫紀錄都已記成成功,查軌跡看不出原因:不在增量 1 範圍,列給主線。
- 時間窗照設計收 1h/1d/7d,但用戶端只要 1h:設計字面允許,是否收緊列給主線與使用者決定。
變異檢查(拿掉防護、清 __pycache__、跑對應測試):廣告編號不核對任務、可信證據不擋欄位名稱、可信字串不查短代號、預算上限放寬——四道全紅。
