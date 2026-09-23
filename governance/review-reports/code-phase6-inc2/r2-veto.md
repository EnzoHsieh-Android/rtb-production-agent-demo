severity: major
<!-- 來源:codex exec --sandbox read-only,取 stdout 最後一次完整輸出;原文未改 -->

## F1 S405 漏測「未投放」與「版本已變」的優先序，交換判斷仍會假綠
severity: major
blocking: 是 — 合約要求多條規則同時成立時回表中最前者，但目前測試未鎖住這組相鄰規則，錯誤交換兩者仍會全數通過。

引句:「((_campaign(status="paused"),), 151, BlockCode.CAMPAIGN_NOT_ACTIVE),  # 不在投放 > 比例」

現有交叉案例只驗「未投放 > 比例」和「版本已變 > 比例」，沒有讓 `status="paused"` 與錯誤版本同時成立。把 `precheck` 中未投放與版本檢查交換後，S403 的單獨案例及目前全部 S405 案例仍會通過，但依表格應回 `campaign_not_active` 的組合會錯回 `version_changed`。

file: `tests/executor/test_guardrails.py:187`  
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase6權限護欄與總曝險_計劃.md:209`

重現命令：

```text
/Users/enzo/rtb-production-agent-demo/.venv/bin/python -c 'def swapped(status="active", version=3, too_large=False, tenant=True, over_cap=False):
    if version != 3: return "version_changed"
    if status != "active": return "campaign_not_active"
    if too_large: return "budget_increase_too_large"
    if not tenant: return "campaign_not_allowed"
    if over_cap: return "over_budget_cap"
rows=[(("paused",3,True,True,False),"campaign_not_active"),(("active",4,True,True,False),"version_changed"),(("active",3,True,False,False),"budget_increase_too_large"),(("active",3,False,False,True),"campaign_not_allowed")]
print("current S405 rows pass after swap:", all(swapped(*args)==expected for args,expected in rows))
print("missing paused+version-changed case:", swapped("paused",4))'
```

輸出：

```text
current S405 rows pass after swap: True
missing paused+version-changed case: version_changed
```

應補一列同時設定 `status="paused"`、`version=4`，預期 `CAMPAIGN_NOT_ACTIVE`。

## F2 閉包掃描把普通同名函式誤報成動態匯入
severity: minor
blocking: 否 — 目前程式沒有觸發，但合法的共用模組只要呼叫一支恰巧名為 `import_module` 的本地函式，測試就會錯誤失敗。

引句:「offenders += _network_offenders(module, tree)」

本輪把既有判斷擴到整個靜態匯入閉包，但 `_network_offenders` 只按 AST 名稱判斷；任何 `Name("import_module")` 或任意物件的 `.import_module` 都被當成動態匯入，沒有確認它實際綁定到 `importlib.import_module`。這是本輪擴大掃描範圍後新增的誤報面。

file: `tests/analyzer/test_boundaries.py:125`  
file: `tests/analyzer/test_boundaries.py:289`

重現命令：

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. /Users/enzo/rtb-production-agent-demo/.venv/bin/python -c 'import ast; from tests.analyzer.test_boundaries import _network_offenders; code="def import_module(name):\n    return name\nvalue = import_module(\"safe\")\n"; print(_network_offenders("rtb.domain.helper", ast.parse(code)))'
```

輸出：

```text
['rtb.domain.helper: 動態匯入']
```

應追蹤 `from importlib import import_module`、`import importlib` 或內建 `__import__` 的實際綁定，而不是禁止所有同名字詞。

第一輪指出的兩個洞已修到：沿途父套件初始化檔會進閉包，閉包內直接匯入網路模組也會被掃描；新增的兩個變異案例分別依賴這兩條修正。

比例計算、邊界值、`not_permitted` 合併與既有失敗鍵分流未見行為錯誤。兩支並行測試把第二份提案由 160 改為 140 後，提案仍有不同金額且都落在五成上限內，原本的同廣告互斥與版本衝突意圖沒有被削弱；封閉列舉與 `BLOCK_TRIGGERS` 的新增項目也仍保留原有完整性斷言。
