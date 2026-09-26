severity: major

## 發現 1: UTC 午夜可讓逐日回應與 7d 視窗互相矛盾
severity: major
blocking: 是

`get_daily()` 先由 `_materialize_daily()` 讀一次時鐘，再讀一次時鐘決定七日範圍。若午夜落在兩次讀取之間，物化仍按舊日期判定「不用補桶」，回應卻按新日期產生：昨天變成 `no_data`，7d 視窗仍是舊七日總量，違反 [S1426]。位置：`src/rtb/dsp/store.py:604`、`src/rtb/dsp/store.py:605`。

引句:「+        today = _moment(self._clock()).astimezone(UTC).date()」

最小重現（只用記憶體資料庫）：
```sh
PYTHONPATH=src .venv/bin/python -B -c '
from pathlib import Path
from datetime import UTC, datetime, timedelta
from rtb.dsp.store import CampaignStore
a = datetime(2026, 9, 26, 23, 59, 59, tzinfo=UTC)
b = a + timedelta(seconds=2)
ticks = iter([a, b])
s = CampaignStore(Path(":memory:"), clock=lambda: next(ticks).isoformat())
s.seed_campaign("c", 100)
day = lambda n: {"impressions":100, "clicks":10, "conversions":n, "spend":"1.00", "revenue":"2.00"}
s.seed_daily("c", [day(2)] + [day(1)] * 6, now=a)
rows = s.get_daily("c")
print(rows[0].no_data, sum(r.conversions or 0 for r in rows),
      s._conn.execute("SELECT conversions FROM metrics WHERE window_name=\"7d\"").fetchone()[0])
'
```
實際輸出：`True 7 8`。應以同一個 UTC 日期完成物化與投影。

## 發現 2: 兩個首次跨日讀取可因保留期清理而使 GET 拋出 `TypeError`
severity: major
blocking: 是

`_materialize_daily()` 在交易外先記住最新日桶；另一個讀取者若先完成超過 30 日的補桶與清理，該舊桶便已刪除。原讀取者取得寫鎖後仍用舊日期查 `newest_row`，得到 `None`，展開時直接拋例外。這會發生在資料庫停用一段時間後兩個請求同時首次讀取。位置：`src/rtb/dsp/store.py:581`、`src/rtb/dsp/store.py:590`、`src/rtb/dsp/store.py:598`。

引句:「+                    (campaign_id, newest.isoformat(), *newest_row))」

以記憶體資料庫在「讀出舊日期、取得寫鎖」之間插入另一個物化，已重現：
```sh
PYTHONPATH=src .venv/bin/python -B -c '
from pathlib import Path
from datetime import UTC, datetime, timedelta
from contextlib import contextmanager
from rtb.dsp.store import CampaignStore
old = datetime(2026, 8, 1, 12, tzinfo=UTC)
at = [old]
s = CampaignStore(Path(":memory:"), clock=lambda: at[0].isoformat())
s.seed_campaign("c", 100)
day = {"impressions":1, "clicks":1, "conversions":1, "spend":"1.00", "revenue":"1.00"}
s.seed_daily("c", [day] * 7, now=old)
at[0] = old + timedelta(days=40)
original = s._seed_transaction
@contextmanager
def collide():
    s._seed_transaction = original
    s._materialize_daily("c")
    s._seed_transaction = collide
    with original():
        yield
s._seed_transaction = collide
s.get_daily("c")
'
```
實際結果：`TypeError: Value after * must be an iterable, not NoneType`。應在取得寫鎖後重新讀取最新日期及來源桶。

## 發現 3: 合法舊 REAL 金額可使資料庫升級失敗、DSP 無法啟動
severity: major
blocking: 是

舊版金額驗證接受任何有限 `float`；新版升級把每筆舊金額送入預設精度的 `Decimal.quantize()`。例如舊版合法的 `1e30` 會拋出未處理的 `InvalidOperation`，使整個 `_migrate_columns()` 回滾並讓 `CampaignStore` 建構失敗。位置：`src/rtb/dsp/store.py:277`、`src/rtb/dsp/store.py:431`。

引句:「+    rounded = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)」

