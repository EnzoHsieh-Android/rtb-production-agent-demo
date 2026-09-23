severity: clean

這次修正(r2-delta.patch)只動 `tests/analyzer/test_boundaries.py` 與兩篇知識圖譜筆記,沒有動任何 `src/` 下的正式程式碼,不構成跨層問題。

「不准匯入網路模組、不准動態匯入」那段邏輯確實被抽成共用函式,但只是把原本內嵌在 `test_the_analyzer_reaches_the_network_only_through_the_shared_client` 迴圈裡的判斷原封不動搬進 `_network_offenders(label, tree)`,呼叫端從
逐字引句:「for node in ast.walk(ast.parse(file.read_text(encoding="utf-8")))」
改成
逐字引句:「offenders += _network_offenders(file.name, ast.parse(file.read_text(encoding="utf-8")))」
比對兩段邏輯(欄位判斷、動態匯入偵測、`NETWORK_MODULES` 命中規則)逐字相同,只是把 `file.name` 換成參數 `label`、把 `ast.parse(...)` 換成參數 `tree`,是單純的抽取重複程式碼(同一種做法給舊測試與新掃描 `write_call_offenders` 共用),沒有引入第二種判斷邏輯,舊測試 `test_the_analyzer_reaches_the_network_only_through_the_shared_client` 的行為(哪些檔案被掃、哪些字串算命中)沒有改變。

閉包加入父套件初始化檔的寫法(`analyzer_import_closure` 裡新增的
逐字引句:「pending += [".".join(parts[:i]) for i in range(1, len(parts))]  # 沿途的父套件」
)是在原本就存在的同一個 BFS/DFS 收集迴圈裡多推一步:對每個被彈出的模組,把它的所有祖先套件名（不含自己）也丟進 `pending`,交給同一套 `_source_of` / `ast.parse` / `closure[module]=...` 流程處理,沒有另開一條平行路徑。原本用「`path = None if module in closure else _source_of(...)`」跳過已收錄模組、改成先 `if module in closure: continue`,效果等價(已收錄的模組不會被重新解析),只是把「跳過」邏輯搬到迴圈最前面以便在跳過之前先把父套件塞進 `pending`——這是同一個閉包演算法內部的微調,不是新開一套機制。這支函式與 `_source_of`/`_imported_modules`/`_resolve_from` 仍只有一份實作,`write_call_offenders`(新掃描)與 `analyzer_import_closure` 本身共用,沒有在別處(生產碼或其他測試檔)另外重刻一份閉包邏輯或父套件收集邏輯(用 `grep -rn "analyzer_import_closure\|_network_offenders\|沿途"` 核對過,只有這一份定義與呼叫)。

新增的三個自我驗證變體(`package_init`、`domain_package_init`、`domain_urllib`)都是餵給既有的 `write_call_offenders` 掃描去驗證,沒有另外寫一套平行的驗證機制,跟第 1 輪收貨紀錄裡外家席指出的兩個洞(套件初始化檔沒進閉包、領域層繞過共用用戶端直接發請求)對得上,修正方式與既有掃描機制一致。

三格皆空的機械反查(受影響測試/共改夥伴/呼叫者都是 0)在架構對齊角度沒有異常:這批改動的影響面確實侵入不到其他測試檔或正式程式碼的呼叫路徑,因為改動範圍完全封閉在同一支測試檔的靜態分析工具函式裡。
