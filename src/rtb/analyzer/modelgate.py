"""分析端模型閘道(Phase 13 增量 1,計劃〈共用模型入口與行程〉):分析端唯一准匯入模型用戶端的地方。

使用者裁定「共用同一個模型入口」落在這裡:分析端要呼叫模型的地方(模型說明命令列、AI 決策函式所在
模組與分析端驅動命令列)都經它,准匯入它的分析端模組寫死在邊界測試([S1100])。流程推進與
決策規則的匯入閉包不含模型用戶端的任何一支模組。

閘道負責入口該判的事,判一次、之後沿用:
- 模式:照 Phase 11B 的 `settings_from_env`(即時開關、展示編號、找得到 claude、啟用紀錄有效,
全齊才即時,
  否則錄製並帶原因)。claude 的絕對路徑在這裡用呼叫端給的環境的 PATH 找,模型用戶端自己不查 PATH。
- 帳檔:即時模式寫死帳號家目錄那一本,不接受換路徑;錄製模式可以換(一鍵展示把錄製重播的 0 元紀錄導到
  這次展示的暫存目錄,Phase 12 [S1030];Phase 13 [S1102])。
- 錄製目錄:沒給就用專案根的 recordings/model/。
- 停止訊號:模型呼叫期間的 SIGTERM 由模型用戶端轉成 `CallTerminated`(繼承 BaseException),這裡原樣轉手
  給呼叫端;閘道與它的呼叫端都不從模型後端匯入任何名字([S1154])。
"""

import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# 只轉手型別與函式,不綁模型用戶端的模組物件(代碼審 r1:公開模組物件就能經 modelgate.mc 繞過閘道)
from rtb.modelclient import NUMERALS_RULE as NUMERALS_RULE
from rtb.modelclient import Caller as Caller
from rtb.modelclient import CallTerminated as CallTerminated
from rtb.modelclient import LedgerBusy as LedgerBusy
from rtb.modelclient import LoginPreflight as LoginPreflight
from rtb.modelclient import (
    MixedRecordingsDir,
    ModelRequest,
    Settings,
    call_deadline_seconds,
    call_model,
    check_recordings_dir,
    default_recordings_dir,
    live_ledger_path,
    settings_from_env,
)
from rtb.modelclient import Mode as Mode
from rtb.modelclient import ModelCallFailed as ModelCallFailed
from rtb.modelclient import ModelResult as ModelResult
from rtb.modelclient import Outcome as Outcome
from rtb.modelclient import Placeholders as Placeholders
from rtb.modelclient import Preflight as Preflight
from rtb.modelclient import Source as Source
from rtb.modelclient import UnknownModel as UnknownModel
from rtb.modelclient import preflight_login as _preflight_login
from rtb.modelclient import traceable_sentences as traceable_sentences


class GateRefused(ValueError):
    """入口參數跟模式對不上(例如即時模式還給了帳檔路徑):拒絕啟動,什麼都沒呼叫。"""


@dataclass(frozen=True)
class Gate:
    """一個入口行程判好的模型設定;之後每一次呼叫都沿用,不再重判模式。呼叫者標籤在開閘道時綁死,
    送出時不收(代碼審 r2:花費上限看呼叫者,標籤要綁在入口;邊界測試核對每支模組開閘道時帶的
    是不是它准用的)。"""

    settings: Settings
    caller: Caller
    demo_id: str | None
    ledger: Path
    recordings: Path
    batch_id: str | None = None

    @property
    def mode(self) -> Mode:
        return self.settings.mode

    @property
    def notices(self) -> tuple[str, ...]:
        return self.settings.notices

    def complete(self, system: str, user: str, *, max_output_tokens: int,
                 timeout_seconds: float) -> ModelResult:
        """送出一次模型呼叫(或讀錄製)。失敗丟 `ModelCallFailed` 的子類別;停止訊號丟
        `CallTerminated`。"""
        request = ModelRequest(caller=self.caller, system=system, user=user,
                               max_output_tokens=max_output_tokens,
                               timeout_seconds=timeout_seconds, demo_id=self.demo_id,
                               batch_id=self.batch_id)
        return call_model(request, self.settings, recordings_dir=self.recordings,
                          ledger=self.ledger)

    def deadline_seconds(self, timeout_seconds: float) -> float:
        """一次呼叫從送出到回來最壞要多久(模型逾時加模型用戶端自己的清理與等鎖);入口拿來核對租約或
        領取期限。"""
        return call_deadline_seconds(timeout_seconds)

    def check_recordings(self) -> None:
        """開錄前的目錄檢查(即時加錄製模式,Phase 13 [S1142]):目錄要是空的或只有同一批的檔,不符丟
        GateRefused(沒帶批次編號也是)。開閘道時已經檢查過一次;這支給入口在印就緒之前再核一次。"""
        try:
            check_recordings_dir(self.recordings, self.batch_id)
        except MixedRecordingsDir as mixed:
            raise GateRefused(f"錄製目錄不能開錄:{mixed}") from mixed

    def preflight_login(self) -> LoginPreflight:
        """啟動時的登入預檢(即時模式才真的檢查,錄製模式回不適用)。"""
        return _preflight_login(self.settings)


def open_gate(environ: Mapping[str, str], *, caller: Caller, demo_id: str | None,  # noqa: PLR0913 - 入口判模式要的每一樣
              ledger: Path | None, recordings: Path | None, batch_id: str | None = None,
              recorded_ledger: Path | None = None) -> Gate:
    """在入口判一次模式。RTB_MODEL 指定的模型不在價目表丟 UnknownModel;即時模式給了帳檔路徑、
    或即時加錄製卻沒帶批次、錄製目錄不是新的(空的或只有同一批的錄製檔),丟 GateRefused:
    在入口拒絕,不等到第一次呼叫才失敗(代碼審 r1,比照 [S1142])。
    預設的錄製目錄是專案根的入庫目錄,只供重播。recorded_ledger:判成錄製時才用的帳檔,判成即時就
    忽略、照舊用帳號家目錄那一本(Phase 13 增量 4 代碼審 r1 h1:呼叫端不猜模式,一律給,由這裡決定)。"""
    claude = shutil.which("claude", path=environ.get("PATH", ""))
    settings = settings_from_env(environ, demo_id, claude)
    folder = Path(recordings) if recordings is not None else default_recordings_dir()
    if settings.mode is Mode.LIVE and ledger is not None:
        raise GateRefused("即時模式的花費帳寫死在帳號家目錄那一本,不接受換帳檔路徑")
    if settings.mode is Mode.LIVE and settings.record:
        try:
            check_recordings_dir(folder, batch_id)
        except MixedRecordingsDir as mixed:
            raise GateRefused(f"即時加錄製模式拒絕啟動:{mixed}") from mixed
    if not isinstance(caller, Caller):
        raise GateRefused("呼叫者必須是封閉列舉的成員")
    chosen = ledger if ledger is not None else (
        recorded_ledger if settings.mode is Mode.RECORDED else None)
    return Gate(settings, caller, demo_id,
                Path(chosen) if chosen is not None else live_ledger_path(), folder, batch_id)
