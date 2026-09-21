# code-static-gate r1 intake(收貨、重現與處置留痕)

## 收貨三道
- quote-check:兩席全數錨定;refcheck:s1 引了示範路徑 `src/x.py`(席位實測時自己造的假路徑,不是真檔案),不影響 finding。
- 席位:s1 通才單一審查員(做了 42772 組新舊差異測試等大量實驗)、sarch 架構對齊(標準強度;外家席依編制退同門並留痕,本輪沒派外家)。

## 編排者重現與驗證
| 宣稱 | 做法 | 結果 |
|---|---|---|
| 「型別重構行為沒有改變」成立(s1) | 席位以新舊版差異測試回報:metrics 42772 組、task_state 4232 組、parse_proposal 6 萬組,0 筆不同 | 採信(席位輸出完整);測試全套仍全過 |
| s1f1:lint.json 的命令沒有 {LINT_FILES},lumos 新增告警閘整條跳過 | 我用 lumos 自己的 `_lint_new_verdict` 在隔離複本重現 | HIT。**而且我先前把 `pitfalls` 顯示的 `[lint:mypy]` 與 tier high 說成「lumos 會擋」,那是風險分級不是閘,說過頭了** |
| 修法驗證:加上 {LINT_FILES} 後新增型別錯誤是否被擋 | 同一個判定函式,案例 1 放進 `def broken_probe(x: int) -> str: return x` | **第一次結果是 clean(沒抓到)**:發現第二個 bug——lumos 把檔案抽到 `.lumos/lintbase-*/base\|head/` 臨時目錄再傳給命令,我的 `--only-configured-paths` 用 `src/` 前綴判斷,把所有檔案當成範圍外,mypy 根本沒跑。修正(比對前先去掉臨時前綴)並加測試後,重跑結果:案例 1 blocked(指出 `src/rtb/domain/_checks.py:29` return-value)、案例 2(只改測試檔)no-files 不誤擋、案例 3(tools 新增 ruff 違規)blocked |
| s1f3:沒有欄位號的 mypy 錯誤被靜默丟掉 | 席位以 unused-ignore 重現輸出完整 | 採信;修復後有測試 |
| s1f4:CI 接線測試可被 echo、if: false、分支限制繞過,多行 run 區塊誤紅 | 席位以複本重現(5 passed) | 採信;測試改為自寫的剖析器並有守衛的守衛測試 |
| s1f7:requirements-dev.txt 漏 ast_serialize | 席位 pip show 與 manylinux 下載驗證 | 採信;改為完整凍結 |

refuted:none。

## 處置
- 13 條全部折入,沒有放行(s1 為重要等級,規定不得放行)。
- 折入方式:程式修正加測試(368 條);lint 宣告補上 {LINT_FILES};轉換器加欄位選填、URI 編碼、範圍過濾與臨時前綴處理;接線測試重寫;CI 加權限並移除重複觸發;圖譜補 PITFALL 與帶 REVISIT 的已知缺口。
