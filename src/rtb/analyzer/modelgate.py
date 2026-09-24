"""分析端模型閘道(Phase 13 增量 1,計劃〈共用模型入口與行程〉):分析端唯一准匯入模型用戶端的地方。

使用者裁定「共用同一個模型入口」落在這裡:分析端要呼叫模型的地方(模型說明命令列;增量 2 起加上 AI
決策函式所在模組與分析端驅動命令列)都經它,准匯入它的分析端模組寫死在邊界測試([S1100])。流程推進與
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

from rtb import modelclient as mc
from rtb.modelclient import Caller as Caller
from rtb.modelclient import CallTerminated as CallTerminated
from rtb.modelclient import LedgerBusy as LedgerBusy
from rtb.modelclient import LoginPreflight as LoginPreflight
from rtb.modelclient import Mode as Mode
from rtb.modelclient import ModelCallFailed as ModelCallFailed
from rtb.modelclient import ModelResult as ModelResult
from rtb.modelclient import Outcome as Outcome
from rtb.modelclient import Placeholders as Placeholders
from rtb.modelclient import Preflight as Preflight
from rtb.modelclient import Source as Source
from rtb.modelclient import UnknownModel as UnknownModel


class GateRefused(ValueError):
    """入口參數跟模式對不上(例如即時模式還給了帳檔路徑):拒絕啟動,什麼都沒呼叫。"""


@dataclass(frozen=True)
class Gate:
    """一個入口行程判好的模型設定;之後每一次呼叫都沿用,不再重判模式。"""

    settings: mc.Settings
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

    def complete(self, caller: Caller, system: str, user: str, *, max_output_tokens: int,
                 timeout_seconds: float) -> ModelResult:
        """送出一次模型呼叫(或讀錄製)。失敗丟 `ModelCallFailed` 的子類別;停止訊號丟
        `CallTerminated`。"""
        request = mc.ModelRequest(caller=caller, system=system, user=user,
                                  max_output_tokens=max_output_tokens,
                                  timeout_seconds=timeout_seconds, demo_id=self.demo_id,
                                  batch_id=self.batch_id)
        return mc.call_model(request, self.settings, recordings_dir=self.recordings,
                             ledger=self.ledger)

    def deadline_seconds(self, timeout_seconds: float) -> float:
        """一次呼叫從送出到回來最壞要多久(模型逾時加模型用戶端自己的清理與等鎖);入口拿來核對租約或
        領取期限。"""
        return mc.call_deadline_seconds(timeout_seconds)

    def preflight_login(self) -> LoginPreflight:
        """啟動時的登入預檢(即時模式才真的檢查,錄製模式回不適用)。"""
        return mc.preflight_login(self.settings)


def open_gate(environ: Mapping[str, str], *, demo_id: str | None, ledger: Path | None,
              recordings: Path | None, batch_id: str | None = None) -> Gate:
    """在入口判一次模式。RTB_MODEL 指定的模型不在價目表丟 UnknownModel;即時模式給了帳檔路徑丟
    GateRefused。"""
    claude = shutil.which("claude", path=environ.get("PATH", ""))
    settings = mc.settings_from_env(environ, demo_id, claude)
    if settings.mode is Mode.LIVE and ledger is not None:
        raise GateRefused("即時模式的花費帳寫死在帳號家目錄那一本,不接受換帳檔路徑")
    return Gate(settings, demo_id, Path(ledger) if ledger is not None else mc.live_ledger_path(),
                Path(recordings) if recordings is not None else mc.default_recordings_dir(),
                batch_id)
