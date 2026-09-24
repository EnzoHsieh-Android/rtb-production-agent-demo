severity: major

範圍：這一席看完了最後凍結的 `r3-snapshot.patch`(2456 行)，第 2 輪之後的修正以 `r3-delta.patch` 為準。我用 `git diff 3f50354..HEAD` 比對過這份快照：新增的每一行都一樣，差別只在文件那幾段的前後文行號。

實驗環境：
- 所有實驗都在 `/tmp/資安-opus-p12i3r3` 的複本裡跑。
- 直譯器是 `/Users/enzo/rtb-production-agent-demo/.venv/bin/python`(Python 3.14.6、pytest 9.1.1)。
- 這輪的回歸測試我在複本跑過:`test_driver` 裡跟比較表有關的、`tests/tools/test_forgery_comparison.py` 整支，加上 `test_a_shadowed_pytest_internal_package_at_the_repo_root_is_blocked`,全部通過。
- 做完已確認沒有殘留行程，臨時目錄也刪了。

## 第 2 輪兩條逐條驗收

| 第 2 輪 | 修正內容 | 我怎麼驗 | 結果 |
|---|---|---|---|
| 1 repo 根放 `_pytest/` 就能騙過驗證器 | 驗證器 `run_evidence`,以及產生器的兩欄，都加了 `-P` | 用快照裡的 `shadowed_pytest_internals` 造 repo:驗證器擋下 `test_pause`;產生器那一欄是 `1 failed, 1 passed` | `_pytest/` 這條路堵住了。但加 `-P` 之後，同一類頂替還有別的路，見發現 1,所以天花板那句仍然不成立 |
| 2 語系不一致時會亂碼、讀取執行緒會死 | 驗證器與產生器加 `-X utf8`;驅動程式改用 `encoding="utf-8", errors="replace"` | 伺服器設 `LANG=en_US.ISO8859-1`、`LC_ALL=en_US.UTF-8`,比較表六列都正常產生。產生器先印 `b'\xff'` 再印 300KB,記成「讀不懂」,沒有卡到逾時 | 驅動程式和產生器這兩段修好了。驗證器自己起的證據測試沒跟著改，留下一條新的崩潰路線，見發現 2 |

## 發現 1:加了 `-P` 之後,repo 根或 src 放一支跟標準庫同名的模組(例如 `pdb.py`),照樣能把失敗的證據測試改成通過，不用重算雜湊
severity: major
blocking: 是

