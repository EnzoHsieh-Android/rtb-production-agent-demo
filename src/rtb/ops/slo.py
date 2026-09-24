"""服務水準指標的設定表、錯誤預算與多窗口燒損告警(Phase 9 增量 3)。只讀。

名詞(交接文件 14.5):
- 服務水準指標 = 好事件數 ÷ 有效事件數,在一個時間窗內算;有效事件數為 0 是無樣本。
- 錯誤預算 = (1 - 目標)乘 週期內有效事件數;剩餘預算 = 錯誤預算 - 週期內壞事件數,可以是負數(超支)。
- 燒損率 = 窗內壞事件比例 ÷(1 - 目標):消耗錯誤預算的速度,1 表示照這個速度剛好在週期結束時用完。
- 目標為零的兩條(未授權副作用、重複有害副作用):錯誤預算固定 0,不算燒損率;週期內出現一個壞事件
  就是違規,直到它滑出週期。

燒損照 Google SRE Workbook〈Alerting on SLOs〉的多窗口多燒損率:長窗與短窗的燒損率都大於等於門檻才
告警(短窗讓事故停止後很快解除,長窗避免短暫突波);長窗有效事件少於最少樣本回樣本不足、不判告警;
短窗沒有有效事件時燒損率當 0。示範時所有時間窗(週期與燒損窗)等比縮短 60 倍,期限不縮短。全部是
示範值(代使用者裁定、待使用者覆核),不代表系統的真實能力。

評估器不存告警狀態、不自己讀時鐘:每個窗的時間從傳入的「現在」與設定表算出,交給計數函式。數字用
分數算,剛好等於門檻就是等於(不被浮點誤差推到門檻下)。

每條各自算:一條的資料來源讀不到(DSP 讀不到那一類)只把那一條標資料來源缺並記下原因,另外五條照算;
程式錯誤、資料庫毀損、找不到資料庫檔、資料庫還沒升級一律往外丟,不吞(代碼審第 2 輪)。命令列入口
在任何一條缺資料或出錯時回「不完整」的結束代碼,優先於不穩定。目標為零的兩條:已證實的
違規優先,只有週期內沒看到壞事件、又有子窗缺資料才回「不知道」(代碼審第 1 輪)。
"""

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any, TextIO

from rtb.capabilitykit import AuditKeyTooLong, read_audit_key
from rtb.executor.inbox_store import DatabaseNotUpgraded
from rtb.ops import sli
from rtb.ops.cli import EXIT_BAD_ARGUMENTS as EXIT_BAD_ARGUMENTS  # 參數錯(7,維運套件共用)
from rtb.ops.cli import Parser, aware_time
from rtb.ops.side_effects import DspUnreadable, Tally, reason

SCALE = 60  # 示範縮短倍數;正式環境改成 1
PERIOD = timedelta(days=30)
MIN_SAMPLES = 10
DAY = timedelta(hours=24)  # 週期統計切子窗的上限(跟窗內統計的窗長上限一致)
EXIT_OK = 0
EXIT_NO_DATABASE = 2
EXIT_NOT_UPGRADED = 3
EXIT_UNSTABLE = 5  # 沿用指標的固定結束代碼:有一條跨資料庫讀了三輪都不同,照樣印出最後一輪
# 有指標缺資料或出錯(代使用者裁定,代碼審第 2 輪):照樣印出每一條,哪幾條、為什麼印在標準錯誤;
# 跟不穩定同時成立時回這個。8 沒被任何命令列用掉(維運套件 0/2/3/4/5/6/7,執行端 2/3/4/5/6/7)
EXIT_INCOMPLETE = 8
EXIT_BAD_CONFIG = 6  # 沿用指標的代碼:設定錯誤(稽核金鑰超過長度上限)

Counter = Callable[[str, datetime, datetime], Tally]


@dataclass(frozen=True)
class Slo:
    """一條服務水準指標的定義(設定表的一筆)。target 是好事件比例的目標;目標為零的兩條記成 1
    (壞事件目標為零)。deadline 只有期限型兩條有。"""

    name: str
    formula: str
    good: str
    valid: str
    window: str
    exclusions: str
    target: Fraction
    deadline: timedelta | None
    demo: bool = True

    @property
    def zero_target(self) -> bool:
        return self.target == 1


