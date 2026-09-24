severity: major

固定附加輸入量只留備註，預留仍可低估並穿過展示上限
severity: major
blocking: 是
引句:「return {"fixed_input_tokens_seen": fixed, "bad_argument_exit_code": code,」
file: `src/rtb/modelverify.py:139`
file: `src/rtb/modelclient.py:98`
file: `src/rtb/modelclient.py:507`
重現: 在 `/tmp/codex-p11b-v` 用假驗證器回報 `fixed_input_tokens_seen=500000`，所有必要檢查仍判定通過並成功寫出啟用紀錄；再用假後端回傳 500000 個輸入 token，呼叫僅預留 0.0192216 美元便獲准送出，結算後展示已用變成 1.2 美元，超過 1 美元上限。程式只事後標 `overrun`，不可逆的呼叫已經發生。
建議: 把量得的固定輸入 token 納入啟用條件；數值缺失或大於 `CLAUDE_FIXED_INPUT_TOKENS` 時不得寫啟用紀錄。預留應使用經驗證值或更高的保守上界，並補一條「實測固定輸入超過常數時即時模式不開」的測試。

記憶隔離檢查只搜尋檔名，任意被注入的內容仍會判通過
severity: major
blocking: 是
引句:「memory = any(marker in text for marker in ("CLAUDE.md", "MEMORY.md"))」
file: `src/rtb/modelverify.py:115`
重現: 在 `/tmp/codex-p11b-v` 讓假 Claude 的 stream-json `init` 事件含 `injected_context=PRIVATE_CANARY_7f1a`，但不含字面值 `CLAUDE.md` 或 `MEMORY.md`；`Checker.hooks_and_memory()` 回傳 `(True, True)`，把明確存在的額外私人內容判成「沒有記憶」。這可讓未證實隔離有效的即時模式開啟，使白名單外內容隨請求送出。
建議: 在隔離 HOME 的 CLAUDE.md、memory 及各已知記憶來源植入不可混淆的 canary，要求模型回報或由可觀測的實際送出內容證明 canary 不存在；無法觀測送出內容時應 fail-closed，不得以輸出是否出現檔名代替。

設定來源對照使用會被命令列覆寫的 model，隔離失效仍可判通過
severity: major
blocking: 是
引句:「{".claude/settings.json": POISON_SETTINGS})」
file: `src/rtb/modelverify.py:131`
file: `src/rtb/modelclient.py:755`
重現: 在 `/tmp/codex-p11b-v` 用假 CLI 模擬「讀到 settings 內不存在的 poison model，但命令列 `--model claude-sonnet-5` 優先」；實驗同時觀察到 poison 值與固定命令列 model，`settings_suppressed()` 仍回傳 `True`。因此這個對照不能證明設定檔未載入，其他 env、hook 或外掛設定仍可能生效，卻會產生可開啟即時模式的紀錄。
建議: poison 應改用不會被現有命令列旗標覆寫、且一旦載入必定可觀測失敗的設定；最好加入獨特 env/hook canary 並驗證完全沒有事件或痕跡。另補「設定確實被讀取、但 model 被 CLI 覆寫」的假 Claude 反例測試。

3 條,blocking 3。