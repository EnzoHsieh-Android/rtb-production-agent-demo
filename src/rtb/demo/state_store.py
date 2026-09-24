"""展示狀態暫存資料庫(Phase 12 設計審 r1 a2):驅動執行緒寫,展示伺服器用唯讀開法讀。

用共用的 SQLite 工具開,不自創檔案協定;一次展示一個展示編號,同一個檔可以放好幾次展示。
記的是驅動程式觀察到的事:每個情境的起訖與結果、每一筆判斷(帶來源)、目前節點,以及 F7 這種
多工作者並行情境的各節點筆數。頁面要的展示狀態由伺服器從這裡組(增量 2b)。
"""

import json
import sqlite3  # noqa: TID251 - 展示狀態庫是展示自己的資料庫(不是讀別的系統)
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from rtb.sqlitekit import (  # noqa: TID251 - 同上:展示狀態庫
    begin_snapshot,
    connect,
    connect_read_only,
    end_snapshot,
    immediate_transaction,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS scenario_runs (
    demo_id TEXT NOT NULL, code TEXT NOT NULL, status TEXT NOT NULL, reason TEXT,
    summary TEXT, started_at TEXT NOT NULL, finished_at TEXT, PRIMARY KEY (demo_id, code));
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, demo_id TEXT NOT NULL, code TEXT NOT NULL,
    node TEXT NOT NULL, edge_json TEXT, outcome TEXT NOT NULL, reason TEXT NOT NULL,
    at TEXT NOT NULL, origin TEXT NOT NULL, basis_json TEXT NOT NULL DEFAULT '[]',
    operation_key TEXT, actor TEXT NOT NULL DEFAULT '程式', task TEXT);
CREATE INDEX IF NOT EXISTS decisions_by_scenario ON decisions (demo_id, code, id);
CREATE TABLE IF NOT EXISTS current_node (
    demo_id TEXT PRIMARY KEY, code TEXT NOT NULL, node TEXT NOT NULL, entered_at TEXT NOT NULL,
    decision_id INTEGER);