@dataclass(frozen=True)
class Burn:
    name: str
    long: timedelta
    short: timedelta
    threshold: Fraction


_EVENT_WINDOW = "依事件時間歸窗;窗含起點、不含終點"
SLOS: tuple[Slo, ...] = (
    Slo("safe_completion", "好事件 ÷ 窗內終點事件",
        "已交給執行;已擋下且原因是業務上該擋的(同一操作先前已失敗以外的每一種)",
        "窗內的終點事件(已交給執行、已擋下、死信、已過期)", _EVENT_WINDOW,
        "被取代;不是終點的事件(待核可、放回、重放放回)。死信後重放再到終點,兩個終點事件各算一次",
        Fraction(99, 100), None),
    Slo("unknown_reconciled_in_time", "期限內結案的鍵 ÷ 進過結果不明的鍵",
        "第一次進結果不明後 10 分鐘內結案(剛好第 10 分鐘也算)",
        "每把進過結果不明的鍵,事件時間 = min(結案, 第一次進結果不明 + 10 分鐘)", _EVENT_WINDOW,
        "期限前已轉人工的鍵(已交給人);期限後才轉人工的照算壞",
        Fraction(99, 100), timedelta(minutes=10)),
    Slo("end_to_end_handoff", "2 分鐘內交給執行的 ÷ 窗內已交給執行的",
        "從根任務在分析端建立到已交給執行,扣掉鏈上的人工核可與死信等待,不超過 2 分鐘",
        "窗內每一個已交給執行的終點事件", _EVENT_WINDOW,
        "沒交給執行的鏈;接不上根任務的另報、不計入;兩邊時鐘算出負值的另報時鐘異常、不計入",
        Fraction(95, 100), None),
    Slo("queue_wait", "期限內被取件的 ÷ 收件的提案",
        "收件後 30 秒內第一次被取件(剛好第 30 秒也算)",
        "每份收件的提案,事件時間 = min(第一次取件, 收件 + 30 秒)", _EVENT_WINDOW,
        "無(之後被取件、被標成已過期或被取代都不改已定的壞事件)",
        Fraction(99, 100), timedelta(seconds=30)),
    Slo("unauthorized_side_effects", "合法的寫入 ÷ 可核對的執行端寫入(目標為零:壞事件為零)",
        "逐項核對都合法:經過開始一筆、授權範圍、單一廣告上限、比例與總曝險(或有核可)、政策版本",
        "窗內 DSP 提交、冪等鍵是執行端格式的寫入,扣掉無法核對的",
        "依 DSP 提交時間歸窗;窗含起點、不含終點",
        "別的寫入者直接改 DSP;無法核對的(另報份數)", Fraction(1), None),
    Slo("harmful_duplicates", "不重複的寫入 ÷ 找得到第一列的執行端寫入(目標為零:壞事件為零)",
        "同一份提案內容在 DSP 的第一筆提交",
        "窗內 DSP 提交、冪等鍵是執行端格式的寫入,扣掉找不到第一列的", "依那一筆自己的提交時間歸窗",
        "找不到第一列的(無法核對,另報份數)", Fraction(1), None),
)
BURNS: tuple[Burn, ...] = (
    Burn("fast", timedelta(hours=1), timedelta(minutes=5), Fraction("14.4")),
    Burn("slow", timedelta(hours=6), timedelta(minutes=30), Fraction(6)),
)


def scaled(length: timedelta) -> timedelta:
    return length / SCALE


def burn_rate(good: int, valid: int, target: Fraction) -> Fraction:
    """窗內壞事件比例 ÷(1 - 目標);沒有有效事件當 0(沒有證據顯示還在燒)。"""
    if valid == 0:
        return Fraction(0)
    return Fraction(valid - good, valid) / (1 - target)


def error_budget(valid: int, target: Fraction) -> Fraction:
    return (1 - target) * valid


