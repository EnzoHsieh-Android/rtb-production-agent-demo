"""領域層共用的小檢查:同一個規則只有一份定義,免得各模組改了一處漏另一處。

只放「證據編號、任務編號這類識別碼的格式」與「整數不含布林」這類到處要用的判斷。
判斷函式用 TypeGuard 標明「通過之後是什麼型別」,讓型別檢查(mypy)能驗證邊界判斷。
DSP(rtb.dsp)是外部系統的模擬器,刻意不依賴這裡,所以它自己另有一份。
"""

import re
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