CREATE TABLE IF NOT EXISTS verifier_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, demo_id TEXT NOT NULL, verified_at TEXT NOT NULL,
    passed INTEGER NOT NULL, lines_json TEXT NOT NULL, reasons_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS confirmations (
    demo_id TEXT PRIMARY KEY, code TEXT NOT NULL, request_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS confirmation_windows (
    demo_id TEXT PRIMARY KEY, until TEXT NOT NULL, approved_at TEXT, closed_at TEXT);
CREATE TABLE IF NOT EXISTS scenario_details (
    demo_id TEXT NOT NULL, code TEXT NOT NULL, details_json TEXT NOT NULL,
    PRIMARY KEY (demo_id, code));
CREATE TABLE IF NOT EXISTS node_counts (
    demo_id TEXT NOT NULL, code TEXT NOT NULL, node TEXT NOT NULL, count INTEGER NOT NULL,
    PRIMARY KEY (demo_id, code, node));
"""


@dataclass(frozen=True)
class ScenarioRun:
    code: str
    status: str  # running / done / incomplete / awaiting_confirmation
    reason: str | None
    summary: str | None
    started_at: datetime
    finished_at: datetime | None


class BasisCode(StrEnum):
    """根據的結論代碼(代碼審 r1 a6):補判斷點的路徑依它走,文字只拿來顯示,改措辭不影響路徑。"""

    FRESH = "fresh"
    STALE = "stale"
    COMPLETE = "complete"
    MISSING = "missing"
    UNDERPACING = "underpacing"
    NOT_UNDERPACING = "not_underpacing"
    PACING_UNKNOWN = "pacing_unknown"
    ROUTE_RULE = "route_rule"
    WORTH = "worth"
    NOT_WORTH = "not_worth"
    INSUFFICIENT = "insufficient"
    PROPOSE = "propose"
    RATIO_OK = "ratio_ok"
    RATIO_OVER = "ratio_over"
    CAP_OK = "cap_ok"
    CAP_OVER = "cap_over"
    TOTAL_OK = "total_ok"
    TOTAL_OVER = "total_over"


@dataclass(frozen=True)
class Basis:
    """一次判斷所用的一組根據(增量 2b):量到的值、當時生效的標準、比較後的結論,以及這組根據從哪來
    (做判斷的程式當下記下的,或依存下的證據用正式規則重算的)。拿不到就不給這一組,不造數字。
    code 是結論代碼(補判斷點用;只顯示、不補路的那幾種是空的)。"""

    observed: str
    standard: str
    conclusion: str
    source: str
    code: BasisCode | None = None


@dataclass(frozen=True)
class DecisionRow:
    """一筆判斷:走到哪個節點、走的那條邊(圖上沒有這條邊就是空)、判成什麼、白話原因、時間、來源;
    增量 2b 加上根據(可多組)、關聯的外部寫入操作鍵、誰判的(程式、人工、外部平台;之後「AI 參與
    決策」階段加 AI,並另加一欄放 AI 看到什麼、答了什麼、程式怎麼接手)。"""

    node: str
    edge: tuple[str, str] | None
    outcome: str
    reason: str
    at: datetime
    origin: str  # 哪一顆資料庫的哪個事件編號或哪一列
    basis: tuple[Basis, ...] = ()
    operation_key: str | None = None
    actor: str = "程式"
    task: str | None = None  # 哪一件工作(同一個情境有好幾件工作時分得開;代碼審 r1 d9)


@dataclass(frozen=True)
class ChangeRecord:
    """一個廣告最後改了什麼。金額是平台預算的原樣整數(平台預算單位,沒有幣別,不換算)。"""

    campaign: str
    before: int | None
    after: int | None
    written: bool
    reason: str | None = None


@dataclass(frozen=True)
class ScenarioDetails:
    """情境層的細節(增量 2b),拿不到的一律空值:為什麼開始、目標與限制、排隊等了幾秒、展示刻意
    製造的故障(流程圖節點, 說明)、追蹤的那一筆操作鍵與平台套用次數、最後改了什麼;很多廣告的
    情境(F7)另給一行彙總。"""

    trigger: str | None = None
    goal: str | None = None
    queue_wait_seconds: int | None = None
    injected_faults: tuple[tuple[str, str], ...] = ()
    operation_key: str | None = None
    platform_apply_count: int | None = None
    change: ChangeRecord | None = None
    change_overview: str | None = None
    # 平台最後的樣子:(廣告, 預算, 版本, 狀態);平台上的操作紀錄(一行一筆,白話)
    platform: tuple[tuple[str, int, int, str], ...] = ()
    platform_operations: tuple[str, ...] = ()
    audit: tuple[str, ...] = ()  # 人工操作的稽核(死信重新送入等)
    dispositions: tuple[tuple[str, str, str], ...] = ()  # 收件口的擋下與停下:(類別, 代碼, 說明)


def _details_json(details: ScenarioDetails) -> str:
    body = dict(details.__dict__)
    body["injected_faults"] = [list(pair) for pair in details.injected_faults]
    body["platform"] = [list(row) for row in details.platform]
    body["dispositions"] = [list(row) for row in details.dispositions]
    body["change"] = None if details.change is None else dict(details.change.__dict__)
    return json.dumps(body, ensure_ascii=False)


def _details_from(text: str) -> ScenarioDetails:
    body = json.loads(text)
    body["injected_faults"] = tuple((str(a), str(b)) for a, b in body["injected_faults"])
    body["change"] = None if body["change"] is None else ChangeRecord(**body["change"])
    body["platform"] = tuple((str(c), int(b), int(v), str(s)) for c, b, v, s in body["platform"])
    for name in ("platform_operations", "audit"):
        body[name] = tuple(str(line) for line in body[name])
    body["dispositions"] = tuple((str(a), str(b), str(c)) for a, b, c in body["dispositions"])
    return ScenarioDetails(**body)


@dataclass(frozen=True)
class VerifierRun:
    """全部跑一次的最後一步跑的驗證器:原樣每一行、通過或擋下、擋下原因、時間、哪一次展示。
    單一情境重跑不跑驗證器,頁面顯示最近一次這一筆並標明取自哪一次(設計審 r1 x7)。"""

    demo_id: str
    verified_at: datetime
    passed: bool
    lines: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ConfirmationRequest:
    """等人確認的那一筆:簽一張確認要的全部來源(設計審 r2 n5)與給人逐項勾的數字快照。
    伺服器從這裡讀,不經表單;金鑰不在這裡(只在伺服器記憶體)。"""

    task_id: str
    revision: int
    proposal_hash: str
    tenant_config: str
    stage: str
    max_increase: int
    decision_expires_at: datetime
    numbers: tuple[tuple[str, str], ...]


def _request_json(request: ConfirmationRequest) -> str:
    return json.dumps({**request.__dict__,
                       "decision_expires_at": _iso(request.decision_expires_at),
                       "numbers": [list(pair) for pair in request.numbers]})


def _request_from(text: str) -> ConfirmationRequest:
    body = json.loads(text)
    body["decision_expires_at"] = datetime.fromisoformat(body["decision_expires_at"])
    body["numbers"] = tuple((str(a), str(b)) for a, b in body["numbers"])
    return ConfirmationRequest(**body)


class ConfirmationClosed(Exception):
    """這次展示已經不能再確認:沒有等人確認的那一筆、已經確認過、或確認期限已過(原因代碼在 code)。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


NO_CONFIRMATION = "no_confirmation"
ALREADY_CONFIRMED = "already_confirmed"
CONFIRMATION_TIMED_OUT = "confirmation_timed_out"


@dataclass(frozen=True)
class CurrentNode:
    scenario: str
    node: str
    entered_at: datetime
    last_decision: DecisionRow | None


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _time(text: str | None) -> datetime | None:
    return None if text is None else datetime.fromisoformat(text)


class StateWriter:
    """每一次寫入開一條短連線:驅動程式跑在展示伺服器的執行緒裡,情境本體又在自己的執行緒裡,
    SQLite 連線不能跨執行緒共用;一次展示只寫幾百列,短連線的成本可以忽略。"""

    def __init__(self, path: Path, demo_id: str) -> None:
        self.path, self.demo_id = Path(path), demo_id
        conn = connect(self.path, schema=SCHEMA)  # 先建表
        try:
            with immediate_transaction(conn):  # 之前版本建的展示狀態庫:補判斷紀錄的工作欄
                columns = {row[1] for row in conn.execute("PRAGMA table_info(decisions)")}
                if "task" not in columns:
                    conn.execute("ALTER TABLE decisions ADD COLUMN task TEXT")
        finally:
            conn.close()

    def close(self) -> None:
        """沒有常駐連線;保留這個方法讓呼叫端照一般資源的寫法收尾。"""

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        conn = connect(self.path)
        try:
            with immediate_transaction(conn):
                yield conn
        finally:
            conn.close()

    def start_scenario(self, code: str, at: datetime) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO scenario_runs "
                "VALUES (?, ?, 'running', NULL, NULL, ?, NULL)",
                (self.demo_id, code, _iso(at)))

    def finish_scenario(self, code: str, status: str, reason: str | None, summary: str | None,
                        at: datetime) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE scenario_runs SET status = ?, reason = ?, summary = ?, finished_at = ? "
                "WHERE demo_id = ? AND code = ?",
                (status, reason, summary, _iso(at), self.demo_id, code))

    def abandon(self, codes: tuple[str, ...], reason: str, at: datetime) -> None:
        """驅動程式自己出事(代碼審 r1 v4):這次還沒結案的情境一律標成沒跑完、寫明原因;還沒開始的
        也記一列,頁面看得到這次展示停在哪裡,不默默退回上一次。"""
        with self._write() as conn:
            for code in codes:
                conn.execute(
                    "INSERT OR IGNORE INTO scenario_runs "
                    "VALUES (?, ?, 'running', NULL, NULL, ?, NULL)",
                    (self.demo_id, code, _iso(at)))
                conn.execute(
                    "UPDATE scenario_runs SET status = 'incomplete', reason = ?, finished_at = ? "
                    "WHERE demo_id = ? AND code = ? AND status IN ('running', "
                    "'awaiting_confirmation')", (reason, _iso(at), self.demo_id, code))

    def mark_status(self, code: str, status: str, reason: str | None = None) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE scenario_runs SET status = ?, reason = ? WHERE demo_id = ? AND code = ?",
                (status, reason, self.demo_id, code))

    def record_decision(self, code: str, row: DecisionRow) -> None:
        edge = None if row.edge is None else json.dumps(list(row.edge))
        with self._write() as conn:
            cursor = conn.execute(
                "INSERT INTO decisions (demo_id, code, node, edge_json, outcome, reason, at, "
                "origin, basis_json, operation_key, actor, task) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self.demo_id, code, row.node, edge, row.outcome, row.reason, _iso(row.at),
                 row.origin, json.dumps([list(b.__dict__.values()) for b in row.basis],
                                        ensure_ascii=False), row.operation_key, row.actor,
                 row.task))
            conn.execute("INSERT OR REPLACE INTO current_node VALUES (?, ?, ?, ?, ?)",
                         (self.demo_id, code, row.node, _iso(row.at), cursor.lastrowid))

    def set_scenario_details(self, code: str, details: ScenarioDetails) -> None:
        with self._write() as conn:
            conn.execute("INSERT OR REPLACE INTO scenario_details VALUES (?, ?, ?)",
                         (self.demo_id, code, _details_json(details)))

    def record_verifier_run(self, run: VerifierRun) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT INTO verifier_runs (demo_id, verified_at, passed, lines_json, "
                "reasons_json) VALUES (?, ?, ?, ?, ?)",
                (run.demo_id, _iso(run.verified_at), int(run.passed),
                 json.dumps(list(run.lines), ensure_ascii=False),
                 json.dumps(list(run.reasons), ensure_ascii=False)))

    def set_confirmation(self, code: str, request: ConfirmationRequest,
                         until: datetime | None = None) -> None:
        """開一個確認窗:until 是驅動程式等人確認的期限(沒給就用那筆的決策到期)。"""
        with self._write() as conn:
            conn.execute("INSERT OR REPLACE INTO confirmations VALUES (?, ?, ?)",
                         (self.demo_id, code, _request_json(request)))
            conn.execute("INSERT OR REPLACE INTO confirmation_windows VALUES (?, ?, NULL, NULL)",
                         (self.demo_id, _iso(until or request.decision_expires_at)))

    def answer_confirmation(self, now: datetime,
                            sign: Callable[[str, ConfirmationRequest], None]) -> None:
        """[S1056] 簽發跟驅動程式關確認窗互斥(代碼審 r1 x3/s5/v5):同一個寫入交易裡再確認一次「那一筆
        還在等、還沒確認過、確認期限沒過」,才呼叫 sign;sign 成功就把這個窗標成已確認,同一張表單再
        送一次一律拒。sign 丟例外時整個交易退回,窗照樣開著。"""
        with self._write() as conn:
            window = conn.execute(
                "SELECT until, approved_at, closed_at FROM confirmation_windows WHERE demo_id = ?",
                (self.demo_id,)).fetchone()
            pending = conn.execute(
                "SELECT code, request_json FROM confirmations WHERE demo_id = ?",
                (self.demo_id,)).fetchone()
            if window is None:
                raise ConfirmationClosed(NO_CONFIRMATION)
            until, approved, closed = window
            if approved is not None:
                raise ConfirmationClosed(ALREADY_CONFIRMED)
            if closed is not None or pending is None or now >= datetime.fromisoformat(until):
                raise ConfirmationClosed(CONFIRMATION_TIMED_OUT)
            sign(str(pending[0]), _request_from(str(pending[1])))
            conn.execute("DELETE FROM confirmations WHERE demo_id = ?", (self.demo_id,))
            conn.execute("UPDATE confirmation_windows SET approved_at = ? WHERE demo_id = ?",
                         (_iso(now), self.demo_id))

    def clear_confirmation(self, now: datetime | None = None) -> bool:
        """確認逾時或已確認:關掉確認窗、清掉確認表單(設計審 r3 m4),確認頁與送出確認之後一律拒。
        回傳關窗之前有沒有人確認過(跟簽發互斥:兩者在同一顆資料庫的寫入交易裡排隊)。"""
        with self._write() as conn:
            conn.execute("DELETE FROM confirmations WHERE demo_id = ?", (self.demo_id,))
            row = conn.execute("SELECT approved_at FROM confirmation_windows WHERE demo_id = ?",
                               (self.demo_id,)).fetchone()
            conn.execute("UPDATE confirmation_windows SET closed_at = ? "
                         "WHERE demo_id = ? AND closed_at IS NULL",
                         (_iso(now or datetime.now().astimezone()), self.demo_id))
        return row is not None and row[0] is not None

    def set_node_counts(self, code: str, counts: dict[str, int]) -> None:
        with self._write() as conn:
            conn.execute("DELETE FROM node_counts WHERE demo_id = ? AND code = ?",
                         (self.demo_id, code))
            conn.executemany("INSERT INTO node_counts VALUES (?, ?, ?, ?)",
                             [(self.demo_id, code, node, n) for node, n in counts.items()])


