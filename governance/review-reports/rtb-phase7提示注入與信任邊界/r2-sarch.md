severity: minor

## 逐問作答

### 問 1:分層與依賴方向

對齊。各增量落的層跟鄰居一致,沒看到跨層直呼:
- 增量 1 的 DSP 用戶端白名單邏輯規劃放在分析端(`src/rtb/analyzer/dsp_client.py`,快照 r2-snapshot.md:76-94),跟既有檔案「只做打 HTTP、轉成 Evidence」的職責一致;快照明講對抗性素材要放 `tests/` 目錄(r2-snapshot.md:164),對照 `tests/analyzer/test_boundaries.py:108-123` 只掃 `src/rtb/analyzer`,對得上。
- 增量 2 只改 `src/rtb/dsp/store.py`(r2-snapshot.md:131-134),對照既有 `src/rtb/executor/dsp_client.py:51-55` 的 `read_campaign` 逐欄取值,額外欄位本來就會被忽略,設計描述與程式行為一致。
- 增量 5 的拒收紀錄規劃在收件處理函式內(r2-snapshot.md:258-261),對照既有 `src/rtb/executor/inbox_server.py:74-77`,同一層、同一個函式。

### 問 2:命名與錯誤處理

大致對齊,有一處空缺(見下方 finding)。
- S200/S201/S202/S217 全部明講「丟出數值錯誤」,對照 `src/rtb/domain/evidence.py:73` 與 `src/rtb/domain/proposal.py:72`,一致。
- S217 的溢位處理對照 `src/rtb/domain/evidence.py:101-107` 既有寫法,一致。

### 問 3:第二種做法

對齊,沒看到引入專案原本沒有的做法:
- 數值上限:專案慣例本來就是每一層各留一份同值常數(`src/rtb/dsp/store.py:51`、`src/rtb/domain/proposal.py:29`、`src/rtb/executor/dsp_client.py:27`),分析端再加一份是延續既有做法。
- 換處理器:對照 `tests/executor/test_execution_e2e.py:23-40` 的子類化模式,同一套做法。
- 寫標準錯誤:對照 `src/rtb/httpkit.py:161-175` 先寫標準錯誤再回應、`tests/kit/test_httpkit.py:215-222` 跨執行緒抓標準錯誤,同一套。

### 問 4:落點合不合理

對齊。lands_in(r2-snapshot.md:300-306)與各篇 about_code 全部對得上,執行側結構測試落「執行迴圈」與既有 `tests/executor/test_executor_boundaries.py:12` 的三支檔一致。

## Findings

### 1. 模擬 DSP 建檔拒收沒點名例外型別

severity: minor
blocking: 否(實作者依同支檔既有慣例幾乎必然照做,屬易於在實作或代碼審階段補上的空缺,不影響設計骨架)
引句:「長度上限 4096 字元,超過就拒絕建檔」
增量 2 與其對應合約 S219 都只說「拒絕建檔」,沒有指明丟哪種例外。同一支檔的鄰居驗證函式拒收一律丟 `ValidationRejected`,而快照在領域層的每一條新合約都刻意寫「丟出數值錯誤」,唯獨這條沒有對應說法。
佐證:file: `governance/review-reports/rtb-phase7提示注入與信任邊界/r2-snapshot.md:132`;file: `src/rtb/dsp/store.py:179`;file: `src/rtb/dsp/store.py:153`

不對齊共 1 條,其中 major 0 條。
