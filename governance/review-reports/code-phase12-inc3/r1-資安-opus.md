severity: major

範圍:整份 r1-snapshot.patch(1968 行),基準 3f50354..HEAD(836eb27)。實驗都在 `/tmp/資安-opus-p12i3` 的複本裡跑,用的直譯器是 `/Users/enzo/rtb-production-agent-demo/.venv/bin/python`(pytest 9.1.1)。做完已確認沒有殘留行程,臨時目錄也刪了。

## 發現 1:驅動把整份使用者環境交給比較表產生器,破壞 [S1003] 白名單,金鑰會帶進子行程
severity: major
blocking: 是

引句:「self.comparison_command, self.demo_id, self.user_env,」

`run_comparison` 把 `Driver.user_env` 原樣傳進 `env=dict(env)`。在伺服器上,`user_env` 就是整份 `os.environ`(file: `src/rtb/demo/driver.py:1258`)。

- **合約怎麼寫**:[S1003] 規定「當驅動程式啟動任何行程時」,子行程環境只能有 PATH、HOME、LANG、USER,加上固定的 PYTHONPATH(file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase12一鍵展示與HTML報告_計劃.md:271`)。
- **同一支檔的前例**:驗證器就守這條,只給 PATH、HOME、LANG(file: `src/rtb/demo/driver.py:1130`)。
- **計劃本身互相矛盾**:計劃第 390 行寫「子行程環境從 os.environ 起頭」,跟 [S1003] 衝突,要協調者裁定用哪一條。
- **額外的洞:`tools` 可被頂替**。PYTHONPATH 在驅動這層沒被拿掉,而 `tools` 是命名空間套件(沒有 `__init__.py`)。只要使用者 PYTHONPATH 上任何一個目錄有正規的 `tools/__init__.py`,`-m tools.forgery_comparison` 就會跑到那一份。
  - 引句:「return [sys.executable, "-m", "tools.forgery_comparison"]」

**例子一(金鑰外洩)**
- 輸入:`user_env` 帶 `ANTHROPIC_API_KEY=sk-ant-LEAK`、`RTB_APPROVAL_KEY=approval-LEAK`、`PYTEST_ADDOPTS`。
- 預期:產生器只看得到白名單內的鍵。
- 實際:產生器的環境鍵是 `ANTHROPIC_API_KEY,HOME,LC_CTYPE,PATH,PYTEST_ADDOPTS,RTB_APPROVAL_KEY,__CF_USER_TEXT_ENCODING`。產生器再把這份環境往下傳給暫存 repo 的 pytest 和驗證器。

**例子二(`tools` 被頂替)**
- 輸入:PYTHONPATH 指向一個含 `tools/__init__.py` 與 `tools/forgery_comparison.py` 的目錄。
- 預期:跑的是專案內的產生器。
- 實際:印出 `hijacked`,外來的那一份 JSON 被當成比較表。

**重現**
- 例子一:`PYTHONPATH=src python -c` 呼叫 `driver.run_comparison([sys.executable,"-c","import os,json;print(json.dumps({'rows':[],'note':','.join(sorted(os.environ)),'seconds':0}))"], "d", {…上述鍵…}, 10)`,再看 `.note`。
- 例子二:在 repo 根跑 `PYTHONPATH=<evil> python -m tools.forgery_comparison`。

**建議**:改用跟 `run_verifier` 一樣的白名單,驅動這層拿掉 PYTHONPATH;或改成用檔案路徑跑 `tools/forgery_comparison.py`,別靠 `-m`。

## 發現 2:「沒有驗證器」那一欄會被外面的 PYTEST_ 變數與自動載入的外掛改掉,兩欄跑的不是同一組測試
severity: major
blocking: 是

引句:「code, out = _run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",」

產生器只拿掉 PYTHONPATH:

引句:「env = dict(os.environ)」

兩欄的環境因此不一樣:

- **「有驗證器」欄**:驗證器會先清掉 `PYTEST_*`、PYTHONSTARTUP,並設 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`(file: `tools/verify_claims.py:1147`)。
- **「沒有驗證器」欄**:裸跑的 pytest 吃得到使用者的 `PYTEST_ADDOPTS`,也會自動載入 venv 裡所有外掛。

這違反計劃的合約「同一份改動、同一份清單、同一組測試,差別只在有沒有跑驗證器」。表上會顯示錯的結果,而且報告會照樣發布出去。

**例子**
- 輸入:伺服器環境有 `PYTEST_ADDOPTS=--noconftest`。
- 預期:六列的「沒有驗證器」都是 `pytest 結束代碼 0:2 passed`(基準實測就是這樣)。
- 實際有兩列變了:
  - 第 5 列(conftest 偷改)變成 `pytest 結束代碼 1:1 failed, 1 passed`,看起來像沒有驗證器也擋得下。
  - 第 6 列(天花板)變成 `pytest 結束代碼 1:1 failed, 1 passed | 通過:5 條宣稱…`,跟說明「有驗證器也擋不住」自相矛盾。

**重現**:在 repo 根跑 `PYTEST_ADDOPTS="--noconftest" PYTHONPATH=src python -m tools.forgery_comparison`,再跟不加這個變數的輸出比。

**建議**:「沒有驗證器」這一步改用驗證器的 `clean_environment()`,或跟驗證器同樣帶 `-E -s` 與同一份清環境規則。現有測試只在乾淨環境跑,抓不到這個問題。

## 發現 3:產生器的輸出沒有大小上限,部分讀不懂的輸出會變成沒攔住的例外
severity: minor
blocking: 否

引句:「except (ValueError, KeyError, TypeError) as broken:」

引句:「seconds = float(body.get("seconds", seconds))」

合約說輸出「讀不懂都記成這次沒產生」。實際上:

- **OverflowError 沒攔住**:`seconds` 是超大整數時,`float()` 會丟這個例外。
- **RecursionError 沒攔住**:JSON 巢狀很深時會丟這個例外。
- **沒攔住的後果**:例外會跑出 `run_all`,被伺服器的通用處理接住(file: `src/rtb/demo/server.py:184`),stderr 印「展示中止」。狀態庫不會留下「這次沒產生:原因」,頁面顯示成「還沒有產生」。
- **沒有大小上限**:`chunks.extend(popen.stdout)` 整份讀進記憶體。實測 50MB 的 note 會原樣寫進狀態庫,報告也會照樣畫出來。

目前產生器是專案自己的程式,能觸發的路徑有限,所以列 minor。

**例子**
- 產生器印 `{"rows": [], "note": "n", "seconds": 999…(400 位)}`:預期記成「這次沒產生:讀不懂」,實際丟出 `OverflowError: int too large to convert to float`。
- 產生器印 `"[" * 200000`:實際丟出 `RecursionError`。

**重現**:`driver.run_comparison([sys.executable,"-c",<上述 print>], "d", {"PATH":"/usr/bin:/bin"}, 20)`。

**建議**:
- 攔 `Exception`,或至少加上 OverflowError 與 RecursionError。
- 讀取設上限(例如 1MB),超過就記「這次沒產生」。
- `seconds` 改用驅動自己量的時間。

## 發現 4:產生器正在刪暫存 repo 時收到 SIGTERM,暫存 repo 會殘留一半
severity: minor
blocking: 否

引句:「raise SystemExit(128 + signum)」

引句:「with tempfile.TemporaryDirectory(prefix="forgery-") as temporary:」

訊號處理函式在任何一行都可能丟 SystemExit。如果 SIGTERM 剛好落在某一列跑完、`TemporaryDirectory.__exit__` 正在執行 `rmtree` 的時候,刪除就會中斷。

這違反「暫存專案產完就刪」。殘留目錄在使用者的 TMPDIR(權限 0700),內容不含機密,所以列 minor。

**例子**
- 輸入:產生器跑了 0.05 到 1.5 秒之間的隨機時間後,對它的整組送 SIGTERM。
- 預期:TMPDIR 底下沒有 `forgery-*`。
- 實際:40 次裡殘留 1 個,80 次裡又殘留 1 個。殘留目錄只剩 `tests/` 與 `__pycache__`,`src/`、`claims/`、`pyproject.toml` 已被刪掉,可以確定是刪到一半被打斷。兩輪都沒有找到殘留的 pytest 或驗證器孫行程。

**重現**:用 `TMPDIR=<空目錄>` 迴圈啟動 `python -m tools.forgery_comparison`(`start_new_session=True`),隨機延遲後 `os.killpg(pid, SIGTERM)`,最後列出 TMPDIR 的內容。

**建議**:訊號處理函式只記一個旗標,在兩列之間檢查;或在清理期間先暫時遮蔽 SIGTERM。

## 發現 5:執行環境沒有 pytest 時,整張表照樣寫成真結果,天花板列還會顯示「擋下」
severity: minor
blocking: 否

引句:「return f"pytest 結束代碼 {code}:{last}"」

產生器用的是伺服器的 `sys.executable`。如果那個直譯器沒有裝 pytest(例如只裝執行期依賴的 venv),每一列都會被寫成「真的跑出來」的結果,不會標成「這次沒產生」。

**例子**
- 輸入:用沒有 pytest 的 venv(`/Users/enzo/rtb-12i1/.venv`)跑產生器。
- 預期:整張表標「這次沒產生:環境缺 pytest」。
- 實際:
  - 六列的「沒有驗證器」都是 `pytest 結束代碼 1:… No module named pytest`。
  - 天花板列的「有驗證器」是 `擋下:…匯入的 pytest 解析不到…`,跟說明「有驗證器也擋不住」相反。

**重現**:`PYTHONPATH=src /Users/enzo/rtb-12i1/.venv/bin/python -m tools.forgery_comparison`。

**建議**:先檢查 pytest 能不能匯入;或把「pytest 結束代碼不是 0 也不是 1,或總結行不是測試總結」當成這一列沒產生。

## 實作者兩個問題的回覆

1. **搬家前後的項目清單**:在 3f50354(用 `git archive` 取出)與 HEAD 各跑一次 `pytest --collect-only -q tests/tools/test_verify_claims.py`,兩邊都是 278 項,diff 完全相同。
2. **底線名字加公開別名**:從資安角度可以接受。正式程式碼(src)沒有匯入 `tools.*`。`tools/` 只透過子行程被呼叫,被頂替的風險寫在發現 1。

## 我這一席的其他檢查,沒有發現問題

- **暫存 repo 放在哪裡**:在 TMPDIR 底下用 `mkdtemp` 建立,權限 0700。我在 TMPDIR 的上一層放了惡意 `conftest.py`,pytest 9.1.1 沒有載入它(confcutdir 停在 rootdir)。
- **比較表的文字跳脫**:每一格和說明都經過 `escape_text`,也就是先把控制字元換成看得到的代碼,再做 `html.escape(quote=True)`。沒有發現注入點。
- **取消和逾時的殘留行程**:驅動送 SIGTERM 後等 10 秒;產生器自己最多等 5+5 秒,驗證器收到 SIGTERM 會馬上收掉它的證據測試。壓力測試 120 次,沒有殘留孫行程。
- **正式行程會不會匯入 `tools/`**:src 裡沒有 `import tools` 或 `from tools`。

## 看過的改動檔(整份 r1-snapshot.patch)

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

5 條,blocking 2。
