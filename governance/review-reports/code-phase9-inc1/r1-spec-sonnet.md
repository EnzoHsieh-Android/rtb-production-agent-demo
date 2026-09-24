severity: major
我審完 governance/review-reports/code-phase9-inc1/r1-snapshot-src.patch,對照規格文件「增量 1」節(docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase9可觀測與SLO_計劃.md 第 51–140 行)。8 條實作員自述逐一核對程式碼後,只有第 1 條(放掉租約的原因代碼)確認違反規格字面;其餘 7 條(事件租戶各路徑、DSP 呼叫失敗分類、呼叫紀錄寫入不吞忙碌、回呼範圍隔離、嘗試紀錄 by= 機械守衛、程式版本擺放)逐一追蹤實際程式碼後與規格一致,不構成違規,故不列入報告。

完整報告全文如下:

---

severity: major

### 1. 放掉租約事件的原因代碼不是「最後一次失敗原因」,而是這次呼叫自己傳入的原因,沒有跟表上既有的 last_failure 值同步
severity: major
blocking: 是
引句:「self._log_held(receipt, now, LifecycleKind.LEASE_RELEASED, reason=failure)」
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase9可觀測與SLO_計劃.md:62`(規格原文:「放掉租約:寫「放掉租約」,原因代碼是最後一次失敗原因(可為空)。」)
file: `src/rtb/executor/inbox_store.py:1244-1259`(release() 函式本體)
file: `src/rtb/executor/execution.py:740,768,794,865`(四處以 `self.store.release(tx, receipt, now, None)` 呼叫)

觸發情境:一份提案先在某次租約週期因某個原因(例如 `LastFailure.TABLE_FULL`)被 release,表上 `proposals.last_failure` 因 SQL 的 `coalesce(?, last_failure)` 正確存下該原因;下一個租約週期再次取件、簽發後,卻因另一條「防線」路徑(核可被別人搶簽、決策過時要重來、同廣告被鎖、有效核可被取代等)再次呼叫 `release(tx, receipt, now, None)`。

錯誤行為:`release()` 對資料表的更新用 `last_failure = coalesce(?, last_failure)` 正確保留了舊值(即規格所稱「最後一次失敗原因」這個會滾動保留的欄位);但緊接著寫生命週期事件時,`_log_held(receipt, now, LifecycleKind.LEASE_RELEASED, reason=failure)` 傳的是**這次呼叫本身的 `failure` 參數**(此時是 `None`),不是 coalesce 之後、表上實際保留下來的那個「最後一次失敗原因」。結果是:資料表裡明明還記著上一次的失敗原因,但這一列「放掉租約」事件的 `reason` 欄卻寫成空值——事件表(本增量存在的目的之一就是在收件表被保留期清除後,仍要能查到這些欄位,見 [S613] 精神)少記了規格明文要求的內容,且與同一次交易裡實際寫入 proposals 表的 last_failure 值不一致。這不是罕見角落案例:`release(..., None)` 在 execution.py 至少 4 處正常業務路徑(非例外/非停機)上都會觸發。

建議修法:`_log_held` 寫 LEASE_RELEASED 事件時,`reason` 應該取 coalesce 之後的值(即讀回 UPDATE 後的 `last_failure`,或在呼叫端先算出 `failure or 舊值` 再一併傳給 `_held` 與 `_log_held`),讓事件記錄的原因代碼跟規格「原因代碼是最後一次失敗原因」的定義一致,也跟同一列資料表寫入的內容一致。
