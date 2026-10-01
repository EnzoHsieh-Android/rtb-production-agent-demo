# 防漂移標籤試標(2026-10-01)

目的:使用者同意先試標籤、不一次推(工具鏈建議範圍)。在 5 篇 Systems 筆記手標兩種標籤,量三個數:①標了幾處、②其中幾處機器光看標籤就判得出、③寫的人多麻煩。句子照它現在的說法標、不改內容,所以已過期的句子會變成機器判得出。工具鏈還不認得這兩種標籤;判定用下方附的原型檢查器(只讀,沒進專案程式)。

筆記:一鍵展示、展示頁面、分析行程流程與檢查點、稽核表只增不改守衛、提案收件口。兩個稽核員分工標,主控逐處核對過改動只有加標籤(撤除小節裡失效的 `[test:]` 改成 `[test-gone:]`),五篇 lint 0 問題,drift scan 不變。

## 標籤寫法

- `[count:路徑::名稱=N]`:N 是句子說的數;名稱要是模組層的集合字面值或列舉類別。
- `[retired:日期 提交]`:加在撤除橫幅那一行;整節撤除才准標。撤除小節裡失效的 `[test:名稱]` 改成 `[test-gone:名稱@刪除它的提交]`。

## 三個數

1. 標了幾處
   - 數量:5 篇共 115 句帶數量的現況句(「N 個/只有/恰好」),標得上 21 句(18%):一鍵展示與展示頁面 44 句標 10,另三篇 71 句標 11。
   - 撤除:19 個有撤除橫幅的小節,只有 3 個能誠實標成整節撤除;改標 `[test-gone:]` 3 個。
2. 機器判得出幾處
   - 標上的 21 句都能機器判:15 句吻合,**6 句當場抓到已過期**(一鍵展示:34 列舉十八→十七、一鍵展示:41 RULE 三個模型變數→四個、分析行程:66 九個狀態→十個、稽核表:29 只有嘗試紀錄→兩張、稽核表:30 七個表名→九張、提案收件口:36 三種權限類原因→四種)。
   - 漏的:一鍵展示:101 的節點 48→46、判斷點 17→15、分支 68→67 三句已過期,但這些集合是程式組出來的,標籤指不到。分析行程:73「八類對抗性素材」數字吻合(8),但其中一類是「正常」,語意可能已錯,機器看不出。
   - 撤除小節:檢查器抓到 4 個「撤除小節裡掛的測試其實還在」,證明撤除橫幅說過頭(展示頁面〈AI 參與決策的步驟與標示〉有一半內容仍成立)。懸空 `[test:]` 還剩 45 個,全部卡在「部分撤除」的小節裡,現行格式標不了。
3. 多麻煩
   - 找到具名集合的情況:grep 一次,約 1 到 3 分鐘,約六成標籤是這樣。
   - 最費工也最危險的是「挑對集合」:同一個詞常有好幾個候選(九條規則對 `Cell`、`RuleReason`、`QueryKind`;兩種操作對 2 個成員的 `CAMPAIGN_WRITE_ACTIONS` 與 3 個成員的 `ACTIONS`),要讀 2 到 3 支檔、5 到 10 分鐘;挑錯就會誤報。
   - 判「標不上」也花時間:要先確認程式裡真的沒有單一名稱(一鍵展示:101 要把整張流程圖跑出來才知道是 46)。

## 標不上的原因(115 句裡約 94 句)

| 原因 | 約幾句 | 例子 | 補法 |
|---|---|---|---|
| 純量數字(N 筆、N 次、N 秒、N 份) | 約 27 | 8 個工作者 `F7_WORKERS`、各留 20 份 `KEEP_REPORTS`、2000 字元 | 加 `[value:路徑::常數=值]` 就能涵蓋,count+value 合計約四成 |
| 程式組出來的集合、集合的差或聯集、依種類篩的子集 | 約 30 | 流程圖節點(字面 35 個加展開)、七張表 = AUDITED 減 NO_ALTER、其中 15 個判斷點 | 允許指到運算式或屬性(例 `flow.py::FLOW_GRAPH.nodes`),檢查時實際匯入計算;或要求程式為筆記會引用的集合取名 |
| 散在程式各處的概念 | 約 20 | 四層驗證、兩種轉 FAILED 的情形、三個可替換介面 | 標不了,留給整篇重讀判定者 |
| 字典的值或鍵、SQL 字串、HTML/CSS 字串 | 約 8 | 每步讀幾次 `RULE_STEP_READS["C"]`、CHECK 裡的狀態、索引 | 允許 `路徑::名稱["鍵"]`;SQL 與標記字串標不了 |
| 測試斷言或算式裡的字面值 | 約 8 | 測試寫死的 46、67;2+2+5 次 | 要程式把數字取成具名常數才標得了 |
| 文字裡的列舉、筆記自己的開頭欄位 | 約 6 | 「分三步報」「綁 19 支測試」 | 標不了 |
| 合約行 | 1 | | 照規定不標 |

