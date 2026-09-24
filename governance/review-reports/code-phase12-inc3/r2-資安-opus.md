severity: major

範圍：這一席看完了凍結的 `r2-snapshot.patch`(2266 行，對應 3f50354..cff260d)，第 1 輪之後的修正則以 `r2-delta.patch` 為準。我用 `git diff 3f50354..HEAD` 比對過這份快照，除了 `docs/.governance-log.jsonl` 與檔名引號寫法，內容完全一樣。

實驗環境：
- 所有實驗都在 `/tmp/資安-opus-p12i3r2` 的複本裡跑。
- 直譯器是 `/Users/enzo/rtb-production-agent-demo/.venv/bin/python`(Python 3.14.6)。
- 做完已確認沒有殘留行程，臨時目錄也刪了。

## 第 1 輪五條逐條驗收

| 第 1 輪 | 修正內容 | 我怎麼驗 | 結果 |
|---|---|---|---|
| 1 整份環境進產生器、`tools` 可被頂替 | 新增 `tool_environment()`。產生器改用檔案路徑加 `-E -s` 跑 | 環境帶 `ANTHROPIC_API_KEY`、`RTB_APPROVAL_KEY`、`PYTEST_ADDOPTS`,產生器只看到 `HOME,LANG,PATH,__CF_USER_TEXT_ENCODING`。PYTHONPATH 指向含 `tools/__init__.py` 的目錄，也頂替不了 | 已修 |
| 2 兩欄吃到的 PYTEST_ 變數不同 | 「沒有驗證器」欄也用 `clean_environment()`,旗標跟驗證器相同 | 直接帶 `PYTEST_ADDOPTS=--noconftest` 跑產生器:六列都是 `pytest 結束代碼 0:2 passed`,天花板列是「通過」 | 已修 |
| 3 輸出沒有上限、例外沒攔住 | 設 1MB 上限，攔 `Exception`,秒數改由驅動程式自己量 | 400 位的 seconds 記成 note `n`;`"["*200000` 記成「讀不懂(RecursionError)」;200 萬字元記成「上限」 | 已修(另見發現 2 的新洞) |
| 4 SIGTERM 讓暫存 repo 殘留一半 | 訊號處理只記旗標 | 40 次在隨機 0.1–2.6 秒取消:38 次記成「被取消」、2 次完整跑完。暫存目錄沒有殘留，也沒有孫行程 | 已修 |
| 5 沒有 pytest 時照樣寫成真結果 | 先執行 `_has_pytest` 檢查,pytest 沒跑成的那一列算沒產生 | 用 `/Users/enzo/rtb-12i1/.venv` 跑，輸出 `這次沒產生:環境缺 pytest(跑證據測試要用)` | 已修 |

## 發現 1:repo 根放一個 `_pytest/` 就能把失敗的證據測試變成通過，不用重算雜湊，驗證器照樣放行。比較表說明的天花板因此不成立
severity: major
blocking: 是

引句:「"不是比模型寫程式的品質。最後一列是天花板:改結果的鉤子連雜湊一起重算,有驗證器也擋不住,"」

