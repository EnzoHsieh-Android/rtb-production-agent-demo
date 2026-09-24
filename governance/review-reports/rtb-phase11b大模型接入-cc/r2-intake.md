preflight-4: ran
r2 為 delta 審,前掃沿用 r1。

# r2 收貨機械重現(2026-09-24)

三席引句全數錨定;新段落席 file 引用 refcheck 8/8 對得上。

| id | 重現 | 結果 |
|---|---|---|
| p1 | claude --help:--safe-mode 說明列 CLAUDE.md、skills、plugins、hooks、MCP 等,未列 auto-memory;--bare 說明列 auto-memory | HIT 採信 |
| y3/n8 | claude --help:--safe-mode 說明「Admin-managed (policy) settings still apply」 | HIT 採信 |
| p2 | tools/verify_claims.py 在主線 phase11-design 不存在(只在 phase11-inc1/inc2 分支) | HIT 採信(改措辭) |
