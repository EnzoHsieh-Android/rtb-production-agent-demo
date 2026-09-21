# code-metrics r2 intake(收貨、重現與處置留痕)

## 收貨三道
- quote-check:n1 全數錨定;narch 有 1 句引句(「這一層用「結果型別」…」)少於 10 字錨不到,該句不採信,其 finding 另有錨定引句,仍採信。
- refcheck:narch 引了 `src/rtb/agent/x.py`(席位為實測而造的假路徑,不是真檔案),不影響 finding。
- 這一輪是驗收輪,只審第一輪修復的差異(r2-snapshot.patch,555 行)。席位:n1 修復差異審查員、narch 架構對齊。

## 編排者機械重現(修復前的程式碼)
| 宣稱 | 命令 | 結果 |
|---|---|---|
| n1f1:pacing(1, 10**400, 0.5) 仍丟 OverflowError | python3 直接呼叫 | HIT:OverflowError(修復前);修復後有測試,改壞會紅 |
| n1f2:pacing(1.0, 1e-200, 1e-200) 被說成分母為零 | python3 直接呼叫 | HIT:reason=no_denominator(修復前);修復後改為 invalid_data |
| n1f4:`//evil.example/campaigns/c1` 這類路徑會被路由 | 真行程收原始 socket 請求(新增測試) | MISS n1f4:標準函式庫在解析請求時已把開頭多個斜線收合成單斜線,`//evil.example/...` 變成 `/evil.example/...` 找不到而回 404;席位是對原始字串直接呼叫 urlsplit,真行程走不到 |
| n1f5:--config 或 --isolated 會讓 domain/ruff.toml 無聲失效 | 席位以 ruff 實測輸出完整 | 採信;新增測試檢查 lumos 的 lint 指令不含這兩個旗標 |
| n1f3:REAL 欄位整數讀回為浮點、大整數失精度 | 席位以 s.py 重現輸出完整 | 採信;修復後金額整數限制在 2**53,並有測試 |

refuted:n1f4(真行程重現不到,理由見上表;仍新增一條測試鎖住真實行為)。

## 處置
- 9 條折入、1 條(n1f4)重現不到;沒有放行(n1 為重要等級,規定不得放行)。
- 折入方式:程式修正加測試(188 條);新增防護做 7 個變異檢查,全被抓到;其餘寫進兩篇圖譜節點(兩種錯誤慣例的 WHY、禁用清單不完整與 --config 繞過、整數浮點混用的極端輸入取捨、REAL 欄位語意)。
- 附帶發現:我在處理 n1f4 時先加了一段 `startswith("//")` 檢查,實測發現它永遠不會觸發(標準函式庫已先收合),當作死碼移除。