最小重現：
```sh
PYTHONPATH=src .venv/bin/python -B -c 'from rtb.dsp.store import _legacy_cents; print(_legacy_cents(1e30))'
```
實際結果：`decimal.InvalidOperation: [<class 'decimal.InvalidOperation'>]`。需要為超出新分值範圍的既有資料訂明可啟動的遷移結果，或在升級前給出可處理的資料錯誤。

## 發現 4: 基本 1h 讀取把精確分值轉回浮點，改變金額收據
severity: minor
blocking: 否

DSP 已把金額回成固定兩位字串，但基本證據入口立即轉為 `float`。有效分值 `90071992547409.93` 會變成 `90071992547409.94`，使基本收據與 DSP 原始事實差一分；這也反駁了新增整數分路徑能端到端保持金額精確的宣稱。位置：`src/rtb/analyzer/dsp_client.py:154`。

引句:「+                metrics[name] = float(metrics[name])」

最小核對：
```sh
PYTHONPATH=src .venv/bin/python -B -c 'from rtb.domain import metrics; x="90071992547409.93"; print(x, float(x), metrics.receipt_amount(float(x)))'
```
實際輸出：`90071992547409.93 90071992547409.94 90071992547409.94`。

## 發現 5: 單日合法整數可能在七日彙總時溢位
severity: minor
blocking: 否

`_checked_metric()` 允許每個日桶計數達 SQLite 整數上限，但 `_refresh_windows()` 對七日直接相加後寫回同一種 `INTEGER` 欄位。兩個合法日桶的合計即可超界，`seed_daily()` 拋出原始 `OverflowError`。位置：`src/rtb/dsp/store.py:573`、`src/rtb/dsp/store.py:575`。

引句:「+                fields.append(sum(values) if values and all(v is not None for v in values)」

以七個 `impressions=2**63-1` 的日桶呼叫 `seed_daily()`，實際在 `INSERT OR REPLACE INTO metrics` 得到 `OverflowError: Python int too large to convert to SQLite INTEGER`。應在彙總前驗證可儲存範圍並回一致的資料拒收結果。

## 發現 6: [S1108] 仍聲稱單查逐日要三讀，與程式及租約算式矛盾
severity: minor
blocking: 否

本次修改後，`[S1108]` 寫「逐日選項另讀 1d、7d，共三讀」，同一份計劃的 2a 修正、`READS_PER_OPTION`、新增測試及實作卻都明確規定單查逐日只讀一次。這是會誤導後續租約上界修改的現行合約矛盾。位置：`docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase13AI參與決策_計劃.md:673`；相反條款在 `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase14正式規則照九條判斷_計劃.md:127`。

引句:「+ - [S1108] 當模型選查詢選項時,分析端只呼叫該選項需要的唯讀端點、不呼叫寫入端點;Phase 14 增量 2a 起逐日選項為核對跨窗另讀 1d 與 7d,同一選項共三讀,其餘對應不變。」

## 圖譜固定席判定

| 節點 | 判定 |
|---|---|
| 分析行程流程與檢查點 | F5 不可信文字與任務提交圍籬未被此 diff 改動；新增讀取契約受發現 1、4 影響。 |
| 正式九條判斷領域規則 | 本次收緊字串金額格式，未改九條判定；正式接線仍屬後續增量。 |
| 確定性指標計算 | 「算不出不冒充零」的不變量仍成立；其精確金額收據的輸入在發現 4 的基本讀取邊界已失真。 |
| Mock-DSP | 既有冪等、原子寫入、F1 逾時與版本拒收路徑未見破壞；新增跨日讀取與舊庫升級受發現 1–3、5 影響。 |
| 評估與Jev決策點 | 比對評估集全部 46 組修改行，移除新增的 48 個 `committed_at` 後其餘資料相同；未見評分行為改動。 |
| 提案收件口 | 收件、重放與修訂檢查程式未變，未見其合約被直接破壞。 |
| 共用行程基礎 | HTTP 標頭封閉清單、重新導向與故障注入邊界未變。 |
| 任務流程領域模型 | 證據新鮮度、狀態轉換及領域匯入邊界未變。 |
| 執行迴圈 | 執行、對帳、核可與曝險守衛未變；上述資料讀取問題尚未構成繞過執行閘的證據。 |

驗證限制：單支 pytest 在此唯讀沙箱啟動時，因 `tests/conftest.py` 必須建立暫存目錄而失敗；上述最小重現均已用不寫檔的記憶體資料庫或純函式執行。