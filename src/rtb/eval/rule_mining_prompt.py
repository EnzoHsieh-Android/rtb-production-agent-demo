"""規則模式探索的固定系統提示、探索集彙總表、位元組閘與可達性預檢(Phase 15 增量 1,計劃
[[Projects/RTB_Phase15AI找規則模式_計劃]]〈封閉條件語彙、彙總與窮舉基準〉〈拆增量〉第 1 項)。

- 這一增量**不呼叫模型**:只把系統提示文字定稿、建出要送的完整彙總表,量「系統提示 + 彙總表」的 UTF-8
  位元組數。完整表只列探索側達樣本下限的條件,欄序、捨入固定;比例沿用 `metrics.percent_text`(一位
  小數),平均差值以百分點四位小數(同一支 `percent_text`,精確分數 half-even);
  精確分數、保留側、逐日列、識別、時間戳與真相標籤都不進表。一批只准一次完整提示,
  不分塊、不截列:`B > 20480` 或達既有 48 KiB 就拒跑。
- 可達性預檢只在建立或更換評估版本時跑:固定三種子逐批記資料雜湊、兩側加額事件與排除原因、每個真模式
  與誘餌在探索/保留兩側的分母(有方向配對數與兩側相異廣告數,不看正負差)、完整提示位元組數;任一批
  超限或任一真相分母不可達就整體失敗,不得換種子或截列通過。結果寫死在 `PREFLIGHT_RECORD`(評估版本
  理由的機器可核對那份),測試重算比對。
- 評估版本雜湊涵蓋語彙模組的參數清單、系統提示、表頭、真相清單與三批資料雜湊;任何一項改動都要換
  `EVAL_VERSION` 並記理由與對 AI、窮舉雙方的預期影響。
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from rtb.analyzer import investigation as inv
from rtb.domain import metrics as m
from rtb.eval import rule_mining_baseline as b
from rtb.eval import rule_mining_history as h
from rtb.eval import rule_mining_vocab as v

SYSTEM_PROMPT = (
    "你是離線規則探勘助手。使用者內容是一份固定種子合成歷史的探索集彙總表。每列是一條封閉條件"
    "(一或兩個不同欄位的門檻代碼,以 & 相連),數字來自加額事件與對照廣告的配對:對照是同一側、"
    "同一 UTC 日、同一調整前三日轉換率區間、同一投放規模桶、整個觀察期沒有任何預算調整的廣告。"
    "配對差值是"
    "「加額組後三日轉換率減前三日轉換率」減去「對照組同樣的變化」,單位是百分點。\n"
    "任務:從表中挑出至多 10 條你認為在未見資料上最可能也成立的條件與方向,"
    "只能用表中出現的門檻代碼。\n"
    "方向:improve 表示加額後相對對照較可能改善,支持是正差筆數、反例是負差筆數;not_improve 相反,"
    "支持是負差筆數、反例是正差筆數。平手既不是支持也不是反例。\n"
    "輸出:只輸出一行緊湊的 UTF-8 JSON,直接寫中文原字,不要用 \\uXXXX 逸出,不要 Markdown,總長不超過"
    " 6000 位元組。格式:"
    '{"version":1,"suggestions":[{"clauses":[{"condition":"raise_pct",'
    '"threshold":"raise_pct:band_2"}],"direction":"improve","support":24,"counterexample":6,'
    '"confidence_note":"一句話"}]}\n'
    "規則:suggestions 至多 10 條;clauses 是一或兩個不同的 condition,condition 是門檻代碼冒號前的"
    "欄位代碼;同一條件不得同時押兩個方向,也不得重複;support 與 counterexample 照抄表中該方向的整數;"
    "confidence_note 至多 80 字,不含引號、反斜線或控制字元;不要加任何其他鍵。\n"
    "門檻代碼:\n"
    "day_type:weekday 加額 UTC 日是平日;day_type:weekend 是週六或週日\n"
    "raise_pct:band_1 加額幅度未滿 20%;raise_pct:band_2 20% 以上未滿 50%;"
    "raise_pct:band_3 50% 以上\n"
    "pre_cvr:band_1 調整前三日轉換率未滿 2%;pre_cvr:band_2 2% 以上未滿 5%;"
    "pre_cvr:band_3 5% 以上\n"
    "spend_ratio:band_1 調整前三日花費除以三天原日預算未滿 60%;spend_ratio:band_2 60% 以上未滿"
    " 90%;spend_ratio:band_3 90% 以上\n"
)
TABLE_HEADER = (
    f"彙總表 {v.EVAL_VERSION}:探索集;只列有方向配對至少 20 且其中相異加額廣告、相異對照廣告"
    "各至少 20 的條件;有方向配對 = 正差 + 負差,平手不計;相異廣告與日期只數有方向配對",
    "欄位:條件|加額事件|總配對|有方向配對|正差|負差|平手|相異加額廣告|相異對照廣告|相異日期|"
    "正差占有方向比例%|平均差值pp|無對照事件",
)
DIFF_PLACES = 4  # 平均差值:百分點四位小數,用 metrics.percent_text 同一套 half-even 捨入

# 首次預檢後寫死(評估版本理由的機器可核對那份);重跑須逐字相同
PREFLIGHT_RECORD: tuple[str, ...] = (
    '15001 資料 f820e1a486d8b35d 廣告 探索360/保留360 可推斷事件 探索340/保留301 '
     '表列 50 B=6057',
    '15001 排除 探索[incomplete_window=18 missing_value=11 '
     'overlapping_adjustment=18] 保留[incomplete_window=17 '
     'missing_value=9 overlapping_adjustment=32]',
    '15001 true_pattern raise_pct:band_2&spend_ratio:band_3 '
     'improve 探索[有方向48/加額廣告40/對照廣告48] 保留[有方向57/加額廣告45/對照廣告57]',
    '15001 true_pattern raise_pct:band_3 not_improve '
     '探索[有方向113/加額廣告88/對照廣告113] 保留[有方向84/加額廣告66/對照廣告84]',
    '15001 decoy_explore_only day_type:weekend&raise_pct:band_1 '
     'improve 探索[有方向45/加額廣告44/對照廣告45] 保留[有方向40/加額廣告38/對照廣告40]',
    '15001 decoy_correlated spend_ratio:band_1 not_improve '
     '探索[有方向138/加額廣告81/對照廣告138] 保留[有方向83/加額廣告45/對照廣告83]',
    '15002 資料 2a9e4aac0f4ec020 廣告 探索360/保留360 可推斷事件 探索318/保留339 '
     '表列 49 B=5975',
    '15002 排除 探索[incomplete_window=13 missing_value=12 '
     'overlapping_adjustment=16] 保留[incomplete_window=18 '
     'missing_value=7 overlapping_adjustment=13]',
    '15002 true_pattern raise_pct:band_2&spend_ratio:band_3 '
     'improve 探索[有方向49/加額廣告37/對照廣告49] 保留[有方向60/加額廣告45/對照廣告60]',
    '15002 true_pattern raise_pct:band_3 not_improve '
     '探索[有方向101/加額廣告74/對照廣告101] 保留[有方向116/加額廣告88/對照廣告116]',
    '15002 decoy_explore_only day_type:weekend&raise_pct:band_1 '
     'improve 探索[有方向34/加額廣告34/對照廣告34] 保留[有方向39/加額廣告38/對照廣告39]',
    '15002 decoy_correlated spend_ratio:band_1 not_improve '
     '探索[有方向109/加額廣告60/對照廣告109] 保留[有方向112/加額廣告67/對照廣告112]',
    '15003 資料 ba7d6ebc8a3a8b4d 廣告 探索360/保留360 可推斷事件 探索333/保留324 '
     '表列 50 B=6044',
    '15003 排除 探索[incomplete_window=14 missing_value=7 '
     'overlapping_adjustment=21] 保留[incomplete_window=10 '
     'missing_value=8 overlapping_adjustment=28]',
    '15003 true_pattern raise_pct:band_2&spend_ratio:band_3 '
     'improve 探索[有方向62/加額廣告49/對照廣告62] 保留[有方向53/加額廣告39/對照廣告53]',
    '15003 true_pattern raise_pct:band_3 not_improve '
     '探索[有方向109/加額廣告80/對照廣告109] 保留[有方向118/加額廣告82/對照廣告118]',
    '15003 decoy_explore_only day_type:weekend&raise_pct:band_1 '
     'improve 探索[有方向48/加額廣告46/對照廣告48] 保留[有方向27/加額廣告27/對照廣告27]',
    '15003 decoy_correlated spend_ratio:band_1 not_improve '
     '探索[有方向96/加額廣告54/對照廣告96] 保留[有方向117/加額廣告65/對照廣告117]',
)
EXPECTED_VERSION_SHA256 = "77b76d93a06d4690ab953c66d875feb12696c27e75cf37034d6b067b38162788"


def _row(stats: b.ConditionStats) -> str:
    counts = (stats.events, stats.pairs, stats.directed, stats.positive, stats.negative,
              stats.ties, stats.raised_ads, stats.control_ads, stats.dates)
    share = m.percent_text(m.exact_ratio(stats.positive, stats.directed))
    return "|".join([v.key_text(stats.key), *(str(c) for c in counts), share,
                     m.percent_text(stats.diff_sum / stats.directed, places=DIFF_PLACES),
                     str(stats.no_control)])


def summary_table(summary: b.SideSummary) -> str:
    """送模型的完整彙總表(使用者內容):表頭、事件層排除計數、達下限的每一條條件一列。"""
    excluded = " ".join(f"{reason}={count}" for reason, count in summary.exclusions.items())
    lines = [*TABLE_HEADER, f"可推斷加額事件 {summary.events};事件層排除:{excluded or '無'}"]
    lines += [_row(summary.stats[key]) for key in v.all_conditions()
              if b.meets_floor(summary.stats[key])]
    return "\n".join(lines) + "\n"


def prompt_bytes(table: str) -> int:
    """系統提示加使用者內容(彙總表)的 UTF-8 位元組數。"""
    return len(SYSTEM_PROMPT.encode()) + len(table.encode())


def gate_problem(size: int) -> str | None:
    """單次完整提示的位元組閘;超過就拒跑(不分塊、不截列)。預留額另在模型增量用 repo 函式重算。"""
    if size > v.PROMPT_BYTES_LIMIT:
        return f"完整提示 {size} 位元組超過本案上限 {v.PROMPT_BYTES_LIMIT}"
    if size >= v.GATEWAY_PROMPT_BYTES:
        return f"完整提示 {size} 位元組達既有上限 {v.GATEWAY_PROMPT_BYTES}"
    return None


def version_sha256() -> str:
    """評估版本雜湊:參數清單、系統提示、表頭、真相清單與三批預期資料雜湊。"""
    document = {
        "params": v.version_params(), "system_prompt": SYSTEM_PROMPT,
        "table_header": list(TABLE_HEADER),
        "truth": [[t.kind, [list(c) for c in t.clauses], t.direction] for t in h.TRUTH],
        "data_sha256": {str(seed): h.EXPECTED_DATA_SHA256[seed] for seed in v.SEEDS},
    }
    # 雜湊用的正規化 JSON 共用評估套件已在用的那一支(代碼審 a_1),不另拼參數
    return hashlib.sha256(inv.canonical_json(document).encode("ascii")).hexdigest()


# ---- 可達性預檢 ----
@dataclass(frozen=True)
class TruthReach:
    kind: str
    key: v.NormalizedKey
    explore: b.Reach
    holdout: b.Reach


@dataclass(frozen=True)
class SeedPreflight:
    seed: int
    data_sha256: str
    explore_ads: int
    holdout_ads: int
    explore_events: int
    holdout_events: int
    explore_exclusions: Mapping[str, int]
    holdout_exclusions: Mapping[str, int]
    table_rows: int
    prompt_bytes: int
    truths: tuple[TruthReach, ...]


def preflight_seed(seed: int) -> SeedPreflight:
    history = h.generate(seed)
    sides = h.split(ad.ad_id for ad in history.ads)
    explore, holdout = b.summarize(history, sides.explore), b.summarize(history, sides.holdout)
    table = summary_table(explore)
    truths = tuple(TruthReach(t.kind, t.key, b.reach_of(explore.stats[t.key[0]]),
                              b.reach_of(holdout.stats[t.key[0]])) for t in h.TRUTH)
    return SeedPreflight(
        seed=seed, data_sha256=h.data_sha256(history), explore_ads=len(sides.explore),
        holdout_ads=len(sides.holdout), explore_events=explore.events,
        holdout_events=holdout.events, explore_exclusions=explore.exclusions,
        holdout_exclusions=holdout.exclusions,
        table_rows=sum(1 for s in explore.stats.values() if b.meets_floor(s)),
        prompt_bytes=prompt_bytes(table), truths=truths)


def preflight() -> tuple[SeedPreflight, ...]:
    """固定三種子逐批預檢(不收別的種子)。"""
    return tuple(preflight_seed(seed) for seed in v.SEEDS)


def preflight_problems(results: Sequence[SeedPreflight]) -> tuple[str, ...]:
    """任一批超限、任一真相分母不可達、或不是恰好三個固定種子,就回問題清單(空 = 通過)。"""
    problems = []
    if tuple(r.seed for r in results) != v.SEEDS:
        problems.append(f"預檢須恰好依序列出固定種子 {v.SEEDS}")
    for result in results:
        gate = gate_problem(result.prompt_bytes)
        if gate is not None:
            problems.append(f"{result.seed}: {gate}")
        for truth in result.truths:
            for side, reach in (("探索", truth.explore), ("保留", truth.holdout)):
                if not reach.meets:
                    problems.append(
                        f"{result.seed}: {truth.kind} {v.key_text(truth.key[0])} "
                        f"{truth.key[1]} {side}側分母不可達({_reach_text(reach)})")
    return tuple(problems)


def _reach_text(reach: b.Reach) -> str:
    return f"有方向{reach.directed}/加額廣告{reach.raised_ads}/對照廣告{reach.control_ads}"


def _counts_text(counts: Mapping[str, int]) -> str:
    return " ".join(f"{reason}={count}" for reason, count in counts.items()) or "無"


def preflight_record(results: Sequence[SeedPreflight]) -> tuple[str, ...]:
    """預檢結果的固定文字(寫進評估版本理由):每批一行總覽、一行排除、每個真相一行分母。"""
    lines = []
    for r in results:
        lines.append(f"{r.seed} 資料 {r.data_sha256[:16]} 廣告 探索{r.explore_ads}/保留"
                     f"{r.holdout_ads} 可推斷事件 探索{r.explore_events}/保留{r.holdout_events} "
                     f"表列 {r.table_rows} B={r.prompt_bytes}")
        lines.append(f"{r.seed} 排除 探索[{_counts_text(r.explore_exclusions)}] "
                     f"保留[{_counts_text(r.holdout_exclusions)}]")
        lines += [f"{r.seed} {t.kind} {v.key_text(t.key[0])} {t.key[1]} "
                  f"探索[{_reach_text(t.explore)}] 保留[{_reach_text(t.holdout)}]"
                  for t in r.truths]
    return tuple(lines)
