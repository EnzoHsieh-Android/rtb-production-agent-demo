"""Mock DSP 的型別化錯誤:呼叫端靠型別分辨「可重試」「永久拒絕」「版本已變」。"""


class DspError(Exception):
    """所有 DSP 錯誤的基底。"""


class TransientError(DspError):
    """暫時性錯誤:同一把冪等鍵可以安全重試。"""


class StoreBusy(TransientError):
    """儲存正被別的寫入佔用,等待逾時。"""


class PermanentError(DspError):
    """永久性錯誤:原樣重試不會成功。"""


class ValidationRejected(PermanentError):
    """請求內容不合法,例如預算不是正數。"""


class UnknownAction(PermanentError):
    """不認得的動作。"""


class CampaignNotFound(PermanentError):
    """找不到這個廣告。"""


class IdempotencyConflict(PermanentError):
    """同一把冪等鍵搭配了不同內容。"""


class VersionConflict(PermanentError):
    """預期版本與 DSP 目前版本不符,提案已過期,不可強制覆寫。"""
