severity: major
# 審查報告

severity: major

## 總覽
本次審查針對 phase9-integration 分支三個合併衝突處置(540ef0a、6742fb9、96415b1)的 remerge-diff。稽核金鑰只在測試檔內使用、服務水準命令列的結束代碼表與共用時區檢查、索引清單合併都核對過,行為一致,測試(`PYTHONPATH=src /Users/enzo/rtb-p9i1/.venv/bin/python -m pytest`,1753 個測試)全過。但 96415b1 對維運掃描([S617])的「builtins 例外」修正實際上放寬了偵測範圍,造成可繞過的安全缺口,已用實驗證實。

### 1. 維運掃描的「經 builtins 取用」判定只認字面量名字,別名匯入完全繞過
severity: major
blocking: 是

引句:「內建的執行函式:直接呼叫、經 builtins 取用、從 builtins 匯入都算;別的物件上同名的方法不算」

引句:「names = [] if node.attr in BUILTIN_RUNNERS and not on_builtins else [node.attr]」

96415b1 為了不讓 `re.compile(...)` 這種正規式模組方法被誤判為内建 `compile`,把 `ast.Attribute` 節點的判定改成:只有當 `node.value` 是字面量 `ast.Name` 且 `id` 恰好是 `"builtins"` 或 `"__builtins__"` 時,`eval`/`exec`/`compile`/`__import__` 這幾個屬性存取才算違規;否則(`node.attr in BUILTIN_RUNNERS and not on_builtins`)直接把該屬性名從候選清單裡拿掉,連檢查都不做了。

合併前(增量 1 原始版)的邏輯是 `names = [node.attr]`——**任何**物件上叫 `eval`/`exec`/`compile`/`__import__` 都會被抓到,雖然會誤傷 `re.compile`,但也連帶擋住了 `b = builtins; b.eval(...)` 這類別名寫法。合入後為了消除 `re.compile` 的誤報,把整條規則從「任何物件的這幾個屬性名都算」限縮成「只有字面量寫 `builtins.` 或 `__builtins__.` 才算」——這正是放寬了原本要擋的行為。

觸發情境:在維運套件(`src/rtb/ops/`)任一檔案寫
```python
import builtins as b
def _sneaky():
    return b.eval("1+1")
```
或
```python
import builtins
def _sneaky():
    x = builtins
    return x.eval("1+1")
```

錯誤行為:我在 `/tmp` 複製 `src/rtb/ops` 並直接呼叫測試檔裡的 `_ops_offenders`(未改動工作樹)驗證,兩種寫法 `_ops_offenders(copy)` 都回傳 `[]`——[S617] 的機械掃描(pre-commit/CI 用它擋維運套件混入寫入或動態執行程式碼)完全偵測不到,等同這條「唯讀套件禁止動態執行」的合約被繞過。對照組:`getattr(builtins, "eval")`、`from builtins import eval as _e`、`__builtins__.eval(...)` 三種材料提到的寫法確實都還抓得到(已用同樣方法驗證),只有「先把 `builtins` 綁定到別的名字再用屬性存取」這一種被漏掉。

file: `tests/ops/test_ops_boundaries.py:104`-`112`(`_dynamic_lookups` 函式,`on_builtins` 判定與其套用處)

建議修法:不要只比對字面量名字,要先在同一棵語法樹裡收集「哪些名字目前綁定到 `builtins` 模組」(掃 `import builtins`、`import builtins as X`、賦值語句 `X = builtins` 等,建立別名集合),`on_builtins` 改成查這個集合;或者退回「任何物件的這幾個屬性名都算」的舊邏輯,改用更精準的方式排除 `re.compile` 這種已知安全案例(例如同時要求呼叫物件的靜態型別/來源是 `re` 模組),而不是把整條規則鬆到「非字面量 `builtins.` 就不算」。

---

## 其他核對過、未發現問題的項目
- 增量 3(540ef0a):`AUDIT` 稽核金鑰只出現在 `tests/ops/test_investigation_drill.py`,`src/rtb/ops/slo.py:280` 的正式入口仍從環境變數讀 `read_audit_key`,未混入任何寫死的測試金鑰。
- 增量 2(6742fb9):`aware_time` 搬到 `src/rtb/ops/cli.py` 後,`metrics.py`、`slo.py`、`trace.py` 共用同一份 `EXIT_BAD_ARGUMENTS=7`;結束代碼表註解「維運套件 0/2/3/4/5/6/7,執行端 2/3/4/5/6/7」跟三支命令列與 `approve.py`/`replay.py`/`runner.py` 實際定義的代碼比對一致。`_REQUIRED_INDEXES` 新增的 `attempts_unknown_by_time` 確實由 `src/rtb/executor/attempt_store.py:72` 建立,不是空引用。
- 增量 1(96415b1):`GUARDED`(`AUDITED`)新增 `dsp_calls` 與 `test_audit_tables.py` 的斷言一致;`tests/ops/test_trace.py` 的合併衝突標記移除乾淨,兩邊測試都保留且都通過。

## 測試結果
`PYTHONPATH=src /Users/enzo/rtb-p9i1/.venv/bin/python -m pytest`(於 `/Users/enzo/rtb-3b-inc4`):1753 passed,無失敗——現有測試套件沒有涵蓋「別名匯入 builtins」這個路徑,因此上述繞過不會被既有 CI 攔下。
