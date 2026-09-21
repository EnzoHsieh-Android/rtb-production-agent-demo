severity: major

### 1. 收件口自建 HTTP 伺服器與 SQLite 儲存,未說明共用 DSP 既有做法
severity: major
blocking: 是 設計寫「自建最小的本機 HTTP 收件口」,S7 又重述回送綁定與 Host 檢查、S4/S5 重述 typed 4xx/503;DSP 已有 ThreadingHTTPServer、私有 _check_host、RequestRejected(status, code, retryable)、DspError 階層、WAL、BEGIN IMMEDIATE。設計沒有指名共用或抽出這些,實作會落成第二套 HTTP 處理與第二套 SQLite 存取與錯誤型別,屬第二種做法。
引句:「自建最小的本機 HTTP 收件口(標準函式庫),不引入佇列軟體」
file: `src/rtb/dsp/server.py:17`
file: `src/rtb/dsp/server.py:78`
file: `src/rtb/dsp/server.py:137`
file: `src/rtb/dsp/store.py:178`
file: `src/rtb/dsp/store.py:251`
file: `src/rtb/dsp/errors.py:8`

### 2. 新增模組沒有 Systems 節點當家
severity: major
blocking: 是 CLAUDE.md 規定每支程式檔要有 Systems 節點列入 about_code。設計聲稱「只新增檔案」,卻沒列出新檔(收件口伺服器、收件表儲存、雜湊函式),lands_in 只有「任務流程領域模型」,該節點管領域三個模組,不含伺服器與儲存,也沒有新開節點的計劃;新檔提交前會被每支檔有家的檢查擋下。
引句:「因為這個增量只新增檔案、沒有修改既有介面;回退就是刪掉新增的模組與測試」
file: `CLAUDE.md:57`
file: `docs/rtb-production-agent-demo-knowledge/Systems/任務流程領域模型.md`
