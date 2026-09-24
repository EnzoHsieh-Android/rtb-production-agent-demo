severity: major

主行程退出後，停止函式會留下同群組的孫行程
severity: major
blocking: 是
引句:「        if self._popen.poll() is None:」
file: `src/rtb/demo/launcher/__init__.py:120`
重現：唯讀追讀 `Process.stop()`：主行程若已退出，`poll()` 非空，整段 `killpg` 會跳過；即使主行程仍在，收到 SIGTERM 後先退出，也會讓 `wait()` 立即返回，沒有確認孫行程是否仍活著。兩條路徑都可能留下同群組的行程。未在唯讀 repo 起行程。
建議：清理時依行程群組是否仍有成員決定送訊號；寬限期後仍有成員就送 SIGKILL，測試涵蓋主行程先退出及孫行程不理 SIGTERM。

故障子行程可用等號形式的參數繞過目標路徑核對
severity: major
blocking: 是
引句:「    return [Path(args[i + 1]) for i, arg in enumerate(args[:-1]) if arg in _PATH_OPTIONS]」
file: `src/rtb/demo/launcher/child.py:19`
重現：唯讀對照路徑擷取與正式參數解析。`_targets()` 只擷取 `--db <路徑>`；`argparse` 的 `--db=<路徑>` 形式也會被執行端接受，卻不會交給 `load_verified()` 核對。因此帶有效故障交付的子行程可用 `--db=/tmp/根目錄外的資料庫` 啟動；重複給參數時，後一個等號形式的值也能蓋過已核對的值。執行端接受該選項見 `src/rtb/executor/runner.py:50`。未在唯讀 repo 起行程。
建議：用與正式入口相同的解析結果核對所有目標路徑，並拒絕重複的路徑選項；補 `--db=`、`--tenant-config=` 測試。

流程圖把已驗證完成誤標為正在寫入
severity: major
blocking: 是
引句:「        "HANDED_OFF": _at("x_write", "已交給寫入這一步"),」
file: `src/rtb/demo/flow.py:235`
重現：唯讀追讀收件狀態：`Disposition.HANDED_OFF` 表示該鍵的嘗試已驗證，見 `src/rtb/executor/inbox_store.py:80`；確認時才同時寫入該處置與 `LifecycleKind.HANDED_OFF`，見 `src/rtb/executor/inbox_store.py:1313`。流程圖卻把兩者都指向「寫入廣告平台」，後者見 `src/rtb/demo/flow.py:268`。展示讀到完成事件時會顯示較早的階段。未在唯讀 repo 執行展示。
建議：將這兩個結果對到完成節點，另以真正開始寫入的事件標示寫入階段；用已驗證的收件紀錄測試顯示位置。

3 條，blocking 3。