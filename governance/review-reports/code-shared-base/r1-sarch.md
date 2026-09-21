severity: minor

架構對齊審查(第 1 輪)。結論:共用基礎放 `src/rtb/` 頂層、依賴方向單向(dsp 依賴 kit,kit 不回頭依賴 dsp,domain 不碰),DSP 沒留第二份實作(已對 patch 內 server/store 逐項確認,`tests/kit/test_shared_base.py` 也守著)。命名與錯誤處理沿用原本的碼(`internal_error`、`StoreBusy` 映射)。以下只有小的對齊缺口。

### 1. 領域層的禁匯入清單沒涵蓋新的共用基礎,靠傳遞式規則擋不住
severity: minor
blocking: 否 目前 domain 沒有任何匯入,只是護欄少一格,不是現況違規。
引句:「from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer」
說明:domain 的 ruff 只禁 `sqlite3`、`http.server` 等直接匯入;`rtb.httpkit`、`rtb.sqlitekit` 是「包了網路與資料庫」的新第一方模組,領域層若匯入它們,現有 ruff 護欄不會紅。建議在 `src/rtb/domain/ruff.toml:5` 的 banned-api 補這兩個模組。
file: `src/rtb/domain/ruff.toml:5`

### 2. 「故障注入用」的 NoResponse 放進共用基礎,DSP 專屬概念上移
severity: minor
blocking: 否 只是名詞歸屬,不引入第二種做法,也沒有跨層直呼。
引句:「"""故障注入用:刻意不回應。"""」
說明:共用基礎的 `_dispatch` 為它留了專門分支,而提案收件口不會用故障注入;責任歸屬上偏向 Mock-DSP。可接受,但 `Systems/共用行程基礎` 的 responsibility 沒提到它,兩個節點的分工在這一點模糊。

### 3. 共用行為的測試在 tests/dsp 與 tests/kit 兩處重複
severity: minor
blocking: 否 是測試擺放的重疊,不是產品碼的第二份實作。
引句:「TEST: tests/kit 涵蓋回送位址、Host 檢查、重複標頭、本文上限與各種壞輸入」
說明:`tests/dsp/test_server.py` 仍留有 Host 大小寫、回送位址、408、超長 Content-Length 等同類測試(如 `test_host_header_matching_ignores_case`),`tests/kit/test_httpkit.py` 又有一份。以 DSP 為使用者的整合保護可留,但兩處的分工(kit 測基底、dsp 測路由與故障)沒寫進兩個節點,日後會分不清該改哪邊。
file: `tests/dsp/test_server.py:442`

### 4. 圖譜分工與「每支檔有家」整體到位(無問題,備查)
severity: minor
blocking: 否 僅備註,無需動作。
引句:「DSP 只保留路由、錯誤對照表與故障注入;上面這些規則的實作與防回歸現在在那一篇。」
說明:`httpkit.py`、`sqlitekit.py` 都列在新節點 `about_code`,節點內用反引號只寫自己家的檔,`Mock-DSP` 以 `[[Systems/共用行程基礎]]` 連結。但 Mock-DSP 內舊的規則與 PITFALL 條目(如忙碌擴充碼、`handle_error` 靜音)仍在原處敘述,與「實作在那一篇」的句子並存,讀者需自行對照;建議下一輪把舊條目改為連結。
file: `docs/rtb-production-agent-demo-knowledge/Systems/Mock-DSP.md:43`
