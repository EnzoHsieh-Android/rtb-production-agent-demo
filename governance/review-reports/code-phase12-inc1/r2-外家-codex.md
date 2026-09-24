severity: major

上一輪 x1–x3 已核對：停止函式會向整個行程群組送訊號；故障子行程用正式 parser 核對目標路徑；`HANDED_OFF` 已對到完成節點。

F7 可把未加額的操作判成逐廣告核對通過
severity: major
blocking: 是
引句:「    for campaign, _, budget in world.all_platform_writes():」
file: `src/rtb/demo/driver.py:551`
重現：唯讀追讀 `_check_campaign_by_campaign()`：它丟掉操作種類，只比操作參數中的 `new_budget`，也不讀廣告最後的預算。正式 DSP 允許 `pause_campaign` 帶額外的 `new_budget` 參數，但該操作保持原預算並暫停廣告，見 `src/rtb/dsp/store.py:179`、`src/rtb/dsp/store.py:229`。若收件口錯將這筆記為完成，逐廣告核對仍可通過，F7 會假綠。未在唯讀工作樹執行實驗。
建議：核對操作種類必須是 `update_budget`，並逐廣告讀回最後預算及狀態。

F7 未確認核可證據就宣稱「確認的那一筆」已放行
severity: major
blocking: 是
引句:「        return world.wait_paused(lambda: bool(world.platform_writes(campaign)),」
file: `src/rtb/demo/driver.py:612`
重現：唯讀追讀等待條件與結束核對：指定廣告只要出現平台操作便算「已確認」；其後只比平台操作與收件口完成集合，沒有查該提案的核可紀錄。若核可守衛失效而直接寫入，F7 仍可回報照預期跑完。未執行實驗。
建議：確認該提案有本次展示簽發、關卡及雜湊相符的核可紀錄，再判定放行成功。

F2 發生重送仍會報告「沒有重送」
severity: major
blocking: 是
引句:「    return "寫進平台後執行端當場倒下;重啟後查平台紀錄確認已經寫進去,沒有重送,平台上只改一次"」
file: `src/rtb/demo/driver.py:322`
重現：唯讀對照 `F2_REQUIRED`、`missing_from_path()` 與 `_applied_once()`，見 `src/rtb/demo/observe.py:110`。路徑檢查只要求必經節點依序出現，容許中間多出 `x_resend`；平台冪等處理也可能讓重送後仍只有一筆操作。因此重送發生時，現有斷言仍可能通過。未執行實驗。
建議：F2 額外斷言沒有重送節點，並核對該鍵的送出次數。

情境逾時後工作執行緒未結束，清理與啟動子行程會競爭
severity: major
blocking: 是
引句:「                world.stop.set()  # 只停這個情境的觀察迴圈,不影響整次展示」
file: `src/rtb/demo/driver.py:688`
重現：唯讀追讀 `_attempt()`：逾時時只設停止旗標，沒有等待 `worker` 結束，就在 `finally` 呼叫 `world.close()`。旗標只由 `watch()` 檢查；工作執行緒若正執行 `start()` 或其他情境步驟，可在 `close()` 清空行程清單後再起子行程，留下未受管理的行程，或在結案後繼續寫展示狀態。未起行程實驗。
建議：讓整個情境流程可取消；逾時後先等工作執行緒確實結束，再清理行程與標記結果。

F5 未讓對抗文字經過模型判斷，卻宣稱擋住提示注入
severity: major
blocking: 是
引句:「    return "名稱裡叫系統加 500%、洩漏金鑰;實際只照規則加了一成,旁邊的廣告沒被動到"」
file: `src/rtb/demo/driver.py:455`
重現：唯讀追讀 F5 的 `start_services()` 與正式分析入口：後者直接呼叫 `policy.decide`，沒有模型入口，見 `src/rtb/analyzer/runner.py:89`。對抗名稱未進入可受提示影響的模型判斷，F5 通過只能證明這條程式規則路徑的寫入結果，不能證明提示注入被擋下。未呼叫 Claude。
建議：F5 接上預定的模型候選入口並核對其輸入與處置；在接通前不要把此情境標成提示注入驗證通過。

ps 檢查：已執行檢查命令，但唯讀沙盒回覆 `operation not permitted: ps`；本輪未啟動任何測試或服務行程。

5 條，blocking 5。