"""領域層共用的小檢查:同一個規則只有一份定義,免得各模組改了一處漏另一處。

只放「證據編號、任務編號這類識別碼的格式」「整數不含布林」「時間帶時區」這類到處要用的判斷。
判斷函式用 TypeGuard 標明「通過之後是什麼型別」,讓型別檢查(mypy)能驗證邊界判斷。
DSP(rtb.dsp)是外部系統的模擬器,刻意不依賴這裡,所以它自己另有一份。
"""

import re
from datetime import datetime
from typing import TypeGuard

ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")


def is_plain_int(value: object) -> TypeGuard[int]:
    """是整數,而且不是布林(Python 的 True 也是整數,會繞過型別檢查)。"""
    return isinstance(value, int) and not isinstance(value, bool)


def is_plain_number(value: object) -> TypeGuard[int | float]:
    """是整數或浮點數,而且不是布林。"""
    return isinstance(value, int | float) and not isinstance(value, bool)


def is_id(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and ID_PATTERN.fullmatch(value) is not None


def is_aware(value: object) -> TypeGuard[datetime]:
    """是帶時區的時間,而且時區真的給得出偏移(有些 tzinfo 的 utcoffset 回 None,等於沒有時區)。

    沒有時區的時間換算 UTC 時會被當成伺服器本地時間,悄悄寫錯。證據、提案、嘗試紀錄共用這一份。
    """
    return isinstance(value, datetime) and value.utcoffset() is not None


AWARE_REQUIRED = "時間必須帶時區"


def require_aware(*moments: object) -> None:
    """每一個都要是帶時區的時間,否則丟 ValueError(訊息固定)。沒帶會被當成本機時間換算,時間範圍整段
    位移;有帶沒帶混用則比較時丟 TypeError。嘗試紀錄、分析端任務歷史、指標共用這一支(Phase 9
    增量 2 代碼審第 2 輪:原本三處各寫一份,判準與訊息各自漂移)。"""
    if not all(is_aware(moment) for moment in moments):
        raise ValueError(AWARE_REQUIRED)
