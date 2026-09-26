"""一步的時間預算(Phase 13 增量 2,計劃〈租約、逾時與停止訊號〉,[S1113] [S1136]):分析端驅動命令列的
租約守衛、展示啟動器停分析端的寬限時間、模型後端等行程群組結束的秒數、花費帳結算的嘗試次數,都從這裡
取同一組常數,誰都不必從模型後端匯入(邊界測試只准模型用戶端碰後端)。

檔名刻意不以 model 開頭、不含 claude 字樣:改寫後的 [S917] 把 src/rtb/ 底下 model 開頭的檔都當模型
用戶端,展示啟動器的原始碼又不准出現 claude。這支檔進模型用戶端的匯入閉包(模型後端與花費帳從這裡取
常數),Phase 11B [S918] 的名單有列它。

數字都是暫用值,沒有實測校準(計劃 REVISIT 2026-12-31 看展示紀錄裡開 AI 的情境實際耗時)。

Phase 14 增量 3(計劃 [[Projects/RTB_Phase14正式規則照九條判斷_計劃]]〈拆增量〉3):AI 退出分析端決策,
AI 那一步的最壞耗時、帶 --ai-judge 的停止寬限與展示等模式行的登入預檢時限三支函式撤除;留規則輪共用
常數([S1405] [S1419])與模型後端、花費帳、說明/假說入口仍用的常數。
"""

from rtb.sqlitekit import BUSY_TIMEOUT_SECONDS

# 基本那一步兩次讀 DSP(廣告現況、成效指標)或一次送件([S1001]);規則輪各步另見
# RULE_STEP_READS([S1405])
CALLS_PER_STEP = 2
DEFAULT_TIMEOUT_SECONDS = 3.0  # 分析端驅動命令列的 DSP 與收件口逾時預設值(展示用)
MODEL_TIMEOUT_SECONDS = 15.0  # AI 調查(評估)一次模型呼叫的逾時(第 1 版 25、第 1 輪 20)
GROUP_EXIT_WAIT_SECONDS = 5.0  # 模型後端每次等 claude 行程群組結束的秒數(一次呼叫最多等兩次)
GROUP_EXIT_WAITS = 2
SETTLE_ATTEMPTS = 3  # 花費帳結算寫不進去時的嘗試次數(含第一次);仍失敗就把金額印到標準錯誤
LEDGER_RESERVATIONS = 1  # 每次呼叫預留一次
# 模型後端空暫存 HOME 隔離時的登入權杖變數名(使用者 2026-09-25 裁定用 `setup-token` 的長期權杖);放在
# 這裡是因為展示啟動器也要照名字轉交,而它的原始碼不准提到模型後端
LOGIN_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"  # noqa: S105 - 變數名,不是權杖
LOGIN_CHECK_TIMEOUT_SECONDS = 10.0  # 模型後端的登入狀態檢查(啟動時的預檢也是這一次)


# 正式規則的規則輪(Phase 14 增量 2b,[S1405]):每一步讀幾次 DSP。A 讀現況與 1 小時指標;B
# 讀操作歷史與過去
# 調整;C 讀逐日、1 天、7 天,並重讀現況與 1 小時指標。分析端驅動的租約守衛、規則輪的讀取、
# 展示啟動器的
# 停止寬限共用這一份([S1419])
RULE_STEP_READS: dict[str, int] = {"A": 2, "B": 2, "C": 5}


def collect_step_worst_seconds(dsp_timeout_seconds: float, reads: int) -> float:
    """蒐集證據那一步的最壞耗時(Phase 13 定的算式,規則輪各步沿用),逐項加總:取租約等鎖、
    每次讀取的 DSP 逾時加呼叫紀錄等鎖、提交等鎖。"""
    return (BUSY_TIMEOUT_SECONDS + reads * (dsp_timeout_seconds + BUSY_TIMEOUT_SECONDS)
            + BUSY_TIMEOUT_SECONDS)


def rule_step_worst_seconds(dsp_timeout_seconds: float) -> dict[str, float]:
    """規則輪每一步的最壞耗時(同蒐證那一步的逐項加總):預設逾時 3 秒時 A=26、B=26、C=50 秒。"""
    return {step: collect_step_worst_seconds(dsp_timeout_seconds, reads)
            for step, reads in RULE_STEP_READS.items()}


def rule_stop_grace_seconds(dsp_timeout_seconds: float) -> float:
    """分析端收到停止後最多還要多久:規則輪最長那一步的最壞耗時(預設 50 秒,[S1419])。"""
    return max(rule_step_worst_seconds(dsp_timeout_seconds).values())
