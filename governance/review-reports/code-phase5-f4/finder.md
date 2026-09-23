severity: minor
<!-- 來源:codex exec --sandbox read-only,取 stdout 最後一次完整輸出;原文未改 -->

## F1 啟動執行緒失敗時清理會永久卡住
severity: minor
blocking: 否 — 只影響伺服器執行緒無法建立的異常環境，不會讓合約假綠，但會令測試程序無限等待。

引句:「兩個伺服器各自進自己的 try/finally(比照 F5 端到端):第二個起不來,第一個也要關」

若 `Thread.start()` 因執行緒資源耗盡而拋錯，`finally` 仍會呼叫尚未進入 `serve_forever()` 的 `shutdown()`；Python 的 `BaseServer.shutdown()` 會等待服務迴圈設定停止事件，因此永久阻塞。DSP 與 inbox 兩個啟動點都有同樣問題。

file: `tests/analyzer/test_f4_end_to_end.py:80`

重現命令:
```bash
PYTHONDONTWRITEBYTECODE=1 /Users/enzo/rtb-production-agent-demo/.venv/bin/python -c 'import subprocess,sys; code="from socketserver import BaseServer,BaseRequestHandler; s=BaseServer((chr(120),0),BaseRequestHandler); print(chr(98)+chr(101)+chr(102)+chr(111)+chr(114)+chr(101),flush=True); s.shutdown()"; 
try:
 subprocess.run([sys.executable,"-c",code],capture_output=True,text=True,timeout=1)
except subprocess.TimeoutExpired as e:
 print("TIMEOUT",repr(e.stdout))'
```

輸出:
```text
TIMEOUT b'before\n'
```