def remaining_budget(valid: int, bad: int, target: Fraction) -> Fraction:
    return error_budget(valid, target) - bad


def period_windows(end: datetime, period: timedelta) -> tuple[tuple[datetime, datetime], ...]:
    """把週期 [end - period, end) 切成每段不超過 24 小時、首尾相接的子窗(由舊到新)。"""
    start = end - period
    pieces: list[tuple[datetime, datetime]] = []
    while start < end:
        piece_end = min(start + DAY, end)
        pieces.append((start, piece_end))
        start = piece_end
    return tuple(pieces)


def period_tally(name: str, end: datetime, period: timedelta, counter: Counter) -> Tally:
    """週期統計:逐段查子窗再相加(事件型計數可加,子窗互不重疊)。"""
    parts = [counter(name, since, until) for since, until in period_windows(end, period)]
    return Tally(sum(p.good for p in parts), sum(p.valid for p in parts),
                 tuple(at for p in parts for at in p.bad_at), sum(p.unverifiable for p in parts),
                 sum(p.unlinked for p in parts), sum(p.clock_anomaly for p in parts),
                 any(p.missing for p in parts), all(p.stable for p in parts),
                 next((p.reason for p in parts if p.reason), None))  # 第一個讀不到的原因


@dataclass(frozen=True)
class WindowBurn:
    good: int
    valid: int
    burn: Fraction


@dataclass(frozen=True)
class Alerting:
    fired: bool
    insufficient: bool
    long: WindowBurn
    short: WindowBurn


@dataclass(frozen=True)
class SloStatus:
    """一條的狀態。有目標的四條:快燒、慢燒、週期的錯誤預算與剩餘預算;目標為零的兩條:違規、週期內
    壞事件數、最近一個壞事件時間(燒損為空)。missing:資料來源讀不到(至少一個窗沒算);error:讀不
    到的原因(週期裡第一個讀不到的窗;不含金鑰與標頭值)。stable 為假:有窗跨資料庫讀了三輪都不同。"""

    name: str
    fast: Alerting | None
    slow: Alerting | None
    period_good: int
    period_valid: int
    period_bad: int
    budget: Fraction
    remaining: Fraction
    violating: bool | None
    last_bad: str | None
    unverifiable: int
    unlinked: int
    clock_anomaly: int
    missing: bool
    stable: bool = True
    error: str | None = None


def _alerting(spec: Slo, burn: Burn, now: datetime, counter: Counter) -> Alerting:
    long = counter(spec.name, now - scaled(burn.long), now)
    short = counter(spec.name, now - scaled(burn.short), now)
    windows = (WindowBurn(long.good, long.valid, burn_rate(long.good, long.valid, spec.target)),
               WindowBurn(short.good, short.valid,
                          burn_rate(short.good, short.valid, spec.target)))
    insufficient = long.valid < MIN_SAMPLES
    fired = not insufficient and all(w.burn >= burn.threshold for w in windows)
    return Alerting(fired, insufficient, *windows)


def _violating(period: Tally) -> bool | None:
    """目標為零:週期內有壞事件就是違規(已證實,不因別的子窗缺資料而改判);沒有壞事件又有子窗缺
    資料才是不知道。"""
    if period.bad > 0:
        return True
    return None if period.missing else False


def _status(spec: Slo, now: datetime, counter: Counter) -> SloStatus:
    fast = slow = None
    if not spec.zero_target:
        fast, slow = (_alerting(spec, burn, now, counter) for burn in BURNS)
    period = period_tally(spec.name, now, scaled(PERIOD), counter)
    return SloStatus(
        spec.name, fast, slow, period.good, period.valid, period.bad,
        error_budget(period.valid, spec.target),
        remaining_budget(period.valid, period.bad, spec.target),
        _violating(period) if spec.zero_target else None,
        max(period.bad_at) if period.bad_at else None, period.unverifiable,
        period.unlinked, period.clock_anomaly, period.missing, period.stable, period.reason)


