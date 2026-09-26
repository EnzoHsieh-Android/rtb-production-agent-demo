severity: blocker

## 發現 1：負數長窗轉換仍會產生加預算提案
severity: blocker
blocking: 是

引句:「+    decided = rules.decide(worth_input, queries, now)」

這次接線讓九條結果直接進正式提案。1d 轉換數為 `-1`、7d 為 `5` 時，讀取層把負數欄位略過跨窗核對，收據仍算成功；第 6 條只檢查「任一長窗轉換數大於零」，於是回 `late_conversions`、建立提案。這違反計劃對第 3–9 條「四查詢須有有效原始結果」的要求。相關路徑見 `src/rtb/analyzer/dsp_client.py:383`、`src/rtb/analyzer/dsp_client.py:394`、`src/rtb/domain/nine_rules.py:357`；條款見 `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase14正式規則照九條判斷_計劃.md:83`。

最小重現（唯讀執行，輸出已取得）：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -B -c '
from datetime import UTC, datetime
from types import MappingProxyType
from rtb.analyzer import dsp_client, policy
from rtb.analyzer.flow import ProposalDecision
from rtb.analyzer.task_store import TaskRow
from rtb.domain import nine_rules as r
from rtb.domain.evidence import Evidence, EvidenceKind, TrustClass
from rtb.domain.task_state import TaskState
n = datetime(2026, 9, 26, tzinfo=UTC)
one = dict(impressions=10, clicks=10, conversions=-1, spend=0.0, revenue=0.0)
week = dict(impressions=70, clicks=70, conversions=5, spend=0.0, revenue=0.0)
rows = [dict(days_ago=i, impressions=10, clicks=10, conversions=int(i <= 5),
             spend=0.0, revenue=0.0, no_data=False) for i in range(1, 8)]
q = r.RuleEvidence(r.LongerWindow(r.Window(**one), r.Window(**week)),
                   r.ChangeHistory(()), r.DailyTrend(tuple(r.DailyRow(**x) for x in rows), n),
                   r.PastAdjustments(()))
def ev(k, p, v=None):
    return Evidence(k.value, "t1", k, "dsp", n, v, "0"*64,
                    TrustClass.TRUSTED, MappingProxyType(p))
e = (ev(EvidenceKind.CAMPAIGN_STATE,
        dict(id="c1", status="active", budget=2400, version=1), 1),
     ev(EvidenceKind.METRICS,
        dict(campaign_id="c1", window="1h", impressions=100, clicks=10,
             conversions=0, spend=1.0, revenue=0.0)))
t = TaskRow("t1", 1, TaskState.ANALYZING, "c1", None, None, n)
decision, _ = policy.explain(t, e, n, candidate=None,
                             allowed=policy.ValidatedCells.NONE, queries=q)
print(dsp_client.check_longer_window(one, week) is not None)
print(dsp_client._daily_matches(rows, one, week))
print(isinstance(decision, ProposalDecision))
'
# True
# True
# True
```

負數必須使所需長窗結果成為證據不足；不能讓另一個正數長窗把不合理資料轉成提案。

## 發現 2：C 步先讀現況，查詢期間的改預算逃過 A／C 比對
severity: major
blocking: 是

引句:「+        evidence = base_source(task, now) if rule_round.STEP_BASE[step] else ()」

C 步先讀現況與 1h，再讀長窗與逐日。若另一方恰在 C 的現況讀取後改預算，A／C 仍都是舊版本；B 的歷史也在變更前讀完。後續查詢可取得新資料且彼此一致，定案便可能建立帶舊版本的提案。執行端版本閘會擋寫入，但分析端已做出錯誤提案。計劃指定 C 讀「逐日＋1d＋7d＋**重讀**現況與 1h」，目的正是讓定案前的 A／C 比對涵蓋查詢期間；見 `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase14正式規則照九條判斷_計劃.md:153`。

最小重現證實實際呼叫順序：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -B -c '
from datetime import UTC, datetime
from rtb.analyzer import instrumented, rule_round, dsp_client
from rtb.analyzer.task_store import TaskRow
from rtb.domain.task_state import TaskState
calls = []
rule_round.collect_plan = lambda *a: (1, rule_round.Step.C)
dsp_client.make_client = lambda *a, **kw: lambda task, now: (calls.append("base") or ())
dsp_client.make_query_reader = lambda *a, **kw: lambda task, option: (
    calls.append(option) or dsp_client.QueryRead(None, "not_found"))
n = datetime(2026, 9, 26, tzinfo=UTC)
instrumented.rule_source(object(), "http://unused", 3.0)(
    TaskRow("t1", 1, TaskState.COLLECTING_EVIDENCE, "c1", None, None, n), n)
print(calls)
'
# ['base', 'check_longer_window', 'check_daily_trend']
```

以同輪 A／C 舊版本及四筆有效查詢資料呼叫 `rule_round.decide`，另得到 `proposal = True, version = 1`；上述讀取順序容許查詢開始前實際版本已升為 2。應把 C 的現況重讀放在追加查詢之後，並維持五次讀取的租約上界。

## 發現 3：政策切換沿用舊政策的變動重來次數
severity: major
blocking: 是

引句:「+    for _seq, event in reversed(events):」

