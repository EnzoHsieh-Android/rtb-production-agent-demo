"""維運套件命令列共用的參數解析:參數錯一律以 EXIT_BAD_ARGUMENTS 結束。

argparse 預設用 2 結束,跟追蹤檢視與指標的「資料庫檔不存在」撞號,呼叫端(監控、手冊)分不出是忘了
傳參數還是資料庫不在(Phase 9 增量 2 代碼審第 1、2 輪)。維運套件的每支命令列都用這裡的解析器,
結束代碼表一致。
"""

import argparse
import sys
from typing import NoReturn

EXIT_BAD_ARGUMENTS = 7  # 參數錯(缺參數、參數組合不對、時間沒帶時區)


class Parser(argparse.ArgumentParser):
    """參數錯一律以 EXIT_BAD_ARGUMENTS 結束,訊息印到標準錯誤。"""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_BAD_ARGUMENTS, f"{self.prog}: 參數錯誤:{message}\n")
