preflight-4: ran
r3 為末輪 delta 審,前掃沿用 r1。

# r3 收貨機械重現(2026-09-24)

末輪掃描、外家-codex 引句全數錨定;前輪驗收 clean。

| id | 重現 | 結果 |
|---|---|---|
| m1 | grep 第 7 版 S904「照預留金額計入已用」與正文「取較高者」並存 | HIT 採信 |
| z2 | claude --help:--safe-mode 未列 auto-memory | HIT 採信 |
| m5 | tests/ 根目錄沒有 conftest.py;tests/dsp/conftest.py:34 把 os.environ 整份傳給子行程 | HIT 採信 |
