severity: major

**Finding 1:故障子行程的目標路徑核對可以繞過(`--db=路徑` 或縮寫選項),故障可以打在展示根目錄外的資料庫上**
severity: major
blocking: 是
引句:「return [Path(args[i + 1]) for i, arg in enumerate(args[:-1]) if arg in _PATH_OPTIONS]」
file: `src/rtb/demo/launcher/child.py:20`
- **問題**:`_targets` 只認「選項、值」分成兩個參數的寫法。argparse 另外兩種寫法照樣收,核對卻看不到:
  - `--db=/x`、`--tenant-config=/y`
  - 縮寫選項,例如執行迴圈的 `--tenant /y`(`allow_abbrev` 預設開著)
- **後果**:帶猝死點與時鐘偏移的故障執行迴圈、或帶故障排程的 DSP,會對根目錄外的資料庫與租戶設定正常啟動,違反 S1049 與計劃第 72 行「所有目標路徑必須落在那個根目錄內」。這正是「讓正式資料被故障吃到」。
- **這是忘記,不是刻意繞過**:`--db=...` 是很常見的寫法,呼叫端寫錯一次就中。

重現(在 /tmp 複本,腳本在 `/tmp/p12i1-sec-opus/exp_db.py` 與 `exp_exec.py`):用 `prepare_root` 建根目錄,`write_fault_plan` 排故障,再直接跑 `python -m rtb.demo.launcher.child`。
- DSP 帶 `hang` 故障:
  - `--db /outside/a.db` → 代碼 3,拒絕(符合預期)
  - `--db=/outside/b.db` → 印出 `PORT=61052` 並正常服務,`outside/` 裡出現 `b.db`
- 執行迴圈帶 `crash_point=after_receiving` 與 3600 秒時鐘偏移:
  - `--tenant-config OUT/tenants.json` → 代碼 3
  - `--tenant OUT/tenants.json` → `READY`
  - `--db=OUT/e.db --tenant-config=OUT/tenants.json` → `READY`,根目錄外出現 `e.db`、`e.db-wal`、`e.db-shm`

建議:不要自己比對 argv 字串。
- 在 `dsp_faults.serve` 或執行迴圈端,用正式入口同一支 parser 解析後的 `args.db`、`args.tenant_config` 核對;
- 或在 child 先用同一套 parser 做 `parse_known_args`;
- 或把 parser 設成 `allow_abbrev=False`,並拒絕任何含 `=` 的路徑選項。
- 測試的 "db outside root" 參數化要補 `--db=` 與縮寫兩種寫法。

**Finding 2:匯入邊界有間接路徑,正式層經最上層共用模組可以拿到 rtb.demo,ruff 與邊界測試都沒擋**
severity: major
blocking: 是
引句:「elif isinstance(node, ast.ImportFrom) and node.module:」
file: `src/rtb/sqlitekit.py:1`(最上層沒有 ruff.toml 禁令)
- **ruff 的缺口**:`rtb.demo` 禁令只寫在六個層各自的 ruff.toml。`src/rtb/` 最上層的 capabilitykit、httpkit、httpclient、sqlitekit 吃的是 pyproject,沒有禁令;而分析端、執行端、DSP 全都匯入它們。
- **邊界測試的缺口**:`_imports` 看不出同層相對匯入:
  - `from . import demo`:module 是 None,整句被跳過
  - `from .demo import keys`:得到的名字是 `demo.keys`,不以 `rtb.demo` 開頭
- **後果**:S1002「正式程式不匯入展示套件」的機械守衛,在「忘記」的情境下是綠的。

重現(在複本):在 `src/rtb/sqlitekit.py` 的匯入區加一行 `from . import demo  # noqa: F401`。
- `ruff check src/rtb/sqlitekit.py` → All checks passed
- `pytest tests/demo/test_boundaries.py` → 14 passed
- `python -c "import rtb.analyzer.runner, rtb.executor.runner, sys; print([m for m in sys.modules if m.startswith('rtb.demo')])"` → `['rtb.demo']`
- 跑完已用 `git -C` 還原複本。

建議:
- 在 `src/rtb/` 最上層也要禁 `rtb.demo`(例如 pyproject 的 banned-api 放 `rtb.demo`,demo 目錄自己的 ruff.toml 覆蓋掉)。
- `_imports` 依 `node.level` 與檔案所在套件,把相對匯入還原成絕對名稱再比對。
- 或改成實測:子行程匯入各正式入口後檢查 `sys.modules` 裡沒有 `rtb.demo`,這樣間接路徑一次涵蓋。