另外兩條不在這 5 篇、但工具鏈要求一併記成「標不上」的:外部寫入嘗試紀錄:27 那條 RULE 的撤除條件要等指標 `version_conflict_rate`,它在 `src/rtb/ops/metrics.py` 是 `DECLARED` 字典裡的字串鍵,when-symbol/retire-when-symbol 都對不到;宣稱驗證器:41 那條 F7 RULE 的上限只寫在測試斷言字面值(`< 120`),沒有具名常數,`[value:]` 也對不到。

## 撤除標籤的觀察

- 19 個有撤除橫幅的小節,16 個寫「撤除或改寫」「撤除或改成只供評估」,內容新舊混在一起,照「整節撤除才准標」的規則一個都不能標;45 個懸空 `[test:]` 全在這些小節裡。
- 標得了的 3 個裡,展示頁面那節的橫幅寫整節撤除,但檢查器抓到裡面還有 3 支活測試、還成立的句子,橫幅說過頭。
- 建議:允許逐條撤除(一行一個 `[retired-item:…]` 或直接在行內用 `[test-gone:]`,不限撤除小節內),不要只能整節二選一。

## 給工具鏈的建議(依試標結果)

1. `[count:]` 單獨用涵蓋率低(18%),但抓到的都是真漂移、誤報 0;加上 `[value:]` 可到約四成。
2. 要涵蓋最常過期的那群(程式組出來的集合),標籤得能指運算式並實際匯入計算,這比靜態語法分析貴,也牽涉匯入副作用。
3. 「挑錯集合」是主要風險:建議工具在寫標籤時列出同檔裡成員數相近的候選讓人確認,或反向:工具從句中數字與反引號名稱自動提議標籤。
4. 撤除標籤改成逐條,否則懸空綁定的大宗標不了。
5. 程式端也可以配合:筆記會引用的集合與上限,程式取成具名常數(測試裡寫死的 46、67、120 都是反例)。

## 原型檢查器輸出(2026-10-01,5 篇)

```
== Systems/一鍵展示.md
  [count] 31: OK(9 個)
  [count] 31: OK(2 個)
  [count] 34: 句子說 18 個,程式現在 17 個:Disposition, BlockCode, DeadLetterReason, StopKind, LifecycleKind, ReplayOutcome, AttemptState, OutcomeCode, VoidOutcome, Result, TaskState, ReplanReason, NoActionReason, AwaitingOutcome, LastFailure, Freshness, WorthVerdict
  [count] 34: OK(17 個)
  [count] 41: 句子說 3 個,程式現在 4 個:RTB_MODEL_LIVE, RTB_MODEL, RTB_MODEL_RECORD, LOGIN_TOKEN_ENV
  [count] 72: OK(7 個)
  [count] 101: OK(10 個)
  [count] 101: OK(5 個)
  [count] 268: OK(17 個)
== Systems/展示頁面.md
  [count] 37: OK(7 個)
  [test-gone] 92: OK(確實已不在)
  [test-gone] 93: OK(確實已不在)
  [retired] 94: test_ai_scenarios_have_no_demo_mode_banner:還在→撤除小節裡掛活綁定,確認是不是該搬出撤除小節
  [retired] 94: test_the_page_hides_model_mode_while_entries_keep_their_actual_choice:還在→撤除小節裡掛活綁定,確認是不是該搬出撤除小節
  [retired] 95: test_the_narrative_shows_computed_numbers_first_without_mode_source:還在→撤除小節裡掛活綁定,確認是不是該搬出撤除小節
== Systems/分析行程流程與檢查點.md
  [count] 66: 句子說 9 個,程式現在 10 個:RECEIVED, COLLECTING_EVIDENCE, ANALYZING, PROPOSED, HANDED_OFF, COMPLETED, FAILED, BLOCKED, NO_ACTION, SUPERSEDED
  [count] 73: OK(8 個)
  [count] 169: OK(9 個)
  [count] 173: OK(625 個)
  [test-gone] 288: OK(確實已不在)
  [count] 308: OK(2 個)
  [count] 318: OK(4 個)
  [count] 319: OK(5 個)
  [retired] 238: test_an_injected_name_can_only_flip_propose_or_not:還在→撤除小節裡掛活綁定,確認是不是該搬出撤除小節
== Systems/稽核表只增不改守衛.md
  [count] 29: 句子說 1 個,程式現在 2 個:attempts, dsp_calls
  [count] 30: 句子說 7 個,程式現在 9 個:write_stops, approvals, approval_uses, lifecycle_events, dsp_calls, operations, attempts, dead_letters, dead_letter_ops
== Systems/提案收件口.md
  [count] 26: OK(3 個)
  [count] 36: 句子說 3 個,程式現在 4 個:OVER_BUDGET_CAP, CAMPAIGN_NOT_ALLOWED, BUDGET_INCREASE_TOO_LARGE, AGGREGATE_LIMIT_REACHED

標籤 27 個;懸空 [test:] 共 45 個
```

