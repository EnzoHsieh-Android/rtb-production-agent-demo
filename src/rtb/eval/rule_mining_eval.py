"""規則模式探索的單一種子執行(Phase 15 增量 2,計劃 [[Projects/RTB_Phase15AI找規則模式_計劃]]
〈模型建議、機械核對與人讀報告〉〈拆增量〉2)。命令列、批次驗收與報告是增量 3。

一個固定種子一批:生成歷史 → 探索集彙總表(增量 1)→ 完整提示位元組閘 → 經分析端窄入口
`rtb.analyzer.rule_mining_model` 呼叫模型**一次**(方向同 Phase 13 評估執行器依賴 AI 決策函式;
評估端不直接匯入模型閘道)→ 封閉 JSON 解析、去重、同探索集重算、保留側判定。

- 非 `ok` 的呼叫(缺錄製、逾時、上限拒絕、額度、超支、暫時性錯誤、花費帳忙碌…)一律記「呼叫失敗」,
  不解析、不計分、不重試;同種子要重錄換新展示編號(增量 3 的命令列管)。
- 提示超過本案上限就在呼叫前拒跑(不分塊、不截列)。
- 輸出只有這一批的核對結果;沒有正式決策、提案、DSP 寫入或 Issue 的路徑。匯入它就算送出點
  (邊界測試 `SENDING_ENTRIES`)。停用模型探勘時連同分析端窄入口一起撤,歷史錄製鍵留在
  `rule_mining_recordings`。
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from rtb.analyzer import rule_mining_model as rmm
from rtb.eval import rule_mining_check as c
from rtb.eval import rule_mining_history as h
from rtb.eval import rule_mining_prompt as p
from rtb.eval import rule_mining_recordings as r

CALL_OK = "ok"
CALL_FAILED = "呼叫失敗"
Ask = Callable[[str, str], rmm.Reply]


class PromptRefused(ValueError):
    """完整提示過大:拒跑,不呼叫。"""


@dataclass(frozen=True)
class Prepared:
    """一個種子送出前的一切:歷史、兩側重算器、完整彙總表與位元組數。"""

    seed: int
    history: h.History
    explore: c.Recounter
    holdout: c.Recounter
    table: str
    prompt_bytes: int


def prepare(seed: int) -> Prepared:
    """組表走預檢同一支(`seed_inputs`,探索側建表);重算器照同一份切側各建一個。"""
    inputs = p.seed_inputs(seed)
    return Prepared(seed, inputs.history, c.Recounter(inputs.history, inputs.sides.explore),
                    c.Recounter(inputs.history, inputs.sides.holdout), inputs.table,
                    p.prompt_bytes(inputs.table))


@dataclass(frozen=True)
class SeedRun:
    seed: int
    expected_key: str  # 依這份提示重算的錄製鍵(缺錄製時給人對)
    reply: rmm.Reply
    verification: c.Verification | None  # 呼叫失敗時沒有

    @property
    def status(self) -> str:
        return CALL_OK if self.reply.ok else CALL_FAILED


def run(prepared: Prepared, ask: Ask, *, model: str | None = None) -> SeedRun:
    """送出這一批(至多一次)並核對。提示過大丟 `PromptRefused`,什麼都沒送。"""
    problem = p.gate_problem(prepared.prompt_bytes)
    if problem is not None:
        raise PromptRefused(problem)
    expected = r.expected_key(p.SYSTEM_PROMPT, prepared.table, model)
    reply = ask(p.SYSTEM_PROMPT, prepared.table)
    if not reply.ok or reply.text is None:
        return SeedRun(prepared.seed, expected, reply, None)
    verification = c.verify(c.parse_reply(reply.text), prepared.explore, prepared.holdout)
    return SeedRun(prepared.seed, expected, reply, verification)


def gate_ask(config: rmm.GateConfig, notify: Callable[[str], None] | None = None) -> Ask:
    """經分析端窄入口送出:每呼叫一次就開一次規則模式探索的閘道、送一次。"""
    return partial(rmm.suggest, config=config, notify=notify)
