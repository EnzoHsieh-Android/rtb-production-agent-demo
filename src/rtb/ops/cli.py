"""維運套件命令列共用的參數解析:參數錯一律以 EXIT_BAD_ARGUMENTS 結束,時間參數一律要帶時區。

argparse 預設用 2 結束,跟追蹤檢視、指標、服務水準的「資料庫檔不存在」撞號,呼叫端(監控、手冊)
分不出是忘了傳參數還是資料庫不在(Phase 9 增量 2 代碼審第 1、2 輪)。維運套件的每支命令列都用
這裡的解析器,結束代碼表一致。
"""

import argparse
import sys
from datetime import datetime
from typing import NoReturn

from rtb.domain._checks import require_aware

EXIT_BAD_ARGUMENTS = 7  # 參數錯(缺參數、參數組合不對、時間沒帶時區)


class Parser(argparse.ArgumentParser):
    """參數錯一律以 EXIT_BAD_ARGUMENTS 結束,訊息印到標準錯誤。"""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_BAD_ARGUMENTS, f"{self.prog}: 參數錯誤:{message}\n")


def aware_time(text: str) -> datetime:
    """時間參數共用的型別(指標的 --since、--until、--now,服務水準的 --now):ISO 8601,一定要帶時區
    (Z 或 +08:00 這類偏移);沒帶或看不懂都算參數錯。"""
    try:
        value = datetime.fromisoformat(text)
    except ValueError as bad:
        raise argparse.ArgumentTypeError(f"看不懂的時間:{text}") from bad
    try:
        require_aware(value)
    except ValueError as naive:
        raise argparse.ArgumentTypeError(f"{naive}(例:{text}Z 或 {text}+08:00)") from naive
    return value
