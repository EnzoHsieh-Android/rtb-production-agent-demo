severity: major

第 2 輪 c1、c2、c4 的修正已對到原問題；c5 已明確限縮為規則路徑驗證。未發現 httpkit 使既有 JSON 伺服器回歸的問題。c3 的路徑核對仍有假綠；本輪新增的驗證器呼叫另有孤兒行程風險。本輪受唯讀沙盒限制，未複製或執行測試，以下重現均為程式碼與測試的唯讀推演。

額外重複的必經節點會被判成「照預期跑完」
severity: major
blocking: 是
引句:「    expected = set(required) | set(allowed)」
file: `src/rtb/demo/observe.py:197`
重現：依 `missing_from_path()` 的運算，把 F2 的 `x_unknown` 在必經序列中重複插入一次。必經節點仍可依序找到，而重複的節點也屬於 `expected` 集合，函式遂回傳 `None`。F2 的送出次數與平台單次寫入檢查不會查出這種額外步驟，因此路徑異常仍可能標成照預期。這未完成第 2 輪 c3 要求的「扣掉必經與允許回頭後，多出的節點算對不上」。
建議：核對時逐一消耗實際匹配的必經節點；對 F4、F5 等多工作情境，按任務或鍵核對各自路徑，明確列出允許重複的步驟。

展示驗證器逾時會留下仍在執行的 pytest 行程群組
severity: major
blocking: 是
引句:「        done = subprocess.run(list(command), cwd=PROJECT_ROOT, env=env, capture_output=True,」
file: `src/rtb/demo/driver.py:921`
重現：唯讀追讀逾時路徑：展示在 600 秒時由 `subprocess.run()` 結束驗證器行程；驗證器的證據測試上限卻是 900 秒，且以 `start_new_session=True` 啟動 pytest（`tools/verify_claims.py:52`、`tools/verify_claims.py:1198`）。外層只結束驗證器，未向 pytest 的獨立行程群組送訊號；若測試超過 600 秒，頁面已回報逾時，pytest 及其子行程仍可繼續執行。現有逾時測試只啟動單一睡眠行程（`tests/demo/test_driver.py:499`），驗不到這條路徑。
建議：展示端把驗證器及其後代納入可收回的行程群組，逾時時收乾淨；或讓內層測試期限先於外層期限，並處理外層被取消的情況。

ps 檢查：已執行檢查，沙盒回覆 `operation not permitted: ps`；本輪未啟動服務或測試行程。

2 條，blocking 2。