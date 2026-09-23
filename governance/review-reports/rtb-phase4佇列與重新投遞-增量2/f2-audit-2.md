# 事故 F2 預告合約轉正審計報告

## 背景與方法

合約原文出處是交接文件第 16 節事故 F2,佇列設計出處是 `RTB_Phase4佇列與重新投遞_計劃.md` 增量 1(租約/確認規則)與增量 2(重啟恢復與 F2/F3 端到端),上游裁定在 `RTB_Agent_Phase0架構.md` 的 Queue/lease 段落。我讀了 `src/rtb/executor/{execution,inbox_store,attempt_store,runner}.py` 與 `tests/executor/{test_crash_recovery,test_queue}.py` 全文,並把被測程式複製到臨時目錄,對 `execution.py` 的關鍵路徑做了兩輪真的變異測試,只跑宣稱的那 11 支測試(test_queue.py 4 支中選的那 4 支 + test_crash_recovery.py 7 支)。

## 1. 是不是一條真合約

是。句子裡每個詞在這個系統裡都有具體、可讀到的機制,不是我用常識腦補的:

| 合約用詞 | 對應機制(出處) |
|---|---|
| 副作用已完成 | DSP 寫入成功,回應對照表 `RESPONSE_TABLE` 判成 `committed`(`execution.py:158-165`),本地寫成 `committed_unverified` |
| 持久結果已完成 | 依當機點不同,指「本地嘗試紀錄的 `committed_unverified` 列已提交」(`attempt_store.py` 的 `attempts` 表) |
| 送出確認前當機 | 收件表的 `disposition` 欄位仍是 `in_progress`(`inbox_store.py` 的 `Disposition.IN_PROGRESS`),確認(`ack_handed_off`/`ack_blocked`)還沒寫 |
| 重新投遞 | 收件表的租約機制(`lease_until`/`lease_seq`/`lease_owner`),到期後可被 `receive()` 或對帳的 `take_over()` 接手 |
| 新的工作者 | 真的另一個 OS 行程(`runner.owner` 用行程編號加啟動時間,兩個子行程確實是不同擁有者) |
| 由持久狀態與冪等紀錄判定已完成 | 本地 `attempts` 表的最新列狀態(`attempt_store.latest`)+ DSP 的冪等鍵查詢(`dsp.operation_record`) |
| 不產生第二次副作用 | 用 DSP 伺服器的請求計數器(`CountingHandler`,按路由分類,不是套用計數)量測 |
| 安全送出確認 | 確認一律是帶收據的條件寫入(`_held()`,條件含處置、租約序號、擁有者、到期時間) |

**一個值得記錄的精確度落差**:F2 文字用「重新投遞」,但實際上「副作用+持久結果都完成、確認前當機」這個精確狀態(`committed_unverified`)在重啟後,並不是走佇列的 `receive()`(取件/派工路徑),而是走**對帳的租約接手**(`reconcile_all()` → `_take_over()`)。這兩條路徑共用同一把租約欄位(Phase 0 明訂「這把 lease 同時管訊息與嘗試中紀錄」),語意等價,但程式碼路徑不同——`receive()` 遇到已有嘗試紀錄的鍵只會「跳過或放掉」,真正推進到確認的是對帳。設計文件自己在使用者情境題裡把這個落差講得很清楚(增量 2:「c 取件跳過它,由對帳接手」),不是隱藏的問題,但代表合約裡「重新投遞」是個較寬鬆的傘狀說法,不是字面上「佇列把訊息再交出去一次」。

## 2. 這組測試合起來夠不夠格

逐子句對應如下(檔案都在 `tests/executor/`):

