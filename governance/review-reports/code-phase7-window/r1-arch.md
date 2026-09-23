severity: minor

# 架構對齊審查——code-phase7-window r1-snapshot.patch

## 一、分層與依賴方向

這支 diff 只動 `src/rtb/analyzer/dsp_client.py`（分析端 DSP 用戶端）與對應測試，沒有新增對其他層的直接呼叫。`REQUESTED_WINDOW` 定義在同一支檔案內，同時餵給組請求路徑（`metrics_path = f"/campaigns/{task.campaign_id}/metrics?window={REQUESTED_WINDOW}"`，`src/rtb/analyzer/dsp_client.py:143`）與白名單檢查（`METRICS_FIELDS["window"]`，`src/rtb/analyzer/dsp_client.py:76`），沒有跨層直呼 `src/rtb/dsp/store.py`（模擬 DSP 那一層的 `METRIC_WINDOWS`）或 `src/rtb/analyzer/policy.py`。依賴方向跟改動前一致：dsp_client 只讀 `rtb.domain.*`、`rtb.httpclient`，沒有反向依賴。這一問沒有不對齊。

## 二、命名與錯誤處理

常數命名 `REQUESTED_WINDOW` 延續既有 `SCREAMING_SNAKE_CASE`（比照 `CAMPAIGN_STATUSES`、原本的 `METRIC_WINDOWS`），錯誤路徑完全沒動：`_trusted()` 仍是欄位不合格就整批塞進 `bad` 清單、丟 `DspRequestFailed`（`src/rtb/analyzer/dsp_client.py:89-95`，未改），跟 `budget`、`status`、`version` 等既有欄位的失敗語意一致。

但白名單檢查本身的寫法跟同檔其他字串欄位不一致，見 F1。

## 三、第二種做法(同一個「1 小時」現在有幾處各自定義)

現況數了一下,分析端有兩處各自寫「只認 1 小時」這件事,且互不引用:
- `src/rtb/analyzer/policy.py:30` `ELAPSED_FRACTION_1H = 1 / 24`（配速換算用,把「這個增量只讀 1 小時窗」寫死成分母 24）
- `src/rtb/analyzer/dsp_client.py`(本次新增) `REQUESTED_WINDOW = "1h"`（請求與白名單用的字串）

這兩個常數是同一個業務事實(「這個增量只服務 1 小時窗」)的兩份獨立編碼,型別不同(分數 vs 字串)所以不能直接合併成一個常數,但也沒有任何機制保證改一邊時另一邊會被連帶檢討——是本次審查範圍內唯一稱得上「同一件事兩處定義」的地方,詳見 F2。這不是這次 diff 造成的新分裂(`ELAPSED_FRACTION_1H` 本來就在),但 diff 把 `dsp_client.py` 這邊從「裸字串常數收 3 值」變成「具名常數收 1 值」之後,兩邊語意上更貼近彼此,drift 風險也更值得記一筆。

除此之外,`src/rtb/dsp/store.py:57` 的 `METRIC_WINDOWS`（1h/1d/7d 的完整清單）是模擬 DSP 伺服端在存值,跟分析端「只要 1h」是不同層級的事(伺服端本來就該收多種窗),不算重複定義。

---

## F1 白名單欄位檢查寫法跟同檔慣例不一致

severity: minor
blocking: 否 — 功能正確、不影響行為，只是跟同檔其他字串類型的白名單欄位少了一層顯式型別檢查，屬風格一致性問題
引句:「"window": lambda value: value == REQUESTED_WINDOW,」

同檔 `status` 欄位維持 `isinstance(value, str) and value in CAMPAIGN_STATUSES` 這種「先顯式驗型別、再驗集合成員」的慣例（未改動、比對 `git -C /Users/enzo/rtb-3b show 004a7de:src/rtb/analyzer/dsp_client.py` 裡 `STATE_FIELDS["status"]`），`window` 改成單純 `== REQUESTED_WINDOW` 之後少了顯式 `isinstance` 這一步。功能上等價（跟字串常數相等本來就隱含型別判斷），但跟同檔並列的字串欄位檢查風格不一致。

## F2 「1 小時」在分析端有兩處各自定義、互不引用

severity: minor
blocking: 否 — 兩者型別不同（分數 vs 窗口字串）、用途不同層（配速換算 vs HTTP 請求／白名單），不是同一段邏輯被複製，只是同一個業務事實缺共同來源，非本次 diff 直接造成
引句:「用戶端只要 1 小時的指標(示範規則也只按 1 小時換算);回應的時間窗跟請求的不同就當可信欄位不合格」

`policy.py:30` 的 `ELAPSED_FRACTION_1H = 1 / 24` 早於本次 diff 就已經把「這個增量只讀 1 小時窗」寫死一次；這次 diff 又在 `dsp_client.py` 新增 `REQUESTED_WINDOW = "1h"` 承載同一個業務事實。兩邊沒有互相引用或共同來源，未來若示範規則改成讀別的窗口，容易漏改其中一邊。不是這次 diff 引入的新分裂（`ELAPSED_FRACTION_1H` 本來就存在），但 diff 讓 `dsp_client.py` 這邊的編碼更明確、更像是「同一件事」，值得留意。

---

不對齊共 2 條,其中 major 0 條。

⚠ 交編排者：F2 是否夠格升級為 major（要求兩個常數共用單一來源，例如 policy.py 匯出一個 `REQUESTED_WINDOW` 常數供 dsp_client.py 引用），還是維持現狀（兩層各自擁有各自的表示法，屬合理分工），這個判準沒有在既有程式或 skill 裡找到明確先例，交編排者裁定。
