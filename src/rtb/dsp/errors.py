"""Mock DSP 的型別化錯誤:呼叫端靠型別分辨「可重試」「永久拒絕」「版本已變」。

這是外部邊界的慣例:協定失敗用例外,呼叫端必須分辨並處理。領域層(rtb.domain)相反,
預期中的資料狀態(沒資料、分母為零)用結果型別,見 rtb/domain/metrics.py。
"""


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


class MetricsNotFound(PermanentError):
    """這個廣告在這個時間窗沒有指標資料。"""


class CapabilityError(PermanentError):
    """寫入能力憑證不通過:都發生在任何寫入之前,原樣重送不會通過。"""


class CapabilityNotConfigured(CapabilityError):
    """DSP 沒有可用的金鑰(沒有或太短):所有寫入一律拒收,不是放行。"""


class CapabilityMissing(CapabilityError):
    """寫入請求沒帶憑證。"""


class CapabilityInvalid(CapabilityError):
    """憑證格式、簽章、聲明欄位或型別、格式版本任一不對。"""


class CapabilityExpired(CapabilityError):
    """憑證不在有效時間窗內。"""


class CapabilityScopeMismatch(CapabilityError):
    """請求(廣告、動作、冪等鍵、租戶、新預算、預期版本)與聲明不符。"""


class OperationVoided(PermanentError):
    """這把冪等鍵已被作廢(對帳判失敗之前的證明):同鍵的新寫入一律拒收,三張表都不動。"""


class DailyNotFound(PermanentError):
    """這個廣告沒有種逐日成效(Phase 13 增量 2):整個廣告沒種,不是某幾天缺資料。"""


class AdjustmentsNotFound(PermanentError):
    """這個廣告沒有種過去調整(Phase 13 增量 2);種了零筆是「有資料、零筆」,不是這個。"""