`progress()` 從所有舊事件倒數 `RESTART_CHANGED`，沒有以政策版本或新輪起點切斷。若舊政策已因變動重來兩次，恰在 `COLLECTING_EVIDENCE` 切到新政策，`collect_plan()`會直接開新輪 A，沒有插入可重設計數的事件。新政策第一次 A／C 變動就會走 `CHANGED_LIMIT`、以證據不足結案，沒有得到條款允許的兩次重讀。位置見 `src/rtb/analyzer/rule_round.py:103`、`src/rtb/analyzer/rule_round.py:133`、`src/rtb/analyzer/rule_round.py:291`。

最小重現：以兩個舊政策完整輪次及一個新政策完整輪次的已提交列餵給 `progress/decide`；新輪只發生第一次變動，輸出卻是：

```text
new-policy changes = 2
first new-policy mismatch = changed_limit judged_insufficient
```

可用下列唯讀指令重現核心計數：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -B -c '
from datetime import UTC, datetime
from rtb.analyzer import rule_round as rr
from rtb.analyzer.task_store import RuleStep, RuleEvent
from rtb.domain.proposal import POLICY_VERSION
n = datetime(2026, 9, 26, tzinfo=UTC)
class Reads:
    def rule_steps(self, t):
        return tuple((s, RuleStep(r, step, version, n)) for s, r, step, version in (
            (2,1,"A","old-v1"), (4,1,"B","old-v1"), (6,1,"C","old-v1"),
            (9,2,"A","old-v1"), (11,2,"B","old-v1"), (13,2,"C","old-v1"),
            (16,3,"A",POLICY_VERSION), (18,3,"B",POLICY_VERSION),
            (20,3,"C",POLICY_VERSION)))
    def rule_events(self, t):
        return tuple((s, RuleEvent(r, event)) for s, r, event in (
            (3,1,"continue"), (5,1,"continue"), (7,1,"restart_changed"),
            (10,2,"continue"), (12,2,"continue"), (14,2,"restart_changed"),
            (17,3,"continue"), (19,3,"continue")))
print(rr.progress(Reads(), "t1").changes)
'
# 2
```

這個 `2` 屬於舊政策，卻直接供新輪的上限判斷使用。計數應以本政策連續的變動重來事件為界。

## 發現 4：新檔對 AI 退回的說明與實作相反
severity: minor
blocking: 否

引句:「+(AI 提案否決與退回全量重讀是增量 3)。」

`rule_round.py` 檔頭說 AI 路徑尚未使用規則輪、退回全量重讀屬增量 3；這份 diff 同時讓 `investigation_source()` 在 AI 已用過後呼叫 `rule_source()`，並讓 `runner` 在 AI 模式傳入 `rule_decide`。計劃 `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase14正式規則照九條判斷_計劃.md:160` 也明列退回全量重讀屬 2b。這是新檔內部不一致，會誤導後續維護者判斷在途任務的路徑。

## LUMOS-IMPACT 固定席判定

| 固定席節點 | 本 diff 對其宣稱的影響 |
|---|---|
| `Systems/一鍵展示` | **間接受影響**：會展示發現 1 的錯誤提案；其流程圖展開與事件鍵合約未見破壞。 |
| `Systems/分析行程流程與檢查點` | **受影響**：發現 2 的 C 讀序與發現 3 的跨政策計數破壞規則輪所宣稱的重讀／重進入行為；歷史列同交易與租約圍籬仍在。 |
| `Systems/正式九條判斷領域規則` | **受影響**：負數長窗未被診斷為無格證據不足，經新正式接線成為提案。 |
| `Systems/評估與Jev決策點` | **受影響**：九條規則答案同樣會把發現 1 的資料判成 `worth`；Phase 10 明傳缺四查詢的轉接本身未見破壞。 |
| `Systems/任務流程領域模型` | **間接受影響**：發現 2 使分析端可能使用已變版本的現況建提案；提案解析、狀態機與領域匯入白名單未見破壞。 |
| `Systems/模型用戶端` | **不影響其宣稱**：變更沒有改模型閘道與花費上限；AI 退回的後續規則輪另屬發現 4 的說明錯誤。 |
| `Systems/Mock-DSP` | **不影響其宣稱**：本 source diff 沒改 DSP 寫入、冪等鍵或版本拒收；發現 2 的舊版提案仍由其版本閘拒收。 |
| `Systems/共用行程基礎` | **不影響其宣稱**：新讀取仍經既有 HTTP 用戶端，未引入故障注入標頭或改動伺服器綁定位址。 |
| `Systems/執行迴圈` | **不影響其執行合約**：發現 1、2 發生在分析提案前；執行端的冪等、重驗與總曝險閘仍在。 |
| `Systems/提案收件口` | **不影響其收件合約**：同修訂去重、政策版本與重放重驗未改；它不能替分析端修正錯誤提案。 |
| `Systems/確定性指標計算` | **整合處受影響**：負數長窗欄位在精確指標層屬不合理值，但正式九條接線跳過該診斷而採另一個正數欄位提案；指標函式本身未改。 |

驗證限制：`pytest` 在唯讀沙箱因無可用暫存目錄，尚未載入測試就失敗；上述 Python 最小重現均以 `-B` 唯讀執行。已知的展示錄製重錄失敗未列為發現。