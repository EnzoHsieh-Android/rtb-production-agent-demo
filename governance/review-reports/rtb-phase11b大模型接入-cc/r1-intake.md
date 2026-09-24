preflight-4: ran
第 5 版只改模型後端;前掃沿用主迴圈 rtb-phase11b大模型接入 的 r1 前掃,另由協調者核對 claude --help(2.1.281)確有 -p、--output-format、--tools、--system-prompt、--strict-mcp-config、--no-session-persistence、--max-budget-usd、--model 參數。

# r1 收貨機械重現(2026-09-24)

五席引句全數錨定。

| id | 重現 | 結果 |
|---|---|---|
| g1/s1 | claude --help:--safe-mode 說明為停用 CLAUDE.md、skills、installed plugins、hooks、MCP servers、custom commands;--bare 說明為只收 API 金鑰 | HIT 採信 |
| x2/g2/c2 | claude --help 沒有輸出 token 上限參數,只有 --max-budget-usd | HIT 採信 |
| g2 | claude --help 有 --effort(low…max) | HIT 採信 |