引句:「"""跟驗證器跑證據測試同一套旗標:-E 不讀 PYTHON 開頭的變數、-s 不開 user site、設定檔與根目錄」

**洞在哪**
- 驗證器跑證據測試用的是 `python -E -s -c RUNNER`,工作目錄是 repo 根(file: `tools/verify_claims.py:1195`)。
- 產生器「沒有驗證器」那一欄用的是 `python -E -s -m pytest`,也照抄同一套旗標。
- `-c` 和 `-m` 都會把工作目錄放在 `sys.path` 最前面。`-E -s` 擋不了這一點，要加 `-P` 才擋得住。
- RUNNER 匯入的 pytest 來自 site-packages,但 pytest 內部接著 `import _pytest.*`,這時會先找到 repo 根的 `_pytest/`。
- 驗證器的閉包檢查只追測試檔自己的匯入。前例是 repo 根放 `pytest.py`:驗證器認得出來，擋下原因是「用到的 pytest.py 在 src、tests 以外」。
- 但 `_pytest/` 是 pytest 內部自己匯入的，不在閉包裡，也沒有雜湊可以比。

**為什麼算這次的問題**
報告底部的說明寫「天花板：改結果的鉤子連雜湊一起重算」。讀的人會以為要騙過驗證器至少得重算雜湊，這一點會進提交、審查員看得到。實際上完全不碰清單就能騙過。等於這次上線的比較表對外寫了一個不成立的限制。

**例子**
- 輸入：用 `forgery_demos` 造小 repo,把 `test_pause` 改成 `assert not act`(真的會失敗)，照實重算清單。然後把 site-packages 的 `_pytest/` 整份複製到 repo 根，只在 `_pytest/runner.py` 尾端包一層 `pytest_runtest_makereport`,把 `call` 階段的結果改成 `passed`。
- 預期：驗證器擋下，原因是 `test_pause 沒通過`(沒放 `_pytest/` 之前實測就是這樣)。
- 實際：驗證器結束代碼 0,輸出 `通過:5 條宣稱,跑了 2 支證據測試全部通過`。同一個 repo 裸跑 pytest 也是 `2 passed`。
- 對照：同樣的指令加上 `-P`(`python -E -s -P -m pytest …`),結果是 `1 failed, 1 passed`,假的 `_pytest` 不會被載入。

**重現**
1. `python -c` 載入 `tools/forgery_demos.py`,依序呼叫 `write_files(repo, FILES)`、`write_files(repo, {"tests/dsp/test_server.py": TEST_SERVER.replace("def test_pause():\n    assert act", "def test_pause():\n    assert not act")})`、`write_manifests(repo)`。
2. `cp -R <venv>/site-packages/_pytest <repo>/_pytest`,在 `<repo>/_pytest/runner.py` 尾端加上改結果的包裝。
3. 在 repo 裡跑 `python -E -s tools/verify_claims.py <repo>/claims`。

**建議**
- 驗證器的 `run_evidence` 和產生器的 `_pytest_command` 都加 `-P`(Python 3.11 起才有;專案已經用到 `tomllib`,版本符合)。
- 或者讓驗證器拒絕 repo 根有任何不在閉包內、可被匯入的 `.py` 或套件目錄。
- 改法在 Phase 11 的驗證器裡，要協調者裁定歸哪一個增量。在修好之前，比較表說明的天花板那句要照實改寫。

## 發現 2:白名單只照抄 LANG,子行程和驅動程式的編碼可能對不上。表格會變亂碼或整張不產生;遇到解不開的位元組，讀取執行緒會死掉，一路卡到外層逾時
severity: minor
blocking: 否

引句:「_TOOL_VARIABLES = ("PATH", "HOME", "LANG")」

引句:「while stream is not None and (chunk := stream.read(65536)):」

**洞在哪**
- 驅動程式用 `text=True` 讀輸出，解碼用的是伺服器自己的 locale,而伺服器的 locale 會受 `LC_ALL`、`LC_CTYPE` 影響。
- 子行程只拿到 `LANG`,所以用 `LANG` 決定輸出編碼。兩邊不一致時就會出錯。
- 產生器跑的是 `-E`,所以連 `PYTHONIOENCODING`、`PYTHONUTF8` 都救不回來。
- 另外，讀取是嚴格解碼。遇到解不開的位元組，讀取執行緒會丟 `UnicodeDecodeError` 並死掉，之後就沒有人讀管線。輸出一旦超過管線緩衝，子行程就卡住，直到外層逾時(600 秒)。
- 第 1 版把整份環境交給產生器，沒有這個問題;改成白名單之後才出現。驗證器那邊也共用 `tool_environment`,受同樣影響。

**例子**
- A:伺服器環境 `LANG=en_US.ISO8859-1`、`LC_ALL=en_US.UTF-8`。
  - 預期：比較表正常產生，驗證器通過。
  - 實際：比較表記成 `這次沒產生:產生器結束代碼 1`,驗證器記成 `驗證器結束代碼 1`,也就是展示頁會寫驗證器沒過。原因是子行程用 latin-1 印中文，丟出 UnicodeEncodeError。
- B:`LANG=zh_TW.UTF-8`、`LC_ALL=en_US.ISO8859-1`。
  - 預期：正常的中文表。
  - 實際：每一格都是亂碼，照樣記進狀態庫，例如 `'�\x8f�寫�\x80\x8c已…'`。
- C:產生器先印 `b'\xff'`,再印 300KB。
  - 預期：記成「讀不懂」。
  - 實際：讀取執行緒丟 `UnicodeDecodeError`,整個等到逾時才記成 `逾時(8 秒)`(實驗把逾時設為 8 秒)。

**重現**
- A、B:`env -i PATH=/usr/bin:/bin HOME=$HOME LANG=… LC_ALL=… PYTHONPATH=src python -c "from rtb.demo import driver as d; print(d.run_comparison(d.default_comparison_command(),'d',120).note)"`。
- C:`d.run_comparison([sys.executable,'-c',"import sys;sys.stdout.buffer.write(b'\\xff'+b'a'*300000)"],'d',8)`。

**建議**
- 產生器和驗證器的指令都加 `-X utf8`。
- 驅動程式的 Popen 改用 `encoding="utf-8", errors="replace"`,取代 `text=True`。

列 minor 的理由：要伺服器的 `LANG` 跟 `LC_ALL`/`LC_CTYPE` 編碼不一致才會觸發。常見的配置(`LANG` 是 UTF-8,或 `LANG` 沒設、由 C locale 自動轉成 UTF-8)都不會出事。

## 這輪三個重點的結論

- **白名單環境**
  - 沒有多放：`HOME` 在 `-s` 之下不會帶出 user site,pytest 也不讀家目錄設定;驗證器沒有靠 `PATH` 找外部指令。
  - 漏掉的東西裡，會出事的只有 locale,見發現 2。
  - `TMPDIR` 被拿掉後，暫存 repo 和 junit 改放 `/tmp`,但都用 `mkdtemp` 建立(權限 0700),其他使用者讀寫不到，所以不列。副作用是營運端沒辦法再指定暫存位置。
  - 驅動程式跑驗證器的預設指令(file: `src/rtb/demo/driver.py:1107`)沒有加 `-s`。在沒有 venv 的直譯器上,user site 會在驗證器行程裡載入。這是舊有行為，改的人要有家目錄寫入權，不算提權，不列。
- **用檔案路徑載入**
  - 從外部已經頂替不了：`-E` 擋掉 PYTHONPATH 和 sitecustomize,`-s` 擋掉 user site;直接跑腳本時，工作目錄不在 `sys.path` 裡。
  - `sys.path[0]` 是 `tools/`,裡面沒有跟標準庫撞名的檔案。
  - `_load` 用檔案路徑載入 `forgery_demos` 和 `verify_claims`,這兩支都只匯入標準庫。
  - 還剩的頂替點在證據測試那一步，用 `-c`/`-m` 時工作目錄會在最前面，見發現 1。
  - 附帶一點:`test_the_comparison_generator_is_run_by_its_file_path_and_cannot_be_swapped` 的頂替那一半，就算改回舊的 `-m tools.forgery_comparison` 也會通過，因為 PYTHONPATH 已經先被白名單拿掉。真正守住檔案路徑的只有 `command[:3]` 那條斷言。
- **1MB 上限與解析**
  - 在正常結束的路徑上繞不過去：保留的內容最多到上限加 1 個字元，其餘照讀照丟;大小檢查排在結束代碼之後、解析之前。
  - 上限算的是字元，換成位元組最多約 4MB,記憶體仍然有界。
  - `except Exception` 涵蓋 RecursionError、MemoryError,以及超過 4300 位的整數(會丟 ValueError)。
  - 會失效的只有嚴格解碼讓讀取執行緒死掉的情況，見發現 2。

## 另外檢查、沒列成發現的

- **正常結束後的 `_kill_group`**:行程回收之後才對它的 pid 送 `killpg`,理論上 pid 剛好被重用成新的行程群組組長時會誤殺。視窗只有微秒級，也碰不到其他使用者的行程(會回 EPERM),不列。
- **取消壓力測試**:40 次取消後，暫存目錄與孫行程都沒有殘留。

## 實作者兩個問題的回覆

1. **項目清單**:用 `git archive` 取出 3f50354,和 HEAD 各跑一次 `--collect-only`。兩邊都是 278 項，排序後逐行 diff,完全相同。
2. **底線名字加公開別名**:這輪已經改成公開名字、拿掉別名，驗證器測試從 `FORGERIES` 展開。從資安角度沒有問題：正式程式(src)沒有匯入 `tools.*`。

## 看過的改動檔(整份 r2-snapshot.patch)

- `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase12一鍵展示與HTML報告_計劃.md`
- `docs/rtb-production-agent-demo-knowledge/Systems/一鍵展示.md`
- `docs/rtb-production-agent-demo-knowledge/Systems/宣稱驗證器.md`
- `docs/rtb-production-agent-demo-knowledge/Systems/展示頁面.md`
- `src/rtb/demo/driver.py`
- `src/rtb/demo/page.py`
- `src/rtb/demo/present.py`
- `src/rtb/demo/state_store.py`
- `tests/demo/test_driver.py`
- `tests/demo/test_page.py`
- `tests/demo/test_present.py`
- `tests/demo/test_server.py`
- `tests/demo/test_state_store.py`
- `tests/tools/test_forgery_comparison.py`
- `tests/tools/test_verify_claims.py`
- `tools/forgery_comparison.py`
- `tools/forgery_demos.py`

2 條，blocking 1。