def evaluate(now: datetime, *, counter: Counter) -> tuple[SloStatus, ...]:
    """每條一份狀態。查詢次數固定:有目標的四條各查快燒、慢燒兩組各兩個窗(共 16 次)加週期統計,
    目標為零的兩條各查週期統計。只隔離資料來源讀不到(見模組說明),其餘例外往外丟。"""
    statuses = []
    for spec in SLOS:
        try:
            statuses.append(_status(spec, now, counter))
        # 現行六條都在自己裡面把讀不到轉成資料來源缺(原因帶在計數上),這裡接不到;留著給之後
        # 沒自己處理 DSP 例外的指標當防線:一條讀不到不拖垮另外五條,原因記在這一條的狀態裡
        except DspUnreadable as exc:
            statuses.append(SloStatus(spec.name, None, None, 0, 0, 0, Fraction(0), Fraction(0),
                                      None, None, 0, 0, 0, True,
                                      error=reason(exc)))
    return tuple(statuses)


# ---- 輸出與命令列入口 ----
def _plain(value: Any) -> Any:
    if isinstance(value, Fraction):
        return float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def to_primitives(statuses: tuple[SloStatus, ...]) -> dict[str, Any]:
    return {"slos": [_plain(asdict(s)) for s in statuses]}


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = Parser(description="服務水準指標的狀態與燒損告警(只讀)")
    parser.add_argument("--executor-db", required=True, type=Path)
    parser.add_argument("--analyzer-db", required=True, type=Path)
    parser.add_argument("--dsp-url", required=True)
    parser.add_argument("--dsp-timeout-seconds", type=float, default=2.0)
    parser.add_argument("--now", required=True, type=aware_time)
    return parser.parse_args(argv)


def run(argv: list[str] | None = None, *, out: TextIO | None = None,
        err: TextIO | None = None, environ: Mapping[str, str] | None = None) -> int:
    """命令列入口。DSP 列操作端點的唯讀稽核金鑰從環境讀(只有入口讀環境;測試傳 environ);沒有就
    讀不到 DSP 的窗,兩條目標為零的指標照實標資料來源缺。"""
    args = _parse(argv)
    errors = err or sys.stderr
    try:
        audit_key = read_audit_key(os.environ if environ is None else environ)
    except AuditKeyTooLong as bad:
        print(f"設定錯誤:{bad}", file=errors)
        return EXIT_BAD_CONFIG
    sources = sli.Sources(args.executor_db, args.analyzer_db, args.dsp_url,
                          args.dsp_timeout_seconds, audit_key)
    try:
        statuses = evaluate(args.now, counter=lambda name, since, until: sli.count(
            name, since, until, sources))
    except FileNotFoundError as missing:
        print(f"找不到資料庫檔:{missing}", file=errors)
        return EXIT_NO_DATABASE
    except DatabaseNotUpgraded as old:
        print(f"{old}。請先啟動一次執行迴圈(分析端資料庫則先跑一次分析行程);評估器只讀,不替它補",
              file=errors)
        return EXIT_NOT_UPGRADED
    print(json.dumps(to_primitives(statuses), ensure_ascii=False, indent=2),
          file=out or sys.stdout)
    return exit_code(statuses, errors)


def exit_code(statuses: tuple[SloStatus, ...], errors: TextIO) -> int:
    """印完狀態之後的結束代碼(假說命令列也用這一份,結束代碼跟這裡一致):任何一條缺資料或出錯回
    「不完整」並把哪幾條、為什麼印到 errors;都齊但有不穩定回「不穩定」;否則 0。"""
    incomplete = [s for s in statuses if s.missing or s.error is not None]
    if incomplete:
        for s in incomplete:
            print(f"{s.name}:{s.error or '資料來源讀不到(沒有記下原因)'},這一條沒算完整",
                  file=errors)
        return EXIT_INCOMPLETE  # 缺資料優先於不穩定:缺了的那一條本來就不能拿來判告警
    if not all(s.stable for s in statuses):
        print("讀取期間有新提交:有指標跨資料庫讀了三輪都不同,印出的是最後一輪", file=errors)
        return EXIT_UNSTABLE
    return EXIT_OK


def main(argv: list[str] | None = None) -> None:
    raise SystemExit(run(argv))


if __name__ == "__main__":
    main()
