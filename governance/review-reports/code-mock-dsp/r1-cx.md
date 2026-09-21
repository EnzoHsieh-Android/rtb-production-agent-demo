severity: major
finding: `src/rtb/dsp/server.py:145`、`src/rtb/dsp/store.py:134` 未驗證 `expected_version` 必須是非布林正整數；JSON `{"new_budget":150,"expected_version":true}` 會建立 `expected_version=True`，而 Python 判定 `1 == True`，因此版本 1 的廣告真的被更新到版本 2，讓型別錯誤輸入繞過樂觀鎖。
severity: major
blocking: 是 — 無效版本型別會實際改寫廣告狀態，破壞「寫入一律帶預期版本」合約。
引句:「return Operation(campaign_id, action, params, body.get("expected_version", 0), key)」
佐證: file: `src/rtb/dsp/server.py:145`；file: `src/rtb/dsp/store.py:154`。最小重現：`PYTHONPATH=src .venv/bin/python -c 'from pathlib import Path; from rtb.dsp.store import CampaignStore,Operation; s=CampaignStore(Path(":memory:")); s.seed_campaign("c1",100); r=s.execute(Operation("c1","update_budget",{"new_budget":150},True,"bool-version")); print(r); print(s.get_campaign("c1"))'`；輸出為 `OperationResult(... version_after=2 ... replayed=False)` 與 `Campaign(id='c1', budget=150, status='active', version=2)`。

finding: `src/rtb/dsp/store.py:84` 只檢查預算為正整數，沒有檢查 SQLite INTEGER 上限；輸入 `new_budget=9223372036854775808` 通過驗證後在 SQL 綁值時拋出未型別化的 `OverflowError`。`src/rtb/dsp/server.py:77` 又只捕捉 `DspError`，所以 HTTP 呼叫不會得到約定的永久驗證錯誤，而是連線被中斷且例外遭 `handle_error` 靜默吞掉。
severity: major
blocking: 是 — 合法 JSON 可讓公開寫入介面不回任何型別化 HTTP 結果，違反錯誤合約並使呼叫端誤判為結果不明。
引句:「if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:」
佐證: file: `src/rtb/dsp/store.py:84`；file: `src/rtb/dsp/store.py:185`；file: `src/rtb/dsp/server.py:83`。最小重現：`PYTHONPATH=src .venv/bin/python -c 'from pathlib import Path; from rtb.dsp.store import CampaignStore,Operation; s=CampaignStore(Path(":memory:")); s.seed_campaign("c1",100); op=Operation("c1","update_budget",{"new_budget":2**63},1,"huge"); exec("try: s.execute(op)\\nexcept Exception as e: print(type(e).__name__+\\\": \\\"+str(e))"); print(s.get_campaign("c1")); print(s.history("c1"))'`；輸出含 `OverflowError: Python int too large to convert to SQLite INTEGER`，狀態仍為版本 1、歷史為空。

finding: `tests/dsp/test_server.py:167` 的並行測試只執行一次，沒有設計要求的 50 次重複、移除唯一約束的對照實作，亦沒有同鍵不同內容並行競爭；因此測試無法證明它能抓出雙寫，也未完成 Phase 1 的機械驗收。
severity: major
blocking: 是 — 明文驗收合約缺少必要的壓力、變異與競爭測試，並行冪等性尚未被要求的證據證明。
引句:「def test_concurrent_same_key_requests_over_http_apply_exactly_once(start_dsp):」
佐證: file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Agent_Phase0架構.md:275` 明定三項要求；file: `tests/dsp/test_server.py:167` 只有單次同內容競爭。最小重現：`rg -n 'range\\(50\\)|拿掉唯一|同鍵不同內容並行' tests/dsp` 無輸出、回傳碼 1；`rg -n '^def test_.*concurrent' tests/dsp` 只列出 `test_concurrent_same_key_requests_over_http_apply_exactly_once` 與儲存層同內容測試。

finding: `tests/dsp/test_store.py:103` 所謂原子提交事故測試只以 monkeypatch 丟出一般 Python 例外，隨後仍使用同一個存活連線驗證回滾；它沒有在狀態更新與冪等紀錄之間終止行程，也沒有重啟 DSP，因而未覆蓋設計要求的真實當機恢復路徑。
severity: major
blocking: 是 — 「注入當機、重啟 DSP」是明文驗收條款，目前測試只證明例外處理會主動 ROLLBACK，不能證明行程猝死時 SQLite 恢復後仍原子。
引句:「monkeypatch.setattr(store, "_record_idempotency", explode)」
佐證: file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Agent_Phase0架構.md:276`；file: `tests/dsp/test_store.py:103`。最小重現：`sed -n '103,118p' tests/dsp/test_store.py` 顯示測試僅 monkeypatch、呼叫 `store.execute`、再由同一 `store` 查狀態；片段中沒有 subprocess 終止、重新開啟 store 或重啟 DSP。

finding: `src/rtb/dsp/server.py:110` 直接把 `Content-Length` 轉成整數，未捕捉非數字或拒絕負值；例如 `Content-Length: abc` 會逸出 `ValueError` 而中斷連線，負值則令 `read(-1)` 等待客戶端 EOF，能長時間占住一條無上限的處理執行緒。
severity: minor
blocking: 否 — 問題限於畸形 HTTP 請求，未直接造成資料錯寫，但應回 400 並拒絕負長度。
引句:「length = int(self.headers.get("Content-Length") or 0)」
佐證: file: `src/rtb/dsp/server.py:110`。最小重現：`PYTHONPATH=src .venv/bin/python -c 'import io; from types import SimpleNamespace; from rtb.dsp.server import DspHandler; h=SimpleNamespace(headers={"Content-Length":"abc"},rfile=io.BytesIO(b"{}")); exec("try: DspHandler._read_json(h)\\nexcept Exception as e: print(type(e).__name__+\\\": \\\"+str(e))")'`；輸出為 `ValueError: invalid literal for int() with base 10: 'abc'`。

`.gitignore`：已讀,無 finding。
`.lumos/config.json`：已讀,無 finding。
`.lumos/lint.json`：已讀,無 finding。
`docs/rtb-production-agent-demo-knowledge/Systems/Mock-DSP.md`：已讀,無 finding。
`pyproject.toml`：已讀,無 finding。
`requirements-dev.txt`：已讀,無 finding。
`src/rtb/dsp/errors.py`：已讀,無 finding。
`tests/__init__.py`：已讀,無 finding。
`tests/dsp/__init__.py`：已讀,無 finding。
`tests/dsp/conftest.py`：已讀,無 finding。
測試執行補充：完整 pytest 在唯讀沙盒中因 Python 找不到可寫暫存目錄而於收集前失敗；上述程式缺陷均以不寫檔的 `:memory:` 或純解析最小重現查證。
最嚴重等級 major，blocking 共 4 條。