引句:「        command = [sys.executable, "-E", "-s", "-P", "-c", RUNNER, str(recorded),」

引句:「"不是比模型寫程式的品質。最後一列是天花板:改結果的鉤子連雜湊一起重算,有驗證器也擋不住,"」

引句:「驗證器與產生器跑證據測試的指令都加 -P:原本 repo 根放一份改結果的 `_pytest/` 就能騙過驗證器、不用重算雜湊,說明裡的天花板因此不成立;加了之後天花板那句照舊成立。」

**洞在哪**
- `-P` 只管直譯器啟動那一刻，不把工作目錄放進匯入路徑。
- pytest 啟動後自己會再把兩個目錄插到匯入路徑最前面:
  - 設定檔 `pythonpath = ["src"]` 會插入 `src/`。
  - 預設的 prepend 匯入模式會插入 repo 根。驗證器規定證據測試所在的每層目錄都要是套件(file: `tools/verify_claims.py:336`),所以測試套件的最上層就是 repo 根。
- 插完之後，pytest 自己的外掛還會匯入一批純 Python 的標準庫模組。我在 `pytest_configure` 之後記錄了每個還沒載入的頂層名稱，當時匯入路徑前兩項已經是 repo 根和 `src/`。記到的有:`pdb`、`cmd`、`code`、`socket`、`asyncio`、`base64`、`ssl`、`contextvars` 等。
- 驗證器的閉包與「解析不到」檢查只追證據測試檔自己的匯入(file: `tools/verify_claims.py:622`)。pytest 內部匯入的東西不在閉包裡，也沒有雜湊可比。
- 結果:`_pytest/` 這條路堵住了，但同一類頂替換個名字就能過。天花板那句「改結果的鉤子連雜湊一起重算」照樣不成立。
- 驗證器的這段程式是 Phase 11 的，但這次的說明和計劃都寫「加了之後天花板那句照舊成立」,所以算這次的問題(判斷方式跟第 2 輪發現 1 相同)。

**例子**
- 輸入：用 `forgery_demos` 造小 repo,把 `test_pause` 改成 `assert not act`(真的會失敗),照實重算清單。然後在 repo 根放一支 `pdb.py`,內容大約 15 行:
  - 暫時拿掉自己所在的目錄，載入真正的 `pdb` 放回 `sys.modules`。
  - 包一層 `_pytest.reports.TestReport.from_item_and_call`,把 `call` 階段的結果改成 `passed`。
- 預期：驗證器擋下，原因是 `tests/dsp/test_server.py::test_pause 沒通過`。沒放 `pdb.py` 時實測就是這樣，結束代碼 1。
- 實際：驗證器結束代碼 0,印出 `通過:5 條宣稱,跑了 2 支證據測試全部通過`。產生器那一欄的指令(`-E -s -P -X utf8 -m pytest …`)也是 `2 passed`。
- 變化 1:同一支檔改放 `src/pdb.py`,驗證器同樣放行。
- 變化 2:加 `--import-mode=importlib` 只擋得住 repo 根那一份。`src/pdb.py` 照樣 `2 passed`,因為 `pythonpath = ["src"]` 仍會把 `src/` 插到最前面。

**重現**
1. 用 `python -c` 匯入 `tools.forgery_demos`,依序呼叫:
   - `write_files(repo, FILES)`
   - `write_files(repo, {"tests/dsp/test_server.py": TEST_SERVER.replace("def test_pause():\n    assert act", "def test_pause():\n    assert not act")})`
   - `write_manifests(repo)`
2. 把上面描述的 `pdb.py` 寫進 `<repo>/pdb.py`(或 `<repo>/src/pdb.py`)。
3. 在 repo 裡跑 `env -i PATH=/usr/bin:/bin HOME=$HOME python -E -s -P -X utf8 <專案>/tools/verify_claims.py claims`,結束代碼是 0。

**建議**
- 最穩的做法是在執行後核對：讓 RUNNER 在 `pytest.main` 結束後，把 `sys.modules` 裡 `__file__` 落在 repo 裡的檔案全部列出來。只要有任何一支不在閉包雜湊裡，驗證器就擋下。這樣不管用哪種名字頂替，都查得到。
- 另一種做法是靜態規則:repo 根與 `src/` 最上層不准有跟 `sys.stdlib_module_names` 或已安裝套件同名的 `.py` 檔或套件目錄。
- 在修好之前，比較表說明的天花板那句要照實改寫，計劃和 `宣稱驗證器` 節點裡「照舊成立」的說法也要改。
- 這個改動落在 Phase 11 的驗證器，由協調者裁定歸哪個增量。

## 發現 2:驗證器自己加了 `-X utf8`,它起的證據測試卻沒加。語系是 latin-1 時，失敗訊息裡只要有非 ASCII 字元，驗證器就在解碼時崩潰
severity: minor
blocking: 否

引句:「      - 編碼:驗證器與產生器的子行程加 -X utf8,驅動程式讀輸出照 UTF-8、讀不懂的位元組換成替代字元(伺服器的 LANG 與 LC_ALL 不一致時也不出錯、不卡)。」

引句:「        command = [sys.executable, "-E", "-s", "-P", "-c", RUNNER, str(recorded),」

**洞在哪**
- 驅動程式把驗證器改成 `-X utf8` 之後，驗證器用 `text=True` 讀證據測試的輸出，解碼就變成嚴格的 UTF-8(file: `tools/verify_claims.py:1203`)。
- 但證據測試那個子行程的旗標只有 `-E -s -P`,仍照 `LANG` 決定輸出編碼。環境白名單只照抄 `LANG`,沒有 `LC_ALL`。
- 兩邊編碼一不一致，失敗訊息裡的 latin-1 位元組就會在 `communicate` 裡丟出 `UnicodeDecodeError`。驗證器帶著 traceback 以結束代碼 1 退出，沒有印出正常的「擋下」或「判不出」原因。
- 產生器那邊把 `-X utf8` 同時加在子行程(`_FLAGS`),所以只有驗證器這條路有這個問題。

**例子**
- 輸入:`LANG=en_US.ISO8859-1`。證據測試 `test_pause` 失敗，assert 訊息是 `"caf\u00e9"`。用 `python -X utf8 tools/verify_claims.py claims` 跑，這正是驅動程式的預設指令。
- 預期：擋下，原因是 `test_pause 沒通過`。
- 實際:`UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe9 in position 357`,結束代碼 1,沒有擋下原因。展示頁只會寫驗證器結束代碼 1。
- 它是往「不通過」那邊壞，不會誤放行。而且要語系設定不一致，加上證據測試失敗才會碰到，所以列 minor。

**重現**
- 在 `mk` 造出的 repo 裡，把失敗的 assert 加上 `, "caf\u00e9"`,然後照實重算清單。
- 跑 `env -i PATH=/usr/bin:/bin HOME=$HOME LANG=en_US.ISO8859-1 python -X utf8 <專案>/tools/verify_claims.py claims`。

**建議**
- `run_evidence` 的指令也加 `-X utf8`,或者 Popen 改用 `encoding="utf-8", errors="replace"`,跟產生器一致。

## 這輪三個重點的結論

- **加了 `-P` 之後還有沒有別的頂替路**
  - 有，見發現 1。pytest 自己會把 repo 根和 `src/` 插到匯入路徑最前面，之後才匯入的標準庫模組都能被頂替。
  - `.pth` 從 repo 裡頂替不了:site 只讀 site-packages 目錄裡的 `.pth`,不讀工作目錄。`-s` 也擋掉了 user site。
- **改回 `-m tools.forgery_comparison` 之後能不能被頂替**
  - 工作目錄的位置：驅動程式固定用 `PROJECT_ROOT`(`rtb` 套件往上一層)當工作目錄。專案沒有 build-system、不能安裝，所以這個位置一定是 repo 根，外面改不了。
  - site-packages 裡另有正式的 `tools` 套件：實測頂替不了，照樣跑到 repo 那一份(輸出「環境缺 pytest」)。
  - `.pth` 的路徑行，以及 `import sys; sys.path.insert(0, …)` 這種匯入行：實測都頂替不了，因為 `-m` 是在 site 處理完之後才把工作目錄插到第一位。
  - 反向驗證：同一個環境加上 `-P`,就被 `.pth` 頂替成功(`hijacked`)。可見新的防回歸測試不是空轉，真的守著「不能加 `-P`」這件事。
  - 還剩的是 `.pth` 用匯入行裝自訂的 `meta_path` 攔截器。這等於已經能在直譯器裡跑任意程式，pytest 和驗證器一樣擋不住，不列。
  - 附帶一點：產生器行程的匯入路徑第一項現在是 repo 根，所以 repo 根一支 `json.py` 就能改掉比較表。但能寫 repo 根的人本來就能直接改 `tools/forgery_comparison.py`,兩種改法在提交裡一樣看得到，不列。
- **`-X utf8` 加上 `errors="replace"`**
  - 驅動程式與產生器：判定一律看結束代碼。替代字元只會讓 JSON 讀不懂，然後記成「這次沒產生」,不會把沒通過變成通過。上限算的是字元，替代之後仍然有界。
  - 唯一的新問題在驗證器那一段，見發現 2。

## 另外檢查、沒列成發現的

- 驅動程式跑驗證器的預設指令現在是 `python -X utf8 tools/verify_claims.py`,仍然沒有 `-E -s`。這是舊有行為，第 2 輪已經說明過，不列。

## 實作者兩個問題的回覆

1. **項目清單**:這輪新增一支驗證器測試，驗證器測試的項目數本來就會多 1 項。原本那 278 項是不是不變，這輪我沒有重收，留給修正驗收席。
2. **底線名字加公開別名**:第 1 輪之後已經改成公開名字、拿掉別名。從資安角度沒有問題：正式程式(`src`)沒有匯入 `tools.*`。

## 看過的改動檔(整份 r3-snapshot.patch)

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
- `tools/__init__.py`
- `tools/forgery_comparison.py`
- `tools/forgery_demos.py`
- `tools/verify_claims.py`

2 條，blocking 1。
