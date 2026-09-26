severity: clean

# 代碼審 r2 資安席(opus)— Phase 14 增量 2a

範圍:`r2-delta.patch`(修正差異),對照 `r2-snapshot.patch`(LUMOS-IMPACT: origin/main..HEAD)。只看能被利用的洞。
威脅模型沿用 r1:DSP 只綁本機且檢查 Host 標頭;分析端讀取層收 DSP 回應;廣告名稱是唯一的不可信文字。

## 驗收 r1 發現 1(金額字串白名單太寬):已修好

- 判準收斂成一份,放在領域層 `_checks`。讀取層、指標層、九條三處都改呼叫它,原本 `dsp_client` 與 `metrics` 的兩套 `\d` 寫法已刪除。
- 新判準做了三件事:
  - 只收 ASCII 數字(`[0-9]` 加上 `re.ASCII`)
  - 整數部分最多 13 位
  - 不收多餘的前導零

引句:「_FIXED_AMOUNT = re.compile(rf"-?(?:0|[1-9][0-9]{{0,{MAX_AMOUNT_WHOLE_DIGITS - 1}}})\.[0-9]{{2}}",」
file: `src/rtb/domain/_checks.py:28`

- 基本讀取路徑只在過了判準之後才轉浮點。13 位整數加 2 位小數是 15 位有效數字,不可能轉成 `inf`。

引句:「metrics[name] = fixed_amount_float(metrics[name])」
file: `src/rtb/analyzer/dsp_client.py:146`

- 本機實測結果如下:

  | 輸入 | 結果 |
  |---|---|
  | 阿拉伯—印度數字 `١٢.٣٤` | 拒 |
  | 14 位整數 | 拒 |
  | `01.00` | 拒 |
  | 結尾帶換行的 `1.00\n` | 拒 |
  | `1e3` | 拒 |
  | `9999999999999.99` | 收,轉浮點後 repr 仍是原值 |

- DSP 寫入端 `cents_of` 用同一條規則(`src/rtb/dsp/store.py:91`),上限 `MAX_CENTS`,兩邊寬嚴一致。
- 帶負號的字串仍能過白名單,但指標層與收據都把它當成「不合理」寫 na(`metrics.py` 的 `_exact_input` 與 `receipt_amount`),不會變成可信的正數。

## 逐類檢查(新修正)

**1. 不可信輸入流到危險操作:已看,無**

- **讀取改用只讀快照**
  - `read_snapshot` 是 `BEGIN` 加一次讀取,結束時 `ROLLBACK`,不接收任何外部輸入。
  - 快照裡的 SQL 都參數化,包括 `_buckets`、`_newest_day`、`_template`、`history_limited` 的計數與列。
  - 日期參數來自 `date.isoformat()`,不是請求內容。
- **時鐘注入**

  引句:「store_clock: Callable[[], str] | None = None):」
  file: `src/rtb/dsp/server.py:350`

  - 只有建構子參數能注入。正式入口 `main()` 建構 `DspServer` 時沒傳它(`src/rtb/dsp/server.py:390`)。
  - 命令列參數、環境變數、HTTP 標頭或 fault 模式都碰不到它。
  - 外部請求因此無法左右 DSP 看到的「今天」或提交時間。
- **金額集中規則**:見上方驗收。
  - 浮點路徑沿用 `is_finite_or_none`,沒有放寬。
  - DSP 回的 1d/7d 加總在讀取時用 Python 整數算,不寫回資料庫。超過 13 位時分析端判準拒收、記 invalid,不會溢位進可信證據。
- **遷移補值表**
  - `operation_budget_backfill` 只在 `_migrate_budget_before` 裡寫入,且用參數化的 `INSERT OR IGNORE`。
  - 表名寫死,`json.loads` 讀的是自家資料庫。
  - 沒有任何 HTTP 端點能寫入這張表。
  - 讀取端用 `LEFT JOIN` 加 `COALESCE`,同樣參數化(`src/rtb/dsp/store.py:757`)。
  - 讀取層白名單因此放寬成 `budget_before` 可以是 null(`src/rtb/analyzer/dsp_client.py:245`)。這只擴大了可信欄位的值域,不是自由文字,也進不了提示。
- **歷史 truncated 旗標**
  - 讀取層只接受兩種頂層形狀:恰好 `{history}`,或 `{history, summary, truncated}`。
  - `truncated` 必須是 `True`,列數必須恰好 50。
  - 摘要要通過 `_summary_agrees` 的自洽與下界檢查(`src/rtb/analyzer/dsp_client.py:327`)。
  - 回傳的是重新組出來的 dict,不是原始 body,多餘的鍵帶不過去(`src/rtb/analyzer/dsp_client.py:324`)。
  - 收據裡新增的是寫死的字串:

    引句:「"history_truncated": "true"}」
    file: `src/rtb/analyzer/investigation.py:254`

  - 其餘計數都經過 `receipt_count` 處理,全是非負整數。
  - 不可信文字沒有新路徑能進收據或提示。
- **廣告名稱**:處理路徑沒改。評估集裡帶注入字樣的名稱是既有的測試案例(依規則不報)。

**2. 登入與權限:已看,無**

- 寫入授權沒改。
- `/history` 回應形狀的調整仍限在單一廣告,沒有跨租戶。
- `seed_metrics` 新增的拒收只影響展示種子,不影響權限。

**3. 密鑰與個資:已看,無**

- `cents_of` 的錯誤訊息會帶出輸入的金額值(`{value!r}`),但那是 DSP 本機驗證時的拒收訊息,內容是金額,不是秘密,符合 R16。
- 讀取層錯誤仍只列欄位名,不列欄位值。

**4. 加密與傳輸:已看,無**

- 傳輸沒有變動。

**5. 執行邊界:已看,無**

- 沒有動到 hook、腳本或 CI,也沒有 shell 插值或 `eval`。
- 反序列化只有 `json.loads` 讀自家資料庫,符合 R18。

**新依賴:無**

- 只多了標準庫的 `fractions.Fraction` 與 `decimal.Context`。

## 總結
r1 提出的金額字串白名單太寬已經修好:改成 ASCII、限 13 位、判準只有一份。這次的新修正(只讀快照、時鐘注入、遷移補值表、截斷旗標)沒有外部可控的入口,也沒有讓不可信文字流進收據或提示。沒有新發現。
