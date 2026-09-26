severity: minor

# 代碼審 r1 資安席(opus)— Phase 14 增量 2a

範圍:`r1-snapshot.patch`(LUMOS-IMPACT: origin/main..HEAD)。只看能被利用的洞。
威脅模型:DSP 只綁本機且檢查 Host 標頭;分析端讀取層收 DSP 回應;廣告名稱是唯一的不可信文字。

## 逐類檢查

**1. 不可信輸入流到危險操作:已看,無可利用的洞**
- 新 SQL 全部參數化,沒有字串拼接:
  - 日期桶的查詢、刪除與寫入(`_refresh_windows`、`_prune_daily`、`_materialize_daily`、`_three_days`)
  - 調整紀錄(`_latest_raise`、`history_limited`)
  - 遷移裡的 `ALTER`、`DROP`、`CREATE` 用的是寫死的表名
- 遷移裡的 `json.loads(params_json)` 讀的是自家資料庫。
- DSP 回應進讀取層仍然走白名單:
  - 新增的 `committed_at` 要通過 `fromisoformat` 並帶時區。
  - `summary` 的鍵集合固定,值是非負整數加一個布林,還做了大小關係檢查。
  - `check_history` 雖然改成回傳整個 `body`,但前面已限定頂層只能是 `{history}` 或 `{history, summary}`。
  - 以上都進不了自由文字。所以收據和提示裡不會多出新的不可信文字。
- 廣告名稱的處理路徑沒改。
- 唯一放寬的是金額欄位可以收字串,見發現 1。

**2. 登入與權限:已看,無**
- 寫入仍走 `_authorized_write`,這次沒改。
- 讀取端點本來就不驗證身分,只靠本機綁定;這點沒變。
- `/history` 新增的 `summary` 範圍跟原本的 history 一樣,都是單一廣告,沒有跨租戶。

**3. 密鑰與個資:已看,無**
- 新錯誤訊息 `金額不合法:{value!r}` 只出現在 DSP 本機的驗證拒收,內容是金額,不是秘密。
- 讀取層的錯誤仍然只列欄位名,不列欄位值。

**4. 加密與傳輸:已看,無**
- 傳輸方式沒變動,仍是本機回送的 HTTP。

**5. 執行邊界:已看,無**
- 沒有動到 hook、腳本、CI,也沒有 shell 插值。

**新依賴:無**
- 只多了標準庫的 `re` 和 `decimal`。

## 發現 1:讀取層金額字串白名單用 Unicode `\d` 且長度不限,可繞過有限值檢查(推論)
severity: minor
blocking: 否

引句:「_MONEY_PATTERN = re.compile(r"-?\d+\.\d{2}\Z")」
file: `src/rtb/analyzer/dsp_client.py:75`

引句:「metrics[name] = float(metrics[name])」
file: `src/rtb/analyzer/dsp_client.py:157`

**問題**
- Python 的 `\d` 會比對到非 ASCII 數字(例如阿拉伯—印度數字)。
- `\d+` 沒有長度上限。
- 在基本讀取路徑上,字串會被 `float()` 轉成數字。一個 400 位數的字串會變成 `inf`(本機實測 `float('1'*400+'.00')` 得到 `inf`)。
- 結果是原本 `is_finite_or_none` 擋下的非有限值,換成字串形式就能進可信證據。
- 對照:`nine_rules.py` 的同類檢查用的是 `[0-9]`,兩邊寬嚴不一。

**攻擊路徑(推論)**
- 前提:攻擊者要能控制 DSP 的回應。但 DSP 只綁本機且檢查 Host 標頭,而金額欄是 DSP 的可信欄位。
- 廣告名稱這類不可信文字進不到這一欄。
- 所以沒有外部入口能送出這種值,屬於縱深防禦。

**建議**
- 改用 `[0-9]{1,15}`,或用 `re.ASCII`。
- 轉成數字後再用 `math.isfinite` 驗一次。

## 總結
新 SQL 全部參數化,DSP 新欄位維持嚴格白名單,沒有讓不可信文字流進收據或提示的新路徑。只有一條金額字串驗證偏寬的縱深防禦建議,不阻擋推送。
