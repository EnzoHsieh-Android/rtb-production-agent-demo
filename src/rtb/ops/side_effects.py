"""副作用核對(Phase 9 增量 3):未授權或違反護欄的副作用、重複有害副作用。只讀。

- 核對函式逐項重看 DSP 的一筆寫入,判法只用寫入當時記下、之後不變的資料:嘗試第一列的提案快照、
  租戶、預留金額與四樣核對材料(比例允許加的量、單一廣告上限、總額上限、已用額度),DSP 記下的廣告
  建檔租戶與政策版本,以及核可使用紀錄。任何一項證實違規就判壞;沒有任何一項違規、但缺了需要的
  材料判無法核對(不算有效事件、另報份數)。
- DSP 的窗用兩支唯讀端點讀:先取窗起點的游標,再依操作編號往下翻頁,讀到提交時間大於等於窗終點就停
  (DSP 保證提交時間不小於上一筆,依編號翻頁等於依時間翻頁)。窗含起點、不含終點。
- 讀取順序:先讀 DSP 的窗,再開執行端的唯讀快照。執行端在呼叫 DSP 之前就提交了第一列,所以 DSP
  看得到的每一筆執行端寫入,之後開的快照一定看得到它的第一列;反過來會把正常寫入誤判成沒經過開始
  一筆。執行端一律用擁有模組的批量讀取函式,不逐筆查。
- 穩定與否:不像端到端交給執行那樣讀到兩輪相同為止,計數恆標穩定。理由是上面的因果順序:要核對
  的只有「DSP 窗內的寫入」配「它們的第一列與核可使用」,第一列與核可使用都在呼叫 DSP 之前提交、之後
  不改,所以讀 DSP 之後才開的快照對這些寫入一定讀得全,重讀一輪答案一樣;讀取期間新提交的寫入不在
  這次讀到的 DSP 窗裡,不影響這次的答案(代碼審第 1 輪架構席)。
- DSP 回應是不可信輸入:讀不到、狀態碼不對、欄位或提交時間讀不懂,一律轉成 DspUnreadable,這一條
  回資料來源缺,不讓例外穿出去拖垮其他指標。
- 列操作兩支端點要帶唯讀稽核金鑰(專用標頭,金鑰位元組的 base64url);沒帶或帶錯 DSP 拒讀,照實回
  資料來源缺。
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import quote

from rtb.capabilitykit import encode_audit_key
from rtb.domain.proposal import ActionType, content_hash
from rtb.executor import attempt_store
from rtb.executor.attempt_store import FirstRow
from rtb.executor.inbox_store import BlockCode, ReadOnlyInbox
from rtb.httpclient import ClientHeader, request_json

EXECUTOR_KEY = re.compile(r"k1-[0-9a-f]{64}")  # 執行端的冪等鍵格式;別的寫入者直接改 DSP 不算
MAX_PAGES = 20_000  # 翻頁上限:防 DSP 回應有誤時無限翻下去(一頁 50 筆,約 100 萬筆操作)
RATIO = BlockCode.BUDGET_INCREASE_TOO_LARGE.value
AGGREGATE = BlockCode.AGGREGATE_LIMIT_REACHED.value


class DspUnreadable(Exception):
    """DSP 的唯讀端點讀不到或讀不懂:這個窗的副作用核對做不了(呼叫端照實標資料來源缺)。"""


class Verdict(StrEnum):
    GOOD = "good"
    BAD = "bad"
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True)
class DspWrite:
    """DSP 一筆提交的寫入(列操作端點回的欄位)。"""

    operation_id: int
    key: str
    campaign_id: str
    tenant: str | None  # 廣告建檔時的租戶(之後不變)
    action: str
    new_budget: int | None
    expected_version: int | None
    committed_at: str
    policy_version: str | None


@dataclass(frozen=True)
class Tally:
    """一個窗的計數:好事件、有效事件、每個壞事件的時間,另報無法核對、接不上、時鐘異常的份數。
    missing 為真:資料來源讀不到,這個窗沒算(不是 0 個事件)。stable 為假:跨資料庫讀了三輪都
    不同,回的是最後一輪(比照追蹤檢視與指標的穩定欄;只有端到端交給執行會是假)。"""

    good: int
    valid: int
    bad_at: tuple[str, ...] = ()
    unverifiable: int = 0
    unlinked: int = 0
    clock_anomaly: int = 0
    missing: bool = False
    stable: bool = True

    @property
    def bad(self) -> int:
        return self.valid - self.good


def iso(moment: str | datetime) -> str:
    """固定格式的 UTC 字串(同執行端的寫法);DSP 寫的 +00:00 一律換算。"""
    value = moment if isinstance(moment, datetime) else datetime.fromisoformat(moment)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def is_executor_key(key: str) -> bool:
    return EXECUTOR_KEY.fullmatch(key) is not None


# ---- 核對函式 ----
def _scope_violations(entry: DspWrite, first: FirstRow) -> tuple[list[str], list[str]]:
    """授權範圍:廣告、動作、新預算、預期版本跟第一列的提案比;DSP 廣告建檔租戶跟第一列簽發租戶比。
    回(違規, 缺的材料)。"""
    proposal = first.proposal
    bad: list[str] = []
    missing: list[str] = []
    if proposal is None or not first.snapshot_matches_key:
        return bad, ["snapshot"]  # 快照讀不回來或對不上鍵:不能拿來當授權範圍
    wanted_budget = proposal.requested_change.get("new_budget")
    if entry.campaign_id != proposal.campaign_id:
        bad.append("campaign")
    if entry.action != proposal.action_type.value:
        bad.append("action")
    if proposal.action_type is ActionType.UPDATE_BUDGET and entry.new_budget != wanted_budget:
        bad.append("new_budget")
    # 可能沒記的三項:任一邊為空是缺材料,兩邊都有才比。預期版本與政策版本是 DSP 同一次補欄位加的,
    # 舊操作兩者都空,一樣判缺材料(代碼審第 1 輪:預期版本原本空值也拿去比,判成證實違規)
    for name, recorded, wanted in (
            ("expected_version", entry.expected_version, proposal.campaign_version_observed),
            ("tenant", entry.tenant, first.tenant),
            ("policy_version", entry.policy_version, proposal.policy_version)):
        if recorded is None or wanted is None:
            missing.append(name)
        elif recorded != wanted:
            bad.append(name)
    return bad, missing


def _limit_violations(
    entry: DspWrite, first: FirstRow, stages: frozenset[str],
) -> tuple[list[str], list[str]]:
    """數值護欄(暫停跳過):單一廣告上限不論加減都核對;加的量大於 0 才核對比例與總曝險。剛好等於
    上限、允許量或門檻算合法。"""
    bad: list[str] = []
    missing: list[str] = []
    if entry.action != ActionType.UPDATE_BUDGET.value:
        return bad, missing
    if first.max_budget is None or entry.new_budget is None:
        missing.append("max_budget")
    elif entry.new_budget > first.max_budget:
        bad.append("max_budget")
    amount = first.reserved_amount
    if amount is None:
        return bad, [*missing, "reserved_amount"]
    if amount <= 0:
        return bad, missing
    if first.ratio_allowance is None:
        missing.append("ratio_allowance")
    elif amount > first.ratio_allowance and RATIO not in stages:
        bad.append("ratio")
    if first.used_before is None or first.aggregate_limit is None:
        missing.append("aggregate")
    elif first.used_before + amount > first.aggregate_limit and AGGREGATE not in stages:
        bad.append("aggregate")
    return bad, missing


def check_write(
    entry: DspWrite, first: FirstRow | None, stages: frozenset[str],
) -> tuple[Verdict, tuple[str, ...]]:
    """一筆 DSP 寫入的核對結果與理由。first 為空:執行端找不到這把鍵的第一列(沒經過開始一筆)。
    stages 是這把鍵用過核可的關卡。任何一項證實違規就判壞,不因為別項缺材料而放過。"""
    if first is None:
        return Verdict.BAD, ("not_begun",)
    scope_bad, scope_missing = _scope_violations(entry, first)
    limit_bad, limit_missing = _limit_violations(entry, first, stages)
    if scope_bad or limit_bad:
        return Verdict.BAD, tuple(scope_bad + limit_bad)
    if scope_missing or limit_missing:
        return Verdict.UNVERIFIABLE, tuple(scope_missing + limit_missing)
    return Verdict.GOOD, ()


def tally_unauthorized(
    writes: Iterable[DspWrite], firsts: Mapping[str, FirstRow],
    approvals: Mapping[str, frozenset[str]],
) -> Tally:
    """有效事件:執行端鍵格式的寫入扣掉無法核對的;壞事件記在那一筆自己的提交時間。"""
    good = valid = unverifiable = 0
    bad_at = []
    for entry in writes:
        if not is_executor_key(entry.key):
            continue
        found, _ = check_write(entry, firsts.get(entry.key), approvals.get(entry.key, frozenset()))
        if found is Verdict.UNVERIFIABLE:
            unverifiable += 1
            continue
        valid += 1
        if found is Verdict.GOOD:
            good += 1
        else:
            bad_at.append(iso(entry.committed_at))
    return Tally(good, valid, tuple(bad_at), unverifiable)


# ---- 讀 DSP ----
def get_json(url: str, path: str, timeout: float,
             audit_key: bytes | None = None) -> tuple[int, Any]:
    """打 DSP 的唯讀端點(共用 HTTP 用戶端,只讀);連不上或讀不懂丟 DspUnreadable。列操作兩支端點
    要帶唯讀稽核金鑰(專用標頭,金鑰位元組的 base64url);既有依鍵查的端點不用。"""
    headers = None if audit_key is None else {ClientHeader.AUDIT_KEY: encode_audit_key(audit_key)}
    try:
        return request_json(f"{url.rstrip('/')}{path}", "GET", None, timeout, headers)
    except (OSError, ValueError) as exc:
        raise DspUnreadable(str(exc)) from exc


def commit_moment(text: object) -> datetime:
    """DSP 回的提交時間:讀不懂或沒帶時區丟 DspUnreadable(沒帶時區拿去跟窗界比會直接丟例外)。"""
    try:
        moment = datetime.fromisoformat(str(text)) if isinstance(text, str) else None
    except ValueError as exc:
        raise DspUnreadable(f"提交時間讀不懂:{text!r}") from exc
    if moment is None or moment.tzinfo is None:
        raise DspUnreadable(f"提交時間讀不懂:{text!r}")
    return moment


def _write(entry: Any) -> DspWrite:
    if not isinstance(entry, dict):
        raise DspUnreadable("操作不是物件")
    try:
        written = DspWrite(int(entry["operation_id"]), str(entry["idempotency_key"]),
                           str(entry["campaign_id"]), entry.get("tenant"), str(entry["action"]),
                           entry.get("new_budget"), entry.get("expected_version"),
                           str(entry["committed_at"]), entry.get("policy_version"))
    except (KeyError, TypeError, ValueError) as exc:
        raise DspUnreadable(f"操作欄位讀不懂:{exc!r}") from exc
    commit_moment(entry["committed_at"])  # 在這裡就驗,後面的比較與換算不會再丟別的例外
    return written


def read_dsp_window(
    url: str, since: datetime, until: datetime, timeout: float, audit_key: bytes | None,
) -> tuple[DspWrite, ...]:
    """窗 [since, until) 內 DSP 提交的寫入:先取起點游標,再依編號翻頁,讀到提交時間大於等於終點
    就停。"""
    status, body = get_json(url, f"/operations/since/{quote(since.isoformat(), safe='')}",
                            timeout, audit_key)
    if status != 200 or not isinstance(body, dict) or not isinstance(body.get("cursor"), int):
        raise DspUnreadable(f"游標端點回 {status}")
    cursor: int = body["cursor"]
    found: list[DspWrite] = []
    for _ in range(MAX_PAGES):
        status, page = get_json(url, f"/operations/after/{cursor}", timeout, audit_key)
        if status != 200 or not isinstance(page, dict) or not isinstance(
                page.get("operations"), list):
            raise DspUnreadable(f"列操作端點回 {status}")
        for entry in map(_write, page["operations"]):  # 提交時間在 _write 已驗過帶時區
            if datetime.fromisoformat(entry.committed_at) >= until:
                return tuple(found)
            if datetime.fromisoformat(entry.committed_at) >= since:
                found.append(entry)
        following = page.get("next")
        if not isinstance(following, int):
            return tuple(found)
        if following <= cursor:  # 游標沒往前:DSP 回應有誤,不能一直翻
            raise DspUnreadable("下一頁的游標沒有往前")
        cursor = following
    raise DspUnreadable("翻頁超過上限")


def _operation(url: str, key: str, timeout: float) -> tuple[str, int] | None:
    """依冪等鍵讀 DSP 的一筆操作(既有的唯讀端點):回(提交時間, 操作編號),查不到回空。"""
    status, body = get_json(url, f"/operations/{quote(key, safe='')}", timeout)
    if status == 404:
        return None
    if status != 200 or not isinstance(body, dict):
        raise DspUnreadable(f"操作端點回 {status}")
    try:
        return iso(commit_moment(body.get("committed_at"))), int(body["operation_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DspUnreadable(f"操作欄位讀不懂:{exc!r}") from exc


# ---- 兩條服務水準指標 ----
def unauthorized(since: datetime, until: datetime, executor_db: Path, dsp_url: str,
                 timeout: float, audit_key: bytes | None) -> Tally:
    try:
        writes = read_dsp_window(dsp_url, since, until, timeout, audit_key)  # 先讀 DSP
    except DspUnreadable:
        return Tally(0, 0, missing=True)
    keys = [w.key for w in writes if is_executor_key(w.key)]
    inbox = ReadOnlyInbox(executor_db)  # 再開執行端快照
    try:
        with inbox.read_transaction() as tx:
            firsts = attempt_store.first_rows_for(tx, keys)
            approvals = inbox.approval_uses_for(tx, keys)
    finally:
        inbox.close()
    return tally_unauthorized(writes, firsts, approvals)


def _identity(first: FirstRow) -> tuple[str, int, str] | None:
    if first.proposal is None:
        return None
    return first.proposal.task_id, first.proposal.revision, content_hash(first.proposal)


def duplicates(since: datetime, until: datetime, executor_db: Path, dsp_url: str,
               timeout: float, audit_key: bytes | None) -> Tally:
    """同一份提案內容(任務、修訂、內容雜湊)在 DSP 的第二筆以及之後每一筆提交各算一個壞事件,依那
    一筆的提交時間歸窗;第一筆在窗外也算(用第一列往回查同一份提案的其他鍵,不限窗)。找不到第一列的
    算無法核對。同一把鍵在窗內出現兩筆以上操作(DSP 有唯一限制,防禦性核對)也算壞。讀窗與第二段
    逐鍵查 DSP 任何一次讀不到,整條回資料來源缺(代碼審第 1 輪:第二段原本沒接)。"""
    try:
        writes = [w for w in read_dsp_window(dsp_url, since, until, timeout, audit_key)
                  if is_executor_key(w.key)]
        firsts, siblings = _executor_side(executor_db, writes)
        return _tally_duplicates(writes, firsts, siblings, dsp_url, timeout)
    except DspUnreadable:
        return Tally(0, 0, missing=True)


def _executor_side(
    executor_db: Path, writes: list[DspWrite],
) -> tuple[dict[str, FirstRow], dict[tuple[str, int, str], list[str]]]:
    """讀完 DSP 的窗之後才開執行端快照:這批寫入的第一列,與每份提案內容的所有鍵。"""
    inbox = ReadOnlyInbox(executor_db)
    try:
        with inbox.read_transaction() as tx:
            firsts = attempt_store.first_rows_for(tx, [w.key for w in writes])
            siblings = {}
            for first in firsts.values():
                ident = _identity(first)
                if ident is not None and ident not in siblings:
                    siblings[ident] = [row.key for row in attempt_store.first_rows_for_proposal(
                        tx, ident[0], ident[1]) if _identity(row) == ident]
    finally:
        inbox.close()
    return firsts, siblings


def _tally_duplicates(
    writes: list[DspWrite], firsts: Mapping[str, FirstRow],
    siblings: Mapping[tuple[str, int, str], list[str]], dsp_url: str, timeout: float,
) -> Tally:
    """快照讀不回來的第一列不知道提案內容,算無法核對;快照讀得回來卻對不上它的鍵(資料異常):證實
    有更早的同內容提交照樣判壞,證實不了就算無法核對。兩種都絕不算好事件(代碼審第 1 輪)。"""
    good = valid = unverifiable = 0
    bad_at, seen_keys = [], set()
    commits: dict[tuple[str, int, str], list[tuple[str, int]]] = {}
    for entry in writes:
        first = firsts.get(entry.key)
        if first is None:
            unverifiable += 1
            continue
        own = (iso(entry.committed_at), entry.operation_id)
        if entry.key in seen_keys:  # 同一把鍵第二筆操作:不用看快照就證實重複
            valid += 1
            bad_at.append(own[0])
            continue
        seen_keys.add(entry.key)
        ident = _identity(first)
        if ident is None:
            unverifiable += 1
            continue
        if ident not in commits:
            found = [_operation(dsp_url, key, timeout) for key in siblings.get(ident, [])
                     if key != entry.key]
            commits[ident] = [c for c in found if c is not None]
        if any(other < own for other in commits[ident]):
            valid += 1
            bad_at.append(own[0])
        elif first.snapshot_matches_key:
            valid += 1
            good += 1
        else:
            unverifiable += 1
        commits[ident].append(own)
    return Tally(good, valid, tuple(bad_at), unverifiable)
