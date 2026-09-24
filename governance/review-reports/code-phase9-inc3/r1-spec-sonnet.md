severity: major
Both external precedents confirmed. Here is the complete report.

---

severity: major

### 1. 端到端交給執行三輪快照不穩定時,沒有回報不穩定,牴觸事件型「答案不變」的前提
severity: major
blocking: 是
引句:「三輪讀到的都不同時回最後一輪,計數裡沒有另標不穩定(計數結構沒有這一欄)」
引句:「三輪都不同回最後一輪(計數照實,不另外標)。」
file: `src/rtb/ops/sli.py:39`(`MAX_ROUNDS = 3`)、`src/rtb/ops/sli.py:226-236`(`end_to_end_handoff`)
file: `src/rtb/ops/trace.py:427-434`(`build_trace`:三輪仍不同時回 `stable=False`)
file: `src/rtb/ops/metrics.py:735-744`(`collect_window`:三輪仍不同時回 `Report(previous, False, MAX_ROUNDS)`,docstring 明寫「仍不同回最後一輪並標明不穩定」)

觸發情境:燒損評估器呼叫 `end_to_end_handoff(since, until, sources)` 時,執行端與分析端兩個資料庫在連續三輪重開快照期間有並發寫入,導致三輪的接續鏈與生命週期事件讀出來的結果三次都不一樣(這正是增量 1 的 `build_trace`、增量 2 的 `collect_window` 都要「讀到兩次相同為止」的同一種併發風險,兩者也都對此明確回傳 `stable`/是否穩定的欄位)。

會出的錯:`end_to_end_handoff` 迴圈跑滿 `MAX_ROUNDS` 仍不同時,直接拿第三輪的結果當答案回傳給 `Tally`,`Tally`/`SloStatus` 完全沒有欄位標示這次計數其實不穩定。增量 3 的整個設計基礎是「服務水準指標一律建在事件型上,同一個已經過去的窗,之後再查答案不變」(計劃第 257 行的代使用者裁定),[S655] 也是照這個前提把端到端交給執行定義成事件型指標。一旦三輪讀到的結果彼此不同,就是在事實上證明「這個窗再查會變」,跟事件型的核心假設矛盾;現在的做法是把這個矛盾證據悄悄丟掉,讓燒損評估器把一個承認自己會變的數字當成穩定證據去判快燒/慢燒告警,審計時也查不出這個窗的計數曾經不收斂。

建議修法:比照 `src/rtb/ops/trace.py:427-434`、`src/rtb/ops/metrics.py:735-744` 既有的做法,在 `end_to_end_handoff` 的回傳(或 `Tally`)上加一個穩定與否的欄位,三輪仍不同時明確標記,並讓 `SloStatus` 對外揭露(至少像 `unlinked`/`clock_anomaly` 一樣另報份數),而不是靜默採用最後一輪。

### 2. 目標為零指標:週期內某一子窗 DSP 讀不到時,已經確定發生的違規會被清成空值
severity: major
blocking: 是
引句:「(period.bad > 0 if not period.missing else None) if spec.zero_target else None,」
引句:「不當成 0 個事件(當成 0 會把看不到說成沒違規)」
file: `src/rtb/ops/slo.py:146`(`period_tally`:`any(p.missing for p in parts)`)
file: `src/rtb/ops/slo.py:209`(`evaluate`:`violating` 欄位判斷)

觸發情境:30 天週期(示範縮短後 12 小時)被 `period_tally` 切成多個不超過 24 小時的子窗,逐段呼叫 `side_effects.unauthorized`/`duplicates`。若週期內某一個子窗因 DSP 唯讀端點暫時讀不到而回 `Tally(0, 0, missing=True)`,但週期內「另一個」已成功讀到的子窗確實抓到至少一筆真正的未授權或重複寫入(即 `period.bad` 由其他子窗加總得出、已經大於 0)。

會出的錯:`period_tally`(`slo.py:146`)只要任一子窗 `missing` 就把整個週期的 `missing` 設為真;`evaluate`(`slo.py:209`)接著用 `period.bad > 0 if not period.missing else None` 判斷 `violating`,只要週期 `missing` 為真就整條指標直接回 `None`,完全不看 `period.bad` 是否已經確定大於 0。結果是:一次跟該違規毫無關係的 DSP 短暫故障,就能把「已經抓到、目標為零的護欄違規」從「違規」壓成「不知道」。這正好是「實作時的解讀」自己想避免的效果的反面版本——文件裡說「當成 0 會把看不到說成沒違規」,但這裡的行為是把已經查到的「有」也蓋成「不知道」,直接牴觸 [S661]「目標為零的兩条,週期內出現一個壞事件就應是違規,直到它滑出週期」的字面要求(該壞事件明明還沒滑出週期,只是週期裡另一段暫時讀不到)。

建議修法:`violating` 的判斷順序改成先看 `period.bad > 0`(用已經讀到、確定成立的子窗下結論),只有在 `bad` 仍是 0 且有子窗 `missing` 時才回 `None`;把「已確定有違規」與「完全不知道有沒有」分開,不要讓後者蓋掉前者。

---

其餘經逐條核對均與規格相符,不列入違規:
- DSP 列操作一頁 50 筆(`src/rtb/ops/side_effects.py`、`src/rtb/dsp/store.py` 的 `OPERATION_PAGE = 50`)符合 [S665]「單次最多 5000 筆」的「最多」語意,且共用用戶端 64 KB 上限(`src/rtb/httpclient.py:21` `MAX_RESPONSE_BYTES`)確有其事,不是杜撰理由。
- 核可使用表不加依鍵索引不牴觸任何 [S65x] 條款,批量讀取(`approval_uses_for`)仍走 IN 子句分批查,未逐筆查。
- 目標為零兩条記目標為 `Fraction(1)`、錯誤預算固定為 0、`evaluate()` 對 `zero_target` 指標完全跳過快慢燒計算,均與 [S656][S661] 一致。
- `_reconciled()` 對「剛好在期限那一刻轉人工」判壞、「剛好在期限那一刻結案」判好的邊界寫法與 [S654] 一致(該條款本身未規範轉人工的邊界,屬合理的實作解讀範圍)。
- `_limit_violations()` 對加的量為 0 時跳過比例與總曝險核對、暫停額外跳過單一廣告上限,但授權範圍與政策版本照樣核對,與 [S652] 逐項比對後完全一致。
- `burn_rate`/`error_budget`/`remaining_budget`/`Burn.threshold` 全部用 `fractions.Fraction` 運算,無浮點介入,`>=` 門檻比較在剛好等於門檻時正確判定告警,與 [S656][S657] 一致。
