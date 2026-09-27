"""規則模式探索的歷史錄製鍵(Phase 15 增量 2,計劃 [[Projects/RTB_Phase15AI找規則模式_計劃]]
〈模型建議、機械核對與人讀報告〉〈回退〉)。

用字面 `Caller.RULE_MINING` 與評估版本的輸出上限,照模型用戶端的錄製鍵函式重算「這份系統提示加這張
彙總表」應有的錄製鍵;缺錄製時給人看預期鍵,批次驗收(增量 3)比對目錄鍵集合也用它。

這支是**歷史驗證**的一半:停用模型探勘或回退增量 2/3 時,送出路徑(分析端窄入口與評估端的探勘
執行器)撤掉,這支照留,舊錄製的鍵仍能重算、舊帳仍計上限([S1517]);所以它只經模型用戶端門面取
錄製鍵函式與呼叫者列舉,不匯入模型閘道或任何送出入口。
"""

from rtb import modelclient as mc
from rtb.eval import rule_mining_vocab as v


def expected_key(system: str, table: str, model: str | None = None) -> str:
    """這份提示在規則模式探索呼叫者、評估版本輸出上限下的錄製鍵(模型預設模型用戶端的預設模型)。"""
    return mc.recording_key(mc.Caller.RULE_MINING, model or mc.DEFAULT_MODEL, system, table,
                            v.MAX_OUTPUT_TOKENS)