| 子句 | 主要守護測試與斷言 |
|---|---|
| 副作用+持久結果都完成、確認前當機 | 最貼切的是 **test_crash_before_verification_is_recovered_by_reconciliation**(`test_crash_recovery.py:309`)與 **test_a_crash_before_the_terminal_commit_...**(`:323`):死點斷言 `attempt=committed_unverified disposition=in_progress`,精確卡在「本地結果已提交、確認未送」那一格。`test_crash_after_the_dsp_commit_...`(:295)只驗到「副作用完成、本地結果還沒寫」,是相鄰但較早的當機點,不是字面上這句話的精確實例,屬於同一批當機測試的互補覆蓋 |
| 重新投遞 | 字面上的 `receive()` 重投遞由 **test_f3_a_message_delivered_twice_applies_once**(:353,標註 F3)驗到(投遞次數變 2);F2 那四個當機點驗的是語意等價的對帳接手 |
| 由持久狀態與冪等紀錄判定已完成 | **test_crash_after_the_dsp_commit_...** 查 DSP 冪等鍵表(`operation_record`);**test_crash_before_verification_...**/**test_a_crash_before_the_terminal_commit_...** 查本地 `committed_unverified` + 讀 DSP 現況比對(`_check_applied`/`intent_holds`)。兩種判定機制分別被覆蓋,沒有單一測試同時逼兩者都跑到,但因為它們是互斥分流(依當機點走不同分支),這樣分開驗是合理的 |
| 不產生第二次副作用 | 每支「恢復完成」測試都經 `assert_recovered()`(:246)斷言 `world.counts()["write"] == 1`——這是**請求層級**計數(掛在 `CountingHandler`,不是 DSP 套用層級),比只驗「預算只改一次」更嚴格,連「送了但被 DSP 冪等擋下」的浪費請求都抓得到 |
| 最後安全送出確認 | `assert_recovered()` 驗最終 `disposition == "handed_off"`;**test_a_stale_or_expired_receipt_can_write_nothing**(`test_queue.py:264`)直接證明確認寫入受收據條件保護(擁有者不對、租約過期、序號被換過都寫不進去);**test_two_receipts_for_the_same_key_still_write_the_dsp_once**(:681)證明兩張同時有效收據競爭時只有一個能真正送出/確認 |
| 收據對不上是放棄不是停機 | **test_a_lost_lease_skips_the_key_without_halting**(:581)、**test_reconciliation_only_takes_over_an_expired_or_own_lease**(:473) |

沒有子句完全沒人守。比較明顯的缺口是**範圍邊界**,不是斷言缺口:這組測試裡「兩個工作者」全部是**先後**跑的真行程(子行程接力,見 `test_crash_recovery.py:1-14` 的說明),`test_two_receipts_...` 與 `test_a_lost_lease_...` 的「並行」是同一行程內用回呼函式模擬交錯,不是兩個真正同時存活的 OS 行程互搶。真正的多工作者並行當機留給增量 3,設計文件自己承認這一點(增量 2「不做的事」)。

## 3. 有沒有測試綠但合約不成立的情況

我做了兩輪變異測試,鎖定合約最核心的兩個子句(「由持久狀態判定已完成」與「不產生第二次副作用」),只跑這 11 支被列為證據的測試:

**變異 1**:讓對帳在「結果不明」狀態下完全不查 DSP 冪等紀錄,一律當成查不到就重送(`_reconcile_unknown` 略過 `operation_record` 的判斷)。結果:13 支中 1 支(`test_crash_after_the_dsp_commit_is_recovered_from_the_operation_record`)變紅,但紅的原因是意外多了一次 `void` 呼叫,不是直接的「多送一次寫入」——顯示系統有多層防線(版本比對、作廢再判失敗)會把這類錯誤攔成「浪費一次安全動作」而非「真的兩次副作用」。同時也發現:F2 精確對應的兩支測試(`before_terminal_commit`、`before_verification`)完全沒被這個變異影響,因為它們的當機狀態(`committed_unverified`)根本不會經過我改壞的那個函式。

**變異 2**(更貼近 F2 精確子句):把「已提交待驗證」狀態的重啟恢復路徑改成「不管持久狀態,直接重簽重送一次」,模擬使用者情境題裡答錯的選項 b(「訊息又來了就當成還沒做」)。結果:13 支中恰好 **2 支變紅,而且正是設計文件自己指名「這就是事故 F2 的形狀」的那兩支**——`test_crash_before_verification_is_recovered_by_reconciliation` 與 `test_a_crash_before_the_terminal_commit_rolls_back_the_result_and_the_acknowledgement`,斷言失敗訊息是乾淨的 `assert 2 == 1`(write 計數),不是靠意外的例外或狀態機崩潰帶紅——就是設計要驗的那條斷言真的抓到了。

兩輪變異都沒能做出「測試全綠但合約已破」的案例;越貼近合約字面意思的破壞,越精準被對應的測試抓到。

## 結論

`結論: 同意轉正`

補充:轉正時建議把「重新投遞」在這份合約落地的說明帶一句——對 F2 的核心當機點(持久結果已寫、確認未送),恢復走的是對帳的租約接手,不是佇列 `receive()` 的直接重投遞;兩者同一把租約、同等安全性,但程式路徑不同,免得未來有人照著合約字面去找 `receive()` 而找錯地方。多工作者真並行當機仍是已知未覆蓋範圍(增量 3),但這是文件自己承認、也不在 F2 這句合約的字面範圍內,不影響本次轉正判斷。