_DECISION_COLUMNS = ("node, edge_json, outcome, reason, at, origin, basis_json, operation_key, "
                     "actor, task")


def _decision(record: tuple[str, ...]) -> DecisionRow:
    node, edge, outcome, reason, at, origin, basis, key, actor, task = record
    pair = None if edge is None else tuple(json.loads(edge))
    return DecisionRow(node, (pair[0], pair[1]) if pair else None, outcome, reason,
                       datetime.fromisoformat(at), origin,
                       tuple(_basis(item) for item in json.loads(basis)), key, actor, task)


def _basis(item: list[str | None]) -> Basis:
    observed, standard, conclusion, source, *rest = item
    code = rest[0] if rest else None
    return Basis(str(observed), str(standard), str(conclusion), str(source),
                 None if code is None else BasisCode(code))


def _verifier_run(row: tuple[str, ...] | None) -> VerifierRun | None:
    if row is None:
        return None
    demo_id, at, passed, lines, reasons = row
    return VerifierRun(str(demo_id), datetime.fromisoformat(str(at)), bool(passed),
                       tuple(json.loads(str(lines))), tuple(json.loads(str(reasons))))


class StateReader:
    """唯讀開法:一開就進同一個快照,直到 close(伺服器每次產生頁面開一次)。"""

    def __init__(self, path: Path) -> None:
        self._conn = connect_read_only(path)
        try:
            begin_snapshot(self._conn)
        except BaseException:
            self._conn.close()
            raise

    def close(self) -> None:
        try:
            end_snapshot(self._conn)
        finally:
            self._conn.close()

    def scenario_runs(self, demo_id: str) -> tuple[ScenarioRun, ...]:
        rows = self._conn.execute(
            "SELECT code, status, reason, summary, started_at, finished_at FROM scenario_runs "
            "WHERE demo_id = ? ORDER BY code", (demo_id,)).fetchall()
        return tuple(ScenarioRun(code, status, reason, summary, datetime.fromisoformat(started),
                                 _time(finished))
                     for code, status, reason, summary, started, finished in rows)

    def decisions(self, demo_id: str, code: str) -> tuple[DecisionRow, ...]:
        rows = self._conn.execute(
            f"SELECT {_DECISION_COLUMNS} FROM decisions "  # noqa: S608 - 固定欄位清單
            "WHERE demo_id = ? AND code = ? ORDER BY id", (demo_id, code)).fetchall()
        return tuple(_decision(row) for row in rows)

    def current(self, demo_id: str) -> CurrentNode | None:
        row = self._conn.execute(
            "SELECT code, node, entered_at, decision_id FROM current_node WHERE demo_id = ?",
            (demo_id,)).fetchone()
        if row is None:
            return None
        code, node, entered, decision_id = row
        last = self._conn.execute(
            f"SELECT {_DECISION_COLUMNS} FROM decisions WHERE id = ?",  # noqa: S608 - 固定欄位清單
            (decision_id,)).fetchone()
        return CurrentNode(code, node, datetime.fromisoformat(entered),
                           None if last is None else _decision(last))

    def scenario_details(self, demo_id: str, code: str) -> ScenarioDetails | None:
        row = self._conn.execute(
            "SELECT details_json FROM scenario_details WHERE demo_id = ? AND code = ?",
            (demo_id, code)).fetchone()
        return None if row is None else _details_from(str(row[0]))

    def latest_verifier_run(self) -> VerifierRun | None:
        """最近一次全部跑一次的驗證器結果(不限展示編號)。"""
        row = self._conn.execute(
            "SELECT demo_id, verified_at, passed, lines_json, reasons_json FROM verifier_runs "
            "ORDER BY id DESC LIMIT 1").fetchone()
        return _verifier_run(row)

    def verifier_run(self, demo_id: str) -> VerifierRun | None:
        """這一次展示自己的驗證器結果;還沒跑到就是空的(代碼審 r1 x2:在跑的完整展示不顯示
        上一次的)。"""
        row = self._conn.execute(
            "SELECT demo_id, verified_at, passed, lines_json, reasons_json FROM verifier_runs "
            "WHERE demo_id = ? ORDER BY id DESC LIMIT 1", (demo_id,)).fetchone()
        return _verifier_run(row)

    def confirmation_refusal(self, demo_id: str) -> str:
        """現在沒有可以確認的那一筆時,說明是哪一種:從沒開過、已經確認過、確認期限已過。"""
        row = self._conn.execute(
            "SELECT approved_at, closed_at FROM confirmation_windows WHERE demo_id = ?",
            (demo_id,)).fetchone()
        if row is None:
            return NO_CONFIRMATION
        return ALREADY_CONFIRMED if row[0] is not None else CONFIRMATION_TIMED_OUT

    def confirmation(self, demo_id: str) -> tuple[str, ConfirmationRequest] | None:
        row = self._conn.execute("SELECT code, request_json FROM confirmations WHERE demo_id = ?",
                                 (demo_id,)).fetchone()
        return None if row is None else (str(row[0]), _request_from(str(row[1])))

    def node_counts(self, demo_id: str, code: str) -> tuple[tuple[str, int], ...]:
        return tuple((node, int(n)) for node, n in self._conn.execute(
            "SELECT node, count FROM node_counts WHERE demo_id = ? AND code = ? ORDER BY node",
            (demo_id, code)))
