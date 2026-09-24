# code-phase9-inc3 第 2 輪收貨與重現紀錄(2026-09-24)

## 收貨
- 5 席收齊才判讀:regress、tests、arch、資安(sonnet),finder(Codex;唯讀沙盒跑不了 pytest,改用直譯器重現)。受審 c5a34da..b34055f 全量與 26e4f2b..b34055f 修正段。
- 前輪 20 條:驗收席逐條讀碼判已修(含 EXPLAIN QUERY PLAN 驗 Phase 6 查詢計畫沒變);測試席 24 種變異全紅、既有測試沒放寬(clean)。資安席確認前輪 4 條都在第一線封住。
- 格式處理:tests 席檔首把「severity: clean」與摘要擠在同一行,編排者只把摘要移到下一行,文字未改。
- quote-check 五份全數錨定。共 5 條發現,合併成 3 件事。

## 編排者重現表

| id | 重現 | 結論 |
|---|---|---|
| 資安-1 | 讀 src/rtb/ops/slo.py:289-292 run() 只看 stable;tests/ops/test_slo.py:399-400 沒帶稽核金鑰時 missing 為真仍斷言 EXIT_OK | HIT,折入 |
| regress-1 | 讀 src/rtb/ops/slo.py:228-240 except Exception 把程式錯誤也轉成 missing,run() 回 0 | HIT,同一類,折入 |
| x1-1 | 同 regress-1,席位用直譯器讓計數器丟 AssertionError,六條全 missing、回 EXIT_OK | HIT,同一件,折入(只隔離資料來源例外;任何一條缺資料或出錯回獨立結束代碼) |
| arch-1 | 讀 src/rtb/httpclient.py 的 ClientHeader.CAPABILITY 說明「寫入能力憑證」,稽核金鑰卻原樣放進同一標頭;圖譜 Systems/共用行程基礎 記的標頭列舉用途是擋故障注入標頭,不禁新增成員 | HIT,折入(稽核金鑰另開專用標頭) |
| x1-2 | 席位用直譯器以非 Latin-1 金鑰重現 UnicodeEncodeError,read_key 認可但送不出 | HIT,折入(金鑰位元組 base64url 後放標頭) |

## 處置
- 全部折入,放行 0、駁回 0。修法交回增量 3 修正實作員。第 3 輪是代碼審上限。
