severity: clean

# 代碼審 r3 資安席(opus 頂替)— Phase 14 增量 2a

被審:r3-delta.patch(對照 r3-snapshot.patch)。立場:攻擊者視角,只找能被利用的洞;不報 DoS／資源耗盡。專案資安 linter 輸出:無。

本輪沒有發現。以下是逐類的推演過程,每類都寫出試過的攻擊路徑,以及它為什麼走不通。

## 1 不可信輸入流向危險操作

- 截斷歷史的近期核對 `history_recent_agrees`(file: `src/rtb/analyzer/dsp_client.py:497`)
  - 攻擊者:能改寫 DSP 回應的一方。
  - 手法:送 truncated=true 的歷史,摘要把 `budget_changes_last_3d` 寫成 0,讓收據繞過第 3 條。
  - 結果:走不通。回傳列裡的近期預算調整會拿讀取時刻 now 重算一次,摘要比它少就整份判 invalid。
  - 時鐘:用的 now 就是收據那個 now(file: `src/rtb/analyzer/instrumented.py:151`)。
- 格式錯誤的 `committed_at`
  - 手法:故意給不帶時區或無法解析的時間字串,想讓 aware 與 naive 比較時丟 TypeError,把這一步卡住。
  - 結果:走不通。`_aware` 在白名單階段就先擋掉了(file: `src/rtb/analyzer/dsp_client.py:266`)。
- 反向操作:多報近期筆數
  - 結果:只會讓第 3 條判得更保守,不會促成錯誤的加額提案。
- 摘要總數的核對
  - 總數改成「加總等於」核對(file: `src/rtb/analyzer/dsp_client.py:347`),少報總筆數的路徑也封住了。
- 殘餘路徑:DSP 回 50 筆不是最近的舊列,再配一份自洽的摘要
  - 這條需要 DSP 本身說謊。DSP 本來就是事實來源,能讓它說謊的攻擊者可以直接改狀態與指標,這條路徑沒有多給他任何權限。不列為發現。
- 反序列化與 shell(R18)
  - 新增的程式只用 `datetime.fromisoformat` 處理已驗過的字串。
  - 沒有 pickle、eval、subprocess。
  - SQL 仍是參數化綁定,遷移的 INSERT 用 `*values` 帶入 ? 佔位(file: `src/rtb/dsp/store.py:352`)。

## 2 登入與權限

- 本輪沒有碰能力金鑰與稽核金鑰的比對路徑。
- `CampaignStore` 的時鐘預設值改成延後查詢(file: `src/rtb/dsp/store.py:108`)。正式啟動不注入時鐘,行為跟改之前一樣。
- 測試裡在同一行程起的 `DspServer` 沒給 capability_key,所有寫入一律拒收。沒有開出新的寫入入口。

## 3 密鑰與個資(R16)

- 新增的 `ValidationRejected` 訊息只帶欄位名稱(file: `src/rtb/dsp/store.py:341`)。
- 沒有值、金鑰、租戶資料進入例外訊息或 log。

## 4 加密與傳輸

- 沒有新增對外連線,也沒有改 TLS 或憑證處理。
- 測試伺服器只連 127.0.0.1。

## 5 執行邊界(測試裡自動生效的設定)

- 新的 autouse 夾具(file: `tests/dsp/test_investigation_data.py:47`)只在本檔範圍內 monkeypatch `_utc_now`。
- 它不讀環境變數與外部檔案,也不下載、不執行外部內容。在 CI 自動生效不會執行任何不可信的東西。
- 新的 HTTP 段在同一行程起伺服器,用 daemon 執行緒,結束時會 shutdown。

## 新依賴

- `origin/main..HEAD` 範圍內 pyproject 與 lock 檔都沒有變動,沒有新增第三方套件。

## 其餘新增邏輯

- 日桶每天的上限(`DAILY_MAX_CENTS`、`DAILY_MAX_COUNT`,file: `src/rtb/dsp/store.py:96`)
  - 效果:把七天合計收在讀取白名單上限內,避免「存得進、讀不出」。這是完整性的強化。
- 遷移時超過上限的值記缺值(file: `src/rtb/dsp/store.py:352`)
  - 輸入來源是本機既有的資料庫,沒有外部攻擊者能控制的入口。

## 總結

這一輪的修正都朝收緊的方向:截斷摘要改成用讀取時刻核對下界、總數要求相等、寫入時限制每天的上限。沒有找到能被外部攻擊者利用的路徑,也沒有新依賴。