**Finding 3:子行程的 cwd 設成展示根目錄,`python -m` 會把它放在 sys.path 第一位,根目錄裡的 .py 能蓋掉標準函式庫(正式入口也一樣)**
severity: minor
blocking: 否
引句:「popen = subprocess.Popen(command, env=env, cwd=root, stdout=subprocess.PIPE, stderr=log,」
- **問題**:`-m` 會把 cwd 插在 sys.path[0],排在 PYTHONPATH 與標準函式庫之前。結果是**沒排故障的正式子行程**,也會執行根目錄裡叫 `argparse.py`、`json.py` 之類的檔案。
- **現在的條件**:根目錄是 0700,所以目前只有同一個使用者能放檔案,算在計劃自己宣告的天花板內。
- **會變嚴重的情況**:日後 `prepare_root` 的 base 是別人可寫的共用固定目錄(例如 `/tmp/rtb-demo`,父目錄擁有者可以把整個根目錄換掉)。這時就變成跨使用者執行程式碼。`mode=0o700` 只套在最末層,base 由呼叫端決定,目前沒有任何核對。

重現:在根目錄放一個 `argparse.py`(寫 stderr 後結束),用 `env -i PATH=/usr/bin PYTHONPATH=<src>` 在根目錄跑 `python -m rtb.executor.inbox_server --db x.db`,輸出 `SHADOW argparse from root`,正式入口沒有啟動。

建議:
- 指令加 `-P`(不加 cwd 到 sys.path,不影響 S1003 的環境白名單);
- 或 cwd 改成專案 src 以外、固定不可寫的目錄。
- `prepare_root` 核對 base 的擁有者是自己、而且不是別人可寫的。

**Finding 4:「一次性」隨機值其實不是一次性:核對後留在子行程環境裡,設定檔也不作廢,可以重放**
severity: minor
blocking: 否
引句:「env[FAULT_NONCE_ENV] = fault_nonce」
- **問題**:`load_verified` 核對完沒刪設定檔、沒標記已用,子行程也沒把 `RTB_DEMO_FAULT_NONCE` 從 `os.environ` 拿掉。結果:
  - 故障 DSP 或執行迴圈整段生命都帶著這個值;
  - 同一份設定檔配同一個值可以重複起故障子行程。
- **跟合約的出入**:S1049、計劃第 72 行寫的是「一次性隨機值」,這個性質目前沒有保證。

重現:同一組 `cfg` 和 `nonce` 連跑兩次,兩次都印 `PORT=`。`ps eww -p <pid>` 看得到 `RTB_DEMO_FAULT_NONCE=<值>`,`cfg.exists()` 仍是 True。

建議:核對成功後在 child 裡:
- `os.environ.pop(FAULT_NONCE_ENV)`;
- 以 rename 或 unlink 讓設定檔作廢(第二次讀不到就拒絕);
- 補一條「同一份交付第二次啟動被拒」的測試。

**Finding 5:設定檔內容異常時以代碼 1 加 traceback 結束,不是合約說的一律代碼 3**
severity: minor
blocking: 否
引句:「if not hmac.compare_digest(marker, str(body.get("demo_id", ""))):」
- **問題**:遇到以下內容時,丟出的不是 FaultRefused:
  - 字串含非 ASCII:`hmac.compare_digest` 丟 TypeError
  - `dsp_plan` 型別或數值不對:`float()` 或迭代丟例外
- **後果**:這些例外都沒被接住,子行程以代碼 1 結束。沒有退回正常啟動,但跟 child.py 開頭與家筆記寫的「核對不過一律以結束代碼 3 結束」不符,啟動器也分不出是拒絕還是崩潰。

重現:竄改設定檔後啟動,四種都得到代碼 1:
- `nonce_sha256="é"` → TypeError: comparing strings with non-ASCII…
- `demo_id="展示"` → 同上
- `dsp_plan=[["hang","abc"]]` → ValueError
- `dsp_plan=5` → TypeError

建議:
- 比對前先 `.encode()` 成位元組再 compare_digest;
- `load_verified` 解析欄位的地方把 `(TypeError, ValueError)` 轉成 FaultRefused。

**Finding 6:S1001 啟動守衛放行 NaN 與負值逾時**
severity: minor
blocking: 否
引句:「if CALLS_PER_STEP * args.timeout_seconds * 2 >= LEASE_DURATION.total_seconds():」
- **問題**:NaN 任何比較都是 False,負值乘出來一定小於租約,兩者都會通過守衛並印 READY。
- **目前影響**:之後每次呼叫在 settimeout 就丟 ValueError,不會重複花錢,只是守衛把明顯無效的設定當成安全。

重現:`run([... '--timeout-seconds','nan'], max_rounds=1)` 印 READY、回傳 0;`'-1'` 結果相同。

建議:條件改成 `not (0 < t and math.isfinite(t) and CALLS_PER_STEP*t*2 < lease)` 才放行。

**查過、沒發現問題的部分**
- **金鑰**:`secrets.token_urlsafe(32)`;repr 已遮蔽;角色對金鑰的對應跟正式入口實際讀的一致(DSP 讀能力與稽核,執行迴圈讀能力與核可,收件口、分析端不拿);stderr 與日誌路徑沒看到金鑰。
- **設定檔**:以 `O_EXCL`、0600 寫入。
- **路徑判定**:設定檔用符號連結指到根目錄外、`root` 欄位填 `/`,都會被 resolve 後的核對擋下。
- **子行程控制**:行程群組停止、`StartFailed` 時的收尾都正常。

**清理**:實驗目錄已刪,我起的行程都已收乾淨。
- ps 裡看到 `/tmp/audit-h-copy*` 的 DSP 和 `/tmp/m3t` 的變異測試,不是我起的。
- 另有一支 pytest-454 的故障子行程(pid 97924)還掛著,也不是我起的,但它可能代表 `test_launcher` 的測試會漏收子行程,請相關席位確認。

6 條,blocking 2。
