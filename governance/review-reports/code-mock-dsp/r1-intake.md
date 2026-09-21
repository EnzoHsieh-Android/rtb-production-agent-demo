# code-mock-dsp r1 intake(收貨、重現與處置留痕)

## 收貨三道
- quote-check:七席全數錨定。
- refcheck:c4 引了省略號路徑(`docs/.../Systems/Mock-DSP.md`),屬寫法問題,不影響該 finding。
- 席位:c1 正確性、c2 併發與資源、c3 邊界與輸入、c4 合約與圖譜、cx 外家 Codex(gpt-5.6-sol,唯讀沙盒,報告原樣存檔)、carch 架構對齊、csec 資安。

## 編排者機械重現
| 宣稱 | 命令 | 結果 |
|---|---|---|
| expected_version 傳布林 True 會通過樂觀鎖並改寫廣告(cx1、c1、c3、csec4 多席一致) | python3 直接呼叫 Operation("c1","update_budget",{"new_budget":150},True,"k") | HIT:version_after=2,預算被改成 150 |
| 預算 2**63 造成非型別化 OverflowError(cx2、c1、c3、csec1 多席一致) | 同上,new_budget=2**63 | HIT:OverflowError,不是 DspError |
| 非數字 Content-Length、超長數字、非 DspError 例外會無聲斷線(c1、c2、c3、csec、cx 多席一致) | 修復前由 c3、csec 以 curl 與原始 socket 重現輸出 Empty reply / b'' | HIT(席位重現輸出完整;修復後我加的測試由紅轉綠) |
| 把 BEGIN IMMEDIATE 等處改壞測試會不會紅(c4 的 26 個變異) | 席位報 20 抓到、6 存活 | 未逐個重跑;存活的 6 個對應 c4-F2/F3,已補測試並用我自己的 9 個變異檢查驗證 |
| ERROR_TABLE 以 type() 精確比對(c1、carch) | 席位以 exp.py 重現 | HIT(由席位輸出確認;修復為沿繼承鏈查表並有子類別查得到) |

refuted:none。

## 處置
- 41 條全部折入,沒有放行(依規定:任一席 severity 為 major 以上時不得放行)。
- 折入方式:程式修正加事故測試(78 條測試,先紅後綠;9 個新增防護各做變異檢查,全被抓到;相關不穩定測試連跑 50 次失敗 0 次);其餘寫進 Mock-DSP 圖譜節點的「已知缺口」並附回頭條件。
- 未實作而如實標明:指標計算、50 次判準的自動化、動作表、TID 禁用清單、睡眠式等待。
