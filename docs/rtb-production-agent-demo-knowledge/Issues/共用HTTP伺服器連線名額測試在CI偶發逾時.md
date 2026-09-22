---
type: issue
status: open
created: 2026-09-22
updated: 2026-09-22
aliases: []
about_code: []
tags:
  - type/issue
  - status/open
summary: |-
  FLAG: TECHNICAL 測試靠固定等待時間假設伺服器已接住前一條連線,CI 機器慢時假設不成立。
  PITFALL: 2026-09-22 推送嘗試紀錄增量後,CI 第一次跑在 tests/kit/test_httpkit.py 的連線名額測試逾時紅燈(747 綠 1 紅),重跑同一版本轉綠,本機連跑 5 次全綠。重現指令:.venv/bin/python -m pytest tests/kit/test_httpkit.py -q -k over_limit_sockets
REVISIT:2026-10-15 把這支測試的固定等待改成等伺服器真的佔住名額,並在 CI 連跑觀察是否仍偶發。
---
# 共用HTTP伺服器連線名額測試在CI偶發逾時

關聯:[[Systems/共用行程基礎]]。

## 症狀

`test_connection_slots_come_back_after_a_handler_error_and_over_limit_sockets_are_shut` 在 CI 上偶發失敗:超過上限的那條連線沒有被伺服器立刻關閉,`over.recv(10)` 等了 2 秒逾時。2026-09-22 那次 CI(提交 c5b1102)第一次紅、重跑綠;本機連跑 5 次都綠。這次推送沒有改到共用 HTTP 伺服器。

## 根因(推測,尚未證實)

推測:測試先開一條連線佔住名額,然後只 `time.sleep(0.2)` 就假設伺服器已經接住它、名額已被佔用,再開第二條期待被拒。CI 機器慢時,伺服器可能還沒接住第一條,第二條就被當成名額內的正常連線收下,於是不會被關閉。沒有在 CI 上加記錄證實,所以這是推測。

## 現在怎麼繞

CI 紅在這支測試時重跑一次;重跑仍紅就當真的壞了處理,不能一直重跑。

## 什麼條件算修好

測試改成「等到伺服器確實佔住名額」再開第二條連線(例如輪詢伺服器目前的連線數),不再依賴固定等待;改完在 CI 連續多次推送都沒有再出現這支測試的逾時。

