# 稽核結果

## 一、這是不是一條真合約

大致是,但有兩個詞是活口。

合約句子可拆成六個子句,其中四個(副作用+持久結果已完成、確認前當機、由持久狀態/冪等紀錄判定、不產生第二次副作用)都能對應到程式裡具體、可機械檢查的狀態(`AttemptState.COMMITTED_UNVERIFIED`、`disposition=in_progress`、DSP 側計次、`budget` 只變一次),不算空話。

但兩個詞鬆動:

- **「重新投遞」**:程式碼自己把「投遞」(`deliveries` 欄位,只在 `InboxStore.receive()` 裡加一)跟「對帳接手」(`take_over`/`in_progress_for`,不碰 `deliveries`)分成兩條不同路徑。合約用「重新投遞」這個詞,但沒有講清楚是要哪一條路徑——這個模糊在下面第二點會看到被怎麼利用。
- **「安全」送出確認**:合約沒有自己定義「安全」是什麼,程式碼裡「安全」其實是指「確認寫入要帶收據、收據要對得上目前的租約」(`_held()` 的 `lease_seq`/`lease_owner`/`lease_until` 條件)。合約句子本身沒把這個定義釘進去,所以任何「六個子句表面狀態都對,但確認寫入不驗收據」的實作,也能讓合約文字讀起來像是成立的。

## 二、六支測試逐句對照

| 子句 | 誰守 | 備註 |
|---|---|---|
| 副作用+持久結果都已完成 | `test_crash_before_verification_is_recovered_by_reconciliation`、`test_a_crash_before_the_terminal_commit_...`(死點斷言 `attempt=committed_unverified`) | 這兩支是唯一在死點就已經是 `committed_unverified` 的;其餘四支死在更早(`in_flight`)或更晚(`verified`),不是這個子句要的精確狀態 |
| 確認前當機 | 同上兩支(`disposition=in_progress` 於死點) | `test_a_crash_after_the_terminal_commit_...` 死點是 `disposition=handed_off`,是這子句的反例邊界,不是它本身 |
| 重新投遞後 | **沒有一支守**。六支全靠 `restart()` 觸發的對帳/租約接手(`take_over`),不是走 `receive()` 的待處理佇列重投遞路徑。`assert_recovered` 明文釘死 `deliveries=1`(`world.proposals() == [("t1", 1, "handed_off", None, 1)]`),等於六支自己證明了「過程中沒有發生一次計數意義下的重新投遞」。真正走 `receive()` 重投遞的是 `test_f3_a_message_delivered_twice_applies_once`(`deliveries=2`),但它不在被宣稱的六支之列 |
| 由持久狀態與冪等紀錄判定已完成 | `test_crash_after_the_dsp_commit_...`(`world.counts()["write"] == 1` 用冪等鍵查到操作紀錄)、`assert_recovered` 的 `world.dsp_keys() == ([key], [])` | 有守,但機制混合:有時是本地 `committed_unverified` 列本身(不查 DSP 冪等紀錄),有時才真的查 `operation_record` |
| 不產生第二次副作用 | 六支全部經 `assert_recovered` 的 `world.counts()["write"] == 1`、`world.counts()["void"] == 0`、`world.budget() == (150, 1)` | 守得最紮實,而且計數點刻意放在請求處理器(見測試檔開頭註解),不會被 DSP 端的冪等短路漏數,值得肯定 |
| 最後安全送出確認 | 六支全部經 `assert_recovered` 的 `world.proposals()[...] == "handed_off"` | 只驗「最後有確認」,**沒有驗「確認是有條件寫入,不能在租約被別人接手後還硬確認」**——這個「安全」的真正定義從未在六支裡被觸發過一次(見下段實驗) |

小結:六支測試合起來只把「副作用+持久結果已完成、確認前當機」這個精確狀態守住兩次(其餘四支是相鄰但不同的死點),「重新投遞」這個子句字面上沒人守,「安全確認」的安全機制也沒人守。

## 三、測試綠但合約不成立的實測

把 `src` 整份複製到暫存目錄,對 `inbox_store.py` 的 `_held()` 動一刀:拿掉 `lease_seq`/`lease_owner`/`lease_until` 條件,只留 `task_id`/`revision`/`content_hash`/`disposition`——等於讓「延長租約、確認、寫入」全部變成不驗收據的無條件寫入。

```python
# 原本
"AND content_hash = ? AND {IN_PROGRESS} AND lease_seq = ? AND lease_owner = ? AND lease_until > ?"
# 改壞後
"AND content_hash = ? AND {IN_PROGRESS}"
```

結果:

- `tests/executor/test_crash_recovery.py` 全部 9 支(含被宣稱轉正的 6 支)**照樣全綠**。
- 跑整個 `tests/executor` 套件,`test_execution.py`、`test_queue.py` 裡 5 支測試立刻報錯,包括 `test_a_lost_lease_skips_the_key_without_halting`(租約被接手後還硬確認,斷言直接翻紅)。

這證明:合約講的「最後**安全**送出確認」裡「安全」的具體含義(確認寫入必須綁定仍然有效的租約,不能在被別的工作者接手後還盲目確認),六支被提名的測試完全沒有觸碰到——因為它們全是「一個子行程死透、下一個子行程才啟動」的嚴格序列,租約永遠沒被搶過(這點測試檔自己的開頭註解也承認:「單一執行者鎖從沒被搶過⋯⋯這兩項由 test_runner.py 的鎖測試與每輪先對帳的測試守」)。也就是說,「安全確認」這個子句能成立,靠的其實是 `test_queue.py`(`test_a_lost_lease_skips_the_key_without_halting`、`test_reconciliation_only_takes_over_an_expired_or_own_lease`、`test_two_receipts_for_the_same_key_still_write_the_dsp_once` 等)與 `test_runner.py` 的鎖測試,而不是被提名的六支。

## 結論: 不同意轉正

缺兩塊,而且測試作者自己在檔頭已經承認了範圍限制:

1. 「重新投遞」子句沒有測試守——六支全走對帳/租約接手路徑,`deliveries` 全程鎖死在 1,不是合約字面要求的重投遞路徑(那條路徑的測試是 `test_f3_a_message_delivered_twice_applies_once`,不在提名名單裡)。
2. 「最後安全送出確認」的「安全」沒有被六支觸發過——實測把確認寫入的租約條件整段拔掉,六支照樣全綠,要靠 `test_queue.py`/`test_runner.py` 裡另外幾支才會翻紅。

若要轉正,合約文字本身也建議把「重新投遞」改成明確指名的機制(例如「租約到期後由對帳或佇列任一路徑接手」),並把提名的測試清單補上 `test_queue.py::test_a_lost_lease_skips_the_key_without_halting`、`test_reconciliation_only_takes_over_an_expired_or_own_lease`、`test_two_receipts_for_the_same_key_still_write_the_dsp_once`、`test_f3_a_message_delivered_twice_applies_once`(或等價的重投遞測試)一起作為證據,否則「安全」與「重新投遞」這兩個詞就是允許任何實作矇混過關的空隙。
