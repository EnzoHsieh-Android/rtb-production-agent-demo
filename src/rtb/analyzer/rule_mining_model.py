"""規則模式探索的分析端模型窄入口(Phase 15 增量 2,計劃 [[Projects/RTB_Phase15AI找規則模式_計劃]]
〈模型建議、機械核對與人讀報告〉〈拆增量〉2)。

只做一件事:收**已經組好的**系統提示與探索集彙總表文字,加上閘道設定,用字面 `Caller.RULE_MINING`
開既有模型閘道、呼叫模型**一次**,把原始回覆或失敗類別交回呼叫端。

- 不讀評估集、不解析回覆、不核對數字(那些在評估端的純函式);不匯入流程推進、決策規則、分析端驅動、
  DSP 或收件口用戶端。准匯入模型閘道的分析端模組寫死在邊界測試(`GATE_USERS`,[S1100] 窄增這一支)。
- 模式、帳本、錄製目錄都在閘道判一次:預設錄製重播,缺錄製就是「沒有錄製」,不會改走即時;即時要
  使用者明確設 `RTB_MODEL_LIVE=1` 並過閘道的其餘前置條件。
- 一批只准一次完整提示:系統提示加彙總表超過本案 20480 位元組就在呼叫前拒絕(不分塊、不截列、不記帳)。
  輸出上限 6144 token、逾時 60 秒,寫死在這裡,評估端的同名常數由測試核對一致。
- 預留額不另寫算式:`RULE_MINING` 列在花費帳的計入上限呼叫者,送出前由花費帳在單一寫入交易裡用模型
  用戶端的預留函式(`modelcore.reservation_nanousd`)照當時價目與已用重算,超過每展示或每月剩餘上限
  就記「本地上限拒絕」、不呼叫([S1514])。
- 任何非 `ok` 結果(逾時、上限拒絕、額度用完、超支、暫時性錯誤、花費帳忙碌、沒有錄製…)都以失敗類別
  交回,**不重試**;評估端記「呼叫失敗」。閘道拒絕(`GateRefused`)、不認得的模型與停止訊號照原樣往外丟。
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from rtb.analyzer import modelgate

MAX_OUTPUT_TOKENS = 6144
TIMEOUT_SECONDS = 60.0
PROMPT_BYTES_LIMIT = 20480  # 本案:系統提示加使用者內容的 UTF-8 位元組

GateOpener = Callable[..., modelgate.Gate]


class PromptTooLarge(ValueError):
    """完整提示超過本案上限:呼叫前拒絕,沒有開閘道、沒有記帳。"""


@dataclass(frozen=True)
class GateConfig:
    """開閘道要的設定(沿用 `modelgate.open_gate` 的參數;評估端從命令列組好傳進來)。"""

    environ: Mapping[str, str]
    demo_id: str | None
    ledger: Path | None
    recordings: Path | None
    batch_id: str | None = None
    recorded_ledger: Path | None = None


@dataclass(frozen=True)
class Reply:
    """一次呼叫的結果:成功帶原始回覆文字,失敗帶失敗類別與原因(不帶文字)。"""

    outcome: str  # modelgate.Outcome 的值
    mode: str  # 閘道判出的模式
    text: str | None
    source: str | None
    key: str | None  # 錄製鍵(成功時由模型用戶端回報)
    batch_id: str | None
    list_nanousd: int
    problem: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome == modelgate.Outcome.OK.value and self.text is not None


def prompt_bytes(system: str, table: str) -> int:
    return len(system.encode("utf-8")) + len(table.encode("utf-8"))


def suggest(system: str, table: str, config: GateConfig, *,
            open_gate: GateOpener = modelgate.open_gate,
            notify: Callable[[str], None] | None = None) -> Reply:
    """開規則模式探索的閘道、送出一次(或讀一次錄製)。完整提示過大丟 `PromptTooLarge`;閘道拒絕丟
    `modelgate.GateRefused`;模型呼叫的每一類失敗都收成 `Reply`(outcome 不是 ok),不重試。"""
    size = prompt_bytes(system, table)
    if size > PROMPT_BYTES_LIMIT:
        raise PromptTooLarge(f"完整提示 {size} 位元組超過上限 {PROMPT_BYTES_LIMIT},不呼叫")
    gate = open_gate(config.environ, caller=modelgate.Caller.RULE_MINING, demo_id=config.demo_id,
                     ledger=config.ledger, recordings=config.recordings,
                     batch_id=config.batch_id, recorded_ledger=config.recorded_ledger,
                     notify=notify)
    try:
        result = gate.complete(system, table, max_output_tokens=MAX_OUTPUT_TOKENS,
                               timeout_seconds=TIMEOUT_SECONDS)
    except modelgate.ModelCallFailed as failed:
        return Reply(outcome=failed.outcome.value, mode=gate.mode.value, text=None, source=None,
                     key=None, batch_id=failed.recording_batch_id,
                     list_nanousd=failed.list_nanousd, problem=str(failed))
    return Reply(outcome=modelgate.Outcome.OK.value, mode=gate.mode.value, text=result.text,
                 source=result.source.value, key=result.key, batch_id=result.batch_id,
                 list_nanousd=result.list_nanousd)
