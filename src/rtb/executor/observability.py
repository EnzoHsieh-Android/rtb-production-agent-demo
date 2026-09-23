"""可觀測的唯讀查詢(Phase 6 增量 4):總曝險停下次數、表滿延後份數、額度使用率、總曝險稽核明細。

資料一律從既有的耐久紀錄讀——停下紀錄表與嘗試紀錄——不另存計數器。一張表只透過擁有它的模組讀:
停下紀錄走收件口模組的方法(先核對交易是它開的),嘗試紀錄走嘗試紀錄模組的函式;這裡不寫 SQL
(代碼審第 1 輪架構席)。每支都收執行行程資料庫交易入口開的交易(會握寫入鎖,跟 Phase 5 的
`version_conflict_count` 同一種做法),只讀不寫。依賴方向只有「這裡 → 收件口模組、嘗試紀錄模組」,
那兩個模組都不依賴這裡。人工核可的查詢三隨增量 3 交付。

稽核明細的時間範圍不設上限:給極寬的範圍會掃大段歷史、握寫入鎖。目前只有人工與測試會呼叫;
Phase 9 接告警或輪詢時要一併決定範圍上限與不握寫入鎖的讀法(代碼審第 1 輪資安席)。

時間範圍一律包含起點、不包含終點;停下紀錄與嘗試紀錄的時間都寫成同一種固定格式的 UTC 字串,字串
比較就是時間比較。「現在」與門檻都由呼叫端傳入:額度窗口依「現在」算,兩支查詢要對帳就傳同一個;
門檻由呼叫端用簽發模組的 `load_tenants` 取得(那裡帶著設定檔的安全檢查)。
"""

from dataclasses import dataclass
from datetime import datetime

from rtb.executor import attempt_store
from rtb.executor.attempt_store import CountedFirstRow, ExecutorTransaction
from rtb.executor.inbox_store import InboxStore, StopKind


@dataclass(frozen=True)
class Utilization:
    """額度使用率:整數不回比例(整數到上限時浮點會失真)。門檻 0(設定檔缺這欄)是合法設定,
    這時不准加預算、比例沒有定義,呼叫端看旗標,不要去除以 0。"""

    used: int
    limit: int
    remaining: int
    increases_allowed: bool


@dataclass(frozen=True)
class Stopped:
    key: str
    task_id: str
    revision: int
    content_hash: str
    campaign_id: str
    amount: int
    used: int | None
    limit: int | None
    at: str


@dataclass(frozen=True)
class Entry:
    """一筆照逐列計入規則算進這個租戶的寫入:身分、計入金額、目前狀態、現在還佔不佔額度、是不是
    Phase 6 之前沒有租戶的舊列(照既有規則算進每一個租戶)。"""

    key: str
    task_id: str | None
    revision: int | None
    campaign_id: str
    amount: int
    started_at: str
    state: str
    counted_now: bool
    legacy: bool


@dataclass(frozen=True)
class AggregateAudit:
    stopped: tuple[Stopped, ...]  # 範圍內的總曝險停下(不含表滿延後、比例過大)
    passed: tuple[Entry, ...]  # 範圍內開始、照計入規則算進的寫入,不看 24 小時窗口
    holding: tuple[Entry, ...]  # 目前佔額度的每一筆,不受範圍限制;加總就是已用額度
    utilization: Utilization


def aggregate_stop_count(
    store: InboxStore, tx: ExecutorTransaction, *, tenant: str | None = None,
    campaign_id: str | None = None, since: datetime | None = None, until: datetime | None = None,
) -> int:
    """總曝險停下次數:被停下的次數,不是最後結果(增量 3 之後其中有些會被核可放行)。"""
    return store.stop_count(tx, StopKind.AGGREGATE_LIMIT_REACHED, tenant=tenant,
                            campaign_id=campaign_id, since=since, until=until)


def table_full_deferral_count(
    store: InboxStore, tx: ExecutorTransaction, *, tenant: str | None = None,
    campaign_id: str | None = None, since: datetime | None = None, until: datetime | None = None,
) -> int:
    """表滿延後次數:同一份提案同一種類只記一列,數的是「被延後過的提案份數」,不是延後事件次數。"""
    return store.stop_count(tx, StopKind.TABLE_FULL, tenant=tenant, campaign_id=campaign_id,
                            since=since, until=until)


def utilization(tx: ExecutorTransaction, tenant: str, limit: int, now: datetime) -> Utilization:
    """只讀嘗試紀錄,所以不收收件表(另外三支讀停下紀錄,要收件表來核對交易歸屬)。"""
    return _utilization(attempt_store.aggregate_used(tx, tenant, now), limit)


def _utilization(used: int, limit: int) -> Utilization:
    return Utilization(used, limit, max(0, limit - used), limit > 0)


def aggregate_audit(  # noqa: PLR0913 - 收件表、交易、租戶、門檻、現在、範圍都是稽核必須的輸入
    store: InboxStore, tx: ExecutorTransaction, tenant: str, limit: int, now: datetime,
    since: datetime | None, until: datetime | None,
) -> AggregateAudit:
    """總曝險稽核明細(事故 F7「哪些通過、哪些被阻擋、剩餘多少」):四樣都用同一個傳入的「現在」。"""
    if since is None or until is None:
        raise ValueError("稽核明細一定要給時間範圍:停下紀錄表只增不改,不給範圍會整張撈出來")
    stopped = tuple(Stopped(*row) for row in store.stops(  # type: ignore[arg-type]
        tx, StopKind.AGGREGATE_LIMIT_REACHED, tenant, since, until))
    holding = attempt_store.aggregate_holdings(tx, tenant, now)  # 同一次查詢帶出身分,加總就是已用
    counted = {row.key for row in holding}
    passed = attempt_store.counted_first_rows_started(tx, tenant, since, until)
    used = sum(row.amount for row in holding)
    return AggregateAudit(stopped, _entries(passed, counted), _entries(holding, counted),
                          _utilization(used, limit))


def _entries(rows: tuple[CountedFirstRow, ...], counted: set[str]) -> tuple[Entry, ...]:
    """目前狀態已經跟清單同一次查詢帶出,這裡不再逐鍵回查(大量紀錄時會在寫入鎖裡跑太久)。"""
    return tuple(Entry(row.key, row.task_id, row.revision, row.campaign_id, row.amount,
                       row.started_at, row.state, row.key in counted, row.legacy) for row in rows)
