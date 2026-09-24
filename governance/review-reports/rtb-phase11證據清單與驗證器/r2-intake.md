# r2 收件紀錄(delta 審:第 1 輪後新寫段落)

派工前:〈使用者裁定〉加 F7 那一條(使用者本人 2026-09-24,經協調者轉達),審計修正紀錄的「要轉問」改成結果。快照 sha256 7fa390df…c7fceb;delta 為 6f2482d 以來計劃的 diff。

## 收貨三道
- quote-check:驗收、新段落全錨;外家-codex 1 句錨不到(引句內包了「結果」被截斷,<10 字),那條(k6)編排者讀原文確認段落存在後採信。
- refcheck:新段落 missing 4(tools/ruff.toml、tools/verify_claims.py、tests/tools/test_verify_claims2.py 是尚未建立或席位 /tmp 實驗的檔名;tests/{…}/conftest.py 是展開寫法),不影響結論;其餘全對。
- seat-check:本輪派工單改成每席一份、帶 materials,已非 vacuous;三席各有 1–2 份材料「沒提到」(驗收席沒提接手與架構對齊的 r1 報告、新段落席沒提快照與 delta 檔名、外家沒提 delta 與 pyproject),屬觀測不擋;驗收席報告逐條列了 42 條驗收含 h 與 a 系列內容,人工核判有讀。

## 編排者機械重現(scratchpad,pytest 9.1.1)

| id | 重現 | 結果 |
|---|---|---|
| n1 | tests/kit/test_migration_failure_closes_the_connection.py:12-14 只用 from rtb.dsp import store 形式;src/rtb/dsp/__init__.py 不存在 | 只解析套件會漏 store.py:HIT |
| n2 | PYTEST_ADDOPTS="-p evil" 加 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1,evil 把 call 改 passed,跑 assert 0 | rc=0:HIT |
| n6 | pyproject addopts=-k nothing → 1 deselected;加 pytest.ini(addopts 空)→ 1 failed;再加 -c pyproject.toml → 1 deselected | pytest.ini 蓋掉 pyproject:HIT;-c 能釘回 |
| v1 | 讀快照〈五條宣稱〉權限護欄段含「一律擋下」,步驟 4 範圍詞含「一律」 | 摘要會被誤當 policy 原文:HIT |
| n3、n4、n5、k1–k5 | 讀快照對應段落;n4 席位以 ruff 0.16.8 實跑,ruff 依檔案所在目錄往上找設定是既知行為 | 規格文字缺口成立:HIT |
| k6 | 讀交接文件第 15.2 節要驗證器身分與結果;使用者裁定只禁清單自填結果 | 輸出帶版本不抵觸裁定:HIT(折非衝突部分,轉告協調者) |

## 帳上 id 對照
v1=驗收 F1;n1–n6=新段落 F1–F6;k1–k6=外家 Codex 第 2 輪 F1–F6。
