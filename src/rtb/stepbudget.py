"""一步的時間預算(Phase 13 增量 2,計劃〈租約、逾時與停止訊號〉,[S1113] [S1136]):分析端驅動命令列的
租約守衛、展示啟動器停分析端的寬限時間、模型後端等行程群組結束的秒數、花費帳結算的嘗試次數,都從這裡
取同一組常數,誰都不必從模型後端匯入(邊界測試只准模型用戶端碰後端)。

檔名刻意不以 model 開頭、不含 claude 字樣:改寫後的 [S917] 把 src/rtb/ 底下 model 開頭的檔都當模型
用戶端,展示啟動器的原始碼又不准出現 claude。這支檔進模型用戶端的匯入閉包(模型後端與花費帳從這裡取
常數),Phase 11B [S918] 的名單有列它。

數字都是暫用值,沒有實測校準(計劃 REVISIT 2026-12-31 看展示紀錄裡開 AI 的情境實際耗時)。
"""

from rtb.sqlitekit import BUSY_TIMEOUT_SECONDS

CALLS_PER_STEP = 2  # 沒開 AI 時一步最多兩次讀 DSP(廣告現況、成效指標)或一次送件([S1001])
DEFAULT_TIMEOUT_SECONDS = 3.0  # 分析端驅動命令列的 DSP 與收件口逾時預設值(展示用)
MODEL_TIMEOUT_SECONDS = 15.0  # AI 那一步的模型逾時(第 1 版 25、第 1 輪 20,改小是為了吃下續租等鎖)
GROUP_EXIT_WAIT_SECONDS = 5.0  # 模型後端每次等 claude 行程群組結束的秒數(一次呼叫最多等兩次)
GROUP_EXIT_WAITS = 2
SETTLE_ATTEMPTS = 3  # 花費帳結算寫不進去時的嘗試次數(含第一次);仍失敗就把金額印到標準錯誤
LEDGER_RESERVATIONS = 1  # 每次呼叫預留一次


def collect_step_worst_seconds(dsp_timeout_seconds: float, reads: int) -> float:
    """開 AI 時蒐集證據那一步的最壞耗時,逐項加總:取租約等鎖、每次讀取的 DSP 逾時加呼叫紀錄等鎖、
    提交等鎖。"""
    return (BUSY_TIMEOUT_SECONDS + reads * (dsp_timeout_seconds + BUSY_TIMEOUT_SECONDS)
            + BUSY_TIMEOUT_SECONDS)


def ai_step_worst_seconds() -> float:
    """AI 那一步從續租拿到鎖、讀時鐘之後算起的最壞耗時:模型逾時、兩次等行程群組、花費帳預留一次加
    結算最多三次的等鎖、提交等鎖(15 + 10 + 20 + 5 = 50)。續租自己的等鎖在讀時鐘之前,不佔新租約。"""
    ledger_waits = (LEDGER_RESERVATIONS + SETTLE_ATTEMPTS) * BUSY_TIMEOUT_SECONDS
    return (MODEL_TIMEOUT_SECONDS + GROUP_EXIT_WAITS * GROUP_EXIT_WAIT_SECONDS + ledger_waits
            + BUSY_TIMEOUT_SECONDS)


def ai_stop_grace_seconds() -> float:
    """帶 --ai-judge 的分析端收到停止後最多還要多久:停止可能在續租等鎖時送到,從那一刻算起是續租
    等鎖加上續租後的最壞耗時(5 + 50 = 55)。"""
    return BUSY_TIMEOUT_SECONDS + ai_step_worst_seconds()