## 原型檢查器原始碼(只讀;不進專案程式,附在這裡給工具鏈參考)

```python
#!/usr/bin/env python3
"""防漂移標籤的原型檢查器(示範用,只讀不寫)。

用法: python3 tagcheck.py <repo 根> [筆記相對路徑 ...]   (不給筆記就掃整個圖譜)

認得的標籤(只在正文與摘要的可見行生效;圍欄、表格行、行內反引號裡的不算):
  [count:路徑::名稱=N]            名稱的成員數(tuple/list/set/frozenset/dict/列舉類別)要等於 N
  [value:路徑::常數=字面值]        常數值要等於字面值
  [enum:路徑::名稱=a,b,c]          成員集合要剛好是 a,b,c(多或少都報)
  [retire-when-file:路徑]          成立就報「撤除條件已成立」
  [retire-when-symbol:路徑::名稱]
  [retire-when-status:節點=值|值]
  [expect:路徑::名稱]              名稱要在那支檔裡
  [supersedes:節點#錨] / [superseded-by:節點#錨]   兩端要成對
  [retired:日期 提交]              所在小節視為撤除:小節裡不准有 [test:],要改成 [test-gone:]
  [test-gone:名稱@提交]            名稱要真的不在 tests/
  [snapshot:日期@提交]             標記快照小節;done 計劃的現況類小節沒有它(或「快照」字樣)就報
另外統計所有 [test:] 綁定是否存在(懸空綁定)。
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
VAULT = ROOT / "docs" / "rtb-production-agent-demo-knowledge"
TAG_RE = re.compile(r"\[(count|value|enum|retire-when-file|retire-when-symbol|retire-when-status|expect|"
                    r"supersedes|superseded-by|id|retired|test-gone|snapshot):\s*([^\]]+)\]")
TEST_RE = re.compile(r"\[test:\s*([^\]]+)\]")
INLINE_CODE = re.compile(r"``.*?``|`[^`]*`")
HEAD_RE = re.compile(r"^(#{1,6})\s")
ANCHOR_RE = re.compile(r"\[(S\d+)\]|\[id:\s*([^\]]+)\]")
SNAP_HEADS = ("現況", "已知缺口", "會卡住")


def visible_lines(text):
    """(行號, 去掉行內程式碼後的文字, 是否在 summary 或正文)。開頭欄位只算 summary 區塊。"""
    lines = text.split("\n")
    out, in_fm, fm_key, fence = [], False, None, False
    for i, ln in enumerate(lines, 1):
        if i == 1 and ln.strip() == "---":
            in_fm = True
            continue
        if in_fm:
            if ln.strip() == "---":
                in_fm = False
                continue
            m = re.match(r"^([A-Za-z_]+):", ln)
            if m:
                fm_key = m.group(1)
            if fm_key == "summary" and ln.startswith("  "):
                out.append((i, INLINE_CODE.sub("", ln)))
            continue
        if ln.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence or ln.lstrip().startswith("|"):
            continue
        out.append((i, INLINE_CODE.sub("", ln)))
    return lines, out


_TESTS = None


def test_names():
    global _TESTS
    if _TESTS is None:
        _TESTS = set()
        for p in (ROOT / "tests").rglob("*.py"):
            _TESTS.update(re.findall(r"^\s*(?:async\s+)?def\s+(test_\w+)", p.read_text(encoding="utf-8"), re.M))
    return _TESTS


def resolve(spec):
    """'路徑::名稱' → (ast 節點 or None, 說明)。"""
    path, _, name = spec.rpartition("::")
    f = ROOT / path.strip()
    if not f.is_file():
        return None, f"找不到檔 {path}"
    tree = ast.parse(f.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                return node.value, ""
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return node.value, ""
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node, ""
    return None, f"{path} 裡沒有 {name}"


def members(node):
    """集合/列舉 → 成員名稱清單。"""
    if isinstance(node, ast.Call) and node.args:      # frozenset({...}) / tuple([...])
        node = node.args[0]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return [elt_name(e) for e in node.elts]
    if isinstance(node, ast.Dict):
        return [elt_name(k) for k in node.keys]
    if isinstance(node, ast.ClassDef):
        return [t.id for s in node.body if isinstance(s, ast.Assign) for t in s.targets
                if isinstance(t, ast.Name) and not t.id.startswith("_")]
    return None


def elt_name(e):
    if isinstance(e, ast.Constant):
        return str(e.value)
    if isinstance(e, ast.Attribute):
        return e.value.attr if e.attr == "value" and isinstance(e.value, ast.Attribute) else e.attr
    if isinstance(e, ast.Name):
        return e.id
    return ast.unparse(e)


def note_status(node):
    p = VAULT / (node.strip() + ".md")
    if not p.is_file():
        return None
    m = re.search(r"^status:\s*(\S+)", p.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def own_anchor(line):
    m = ANCHOR_RE.search(line)
    return (m.group(1) or m.group(2)).strip() if m else None


def check_note(rel, all_tags):
    text = (VAULT / rel).read_text(encoding="utf-8")
    raw, vis = visible_lines(text)
    findings = []
    # 小節範圍
    heads = [(i, len(HEAD_RE.match(l).group(1)), l) for i, l in enumerate(raw, 1) if HEAD_RE.match(l)]

    def section_of(lineno):
        cur = None
        for i, lvl, l in heads:
            if i <= lineno:
                cur = (i, lvl, l)
        if cur is None:
            return None
        end = len(raw) + 1
        for i, lvl, _ in heads:
            if i > cur[0] and lvl <= cur[1]:
                end = i
                break
        return cur[0], end, cur[2]

    retired_sections = []
    for ln, t in vis:
        for key, val in TAG_RE.findall(t):
            val = val.strip()
            loc = f"{rel}:{ln}"
            all_tags.append((key, loc))
            if key == "count":
                spec, _, n = val.rpartition("=")
                node, why = resolve(spec)
                ms = members(node) if node is not None else None
                if ms is None:
                    findings.append((loc, key, f"判不了:{why or '不是集合或列舉'}"))
                elif len(ms) != int(n):
                    findings.append((loc, key, f"句子說 {n} 個,程式現在 {len(ms)} 個:{', '.join(ms)}"))
                else:
                    findings.append((loc, key, f"OK({n} 個)"))
            elif key == "value":
                spec, _, lit = val.rpartition("=")
                node, why = resolve(spec)
                try:
                    cur = repr(ast.literal_eval(node)) if node is not None else None
                except ValueError:
                    cur = ast.unparse(node)
                if cur is None:
                    findings.append((loc, key, f"判不了:{why}"))
                elif cur.strip("'\"") != lit.strip().strip("'\""):
                    findings.append((loc, key, f"句子說 {lit},程式現在 {cur}"))
                else:
                    findings.append((loc, key, f"OK({cur})"))
            elif key == "enum":
                spec, _, want = val.rpartition("=")
                node, why = resolve(spec)
                ms = members(node) if node is not None else None
                if ms is None:
                    findings.append((loc, key, f"判不了:{why or '不是集合或列舉'}"))
                else:
                    want_s, have = {w.strip() for w in want.split(",") if w.strip()}, set(ms)
                    extra, gone = sorted(have - want_s), sorted(want_s - have)
                    msg = "OK" if not (extra or gone) else \
                        "句子沒列到程式的新成員:" + ",".join(extra) * bool(extra) + \
                        ("  句子列了已刪的成員:" + ",".join(gone) if gone else "")
                    findings.append((loc, key, msg))
            elif key.startswith("retire-when-"):
                kind = key[len("retire-when-"):]
                if kind == "file":
                    hit = (ROOT / val).is_file()
                elif kind == "symbol":
                    hit = resolve(val)[0] is not None
                else:
                    node, _, vals = val.partition("=")
                    hit = note_status(node) in {v.strip() for v in vals.split("|")}
                findings.append((loc, key, "撤除條件已成立:這條 RULE 應重審或撤掉,失去挑戰程式碼的效力" if hit
                                 else "撤除條件未成立(RULE 仍有效)"))
            elif key == "expect":
                ok = resolve(val)[0] is not None
                findings.append((loc, key, "OK" if ok else f"期望的 {val} 不在,同一行的 when-file 不算成立"))
            elif key in ("supersedes", "superseded-by"):
                node, _, anchor = val.partition("#")
                p = VAULT / (node.strip() + ".md")
                me = own_anchor(t)
                back_key = "superseded-by" if key == "supersedes" else "supersedes"
                if not p.is_file():
                    findings.append((loc, key, f"指到的筆記 {node} 不存在"))
                    continue
                want = f"[{back_key}:{rel[:-3]}#{me}]"
                other = p.read_text(encoding="utf-8")
                if want not in other:
                    findings.append((loc, key, f"只有一端:{node} 裡找不到回指 {want}"))
                else:
                    findings.append((loc, key, "OK(兩端成對)"))
            elif key == "retired":
                sec = section_of(ln)
                if sec:
                    retired_sections.append(sec)
            elif key == "test-gone":
                name = val.split("@")[0].strip()
                findings.append((loc, key, "OK(確實已不在)" if name not in test_names()
                                 else f"標成已刪,但 {name} 還在 tests/"))
    # 撤除小節裡的 [test:]
    for start, end, head in retired_sections:
        for ln, t in vis:
            if start <= ln < end:
                for names in TEST_RE.findall(t):
                    for name in (n.strip() for n in names.split(",")):
                        state = "已不在→應改成 [test-gone:名稱@提交]" if name not in test_names() \
                            else "還在→撤除小節裡掛活綁定,確認是不是該搬出撤除小節"
                        findings.append((f"{rel}:{ln}", "retired", f"{name}:{state}"))
    # done 計劃的現況類小節
    if rel.startswith("Projects/") and note_status(rel[:-3]) == "done":
        for i, lvl, l in heads:
            if any(h in l for h in SNAP_HEADS) and not re.search(r"\d{4}-\d{2}-\d{2}.*快照|快照", l):
                start, end, _ = section_of(i)
                body = "\n".join(raw[i:min(end - 1, i + 4)])
                if "[snapshot:" not in body and "快照" not in body:
                    findings.append((f"{rel}:{i}", "snapshot", f"計劃已 done,現況類小節「{l.strip('# ')}」沒標快照"))
    # 懸空 [test:](全篇)
    dangling = []
    for ln, t in vis:
        for names in TEST_RE.findall(t):
            for name in (n.strip() for n in names.split(",")):
                if re.fullmatch(r"test_\w+", name) and name not in test_names():
                    dangling.append((ln, name))
    return findings, dangling


def main():
    notes = sys.argv[2:] or sorted(str(p.relative_to(VAULT)) for p in VAULT.rglob("*.md"))
    all_tags, total_dangling = [], 0
    for rel in notes:
        findings, dangling = check_note(rel, all_tags)
        total_dangling += len(dangling)
        if findings or dangling:
            print(f"== {rel}")
            for loc, key, msg in findings:
                print(f"  [{key}] {loc.split(':')[-1]}: {msg}")
            if dangling:
                print(f"  [test] 懸空綁定 {len(dangling)} 個:" +
                      "、".join(f"{ln}:{n}" for ln, n in dangling[:6]) + (" …" if len(dangling) > 6 else ""))
    print(f"\n標籤 {len(all_tags)} 個;懸空 [test:] 共 {total_dangling} 個")


if __name__ == "__main__":
    main()
```
