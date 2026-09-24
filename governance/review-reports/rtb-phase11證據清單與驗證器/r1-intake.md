preflight-4: ran

# r1 收件紀錄(前掃與編排者重現)

## 前掃:機械宣稱驗語意(編排者親自開檔與實跑)

| 宣稱 | 怎麼驗 | 結果 | 處置 |
|---|---|---|---|
| CAMPAIGN_WRITE_ACTIONS 在 DSP 儲存層 | `grep -n CAMPAIGN_WRITE_ACTIONS src/rtb/dsp/*.py` → 只在 src/rtb/dsp/server.py:110 | 語意不符:在伺服器(路由)模組 | 修真檔:前「從 DSP 儲存層的 CAMPAIGN_WRITE_ACTIONS 列舉」→ 後「從 DSP 伺服器模組(路由那一層,不是儲存層)的 CAMPAIGN_WRITE_ACTIONS 列舉」 |
| 作廢只看鍵、不改廣告狀態、不比版本 | 讀 src/rtb/dsp/store.py CampaignStore.void:validate_key_and_version(key, 1) 固定帶 1、只寫 voided_keys 表;server.py _void_operation 的 expected_version 只過 validate_key_and_version 範圍檢查 | 相符 | 不改 |
| tools/mypy_sarif.py 與 tests/tools/ 是先例、ruff 與 mypy 都掃得到 | pyproject.toml mypy files = ["src", "tools"];ruff.toml 只 extend-exclude scripts;ls tools tests/tools | 相符 | 不改 |
| CI checkout 預設只抓一層歷史 | .github/workflows/ci.yml 用 actions/checkout@v4 未設 fetch-depth(預設 1) | 相符 | 不改 |
| pytest JUnit 能分出跳過、預期失敗;參數化函式層編號展開全部參數 | scratchpad 實跑 pytest 9.1.1:skip → `<skipped type="pytest.skip">`;xfail → `<skipped type="pytest.xfail">`;**xpass(非嚴格)→ 跟一般通過一樣的空 testcase**;函式層編號 test_p → test_p[1]、test_p[2] 都出現;-k 取消選取 → rc 5、JUnit 0 筆;一個編號不存在 → rc 4、整批 0 筆 | 部分不符:「預期失敗算擋」單靠 JUnit 抓不到 xpass;找不到編號會讓整批不跑 | 修真檔:第 6 步加 `-o xfail_strict=true`,並補三行 JUnit 限制(xpass 長得像通過、rc 4 整批不跑要指出哪個編號、classname 對回節點編號的規則) |
| 專案沒設 xfail_strict、測試目前沒有 skip/xfail | pyproject.toml [tool.pytest.ini_options] 只有 testpaths、pythonpath;grep 測試 xfail/skip 0 筆 | 相符(現況乾淨,新規防未來) | 不改 |

動到「使用者裁定」節:無。

## 機械排乾
- refcheck:0 問題;prose-lint:0;pitfalls --check:有節;lint:0;spec-trace:10 條全懸空(測試尚未實作,設計期預期)。

## 收貨三道
- quote-check:繞過、宣稱證據、可測重現、架構對齊全錨;外家-codex 1 句、接手 1 句、邊界 2 句錨不到。錨不到的都是改寫過的引句(把原文的「」拿掉或插入 ...),內容指的段落存在,下表逐條機械重現後才採信;接手 F10 引的是舊版文字(見下)。
- refcheck:架構對齊 missing 2(tools/ruff.toml、tools/verify_claims.py——席位本意就是指出它們不存在)、out_of_range 2(ruff.toml 行號寫過頭,不影響結論);其餘全對。
- seat-check:派工單格式讓 seat-check 判 vacuous(派工單 seats 陣列不是它讀的欄位),沒有機械判定;編排者人工核:七席都讀了凍結副本並引用對應程式檔。

## 編排者機械重現(scratchpad 實跑,Python 3.14.6、pytest 9.1.1)

| id | 重現指令摘要 | 結果 |
|---|---|---|
| 繞過F1 | t/conftest.py 用 pytest_runtest_makereport hookwrapper 把 call 階段改成 passed;跑 `assert 1 == 2` 的測試出 JUnit | rc=0、failure 0 筆:HIT |
| 邊界F1 | json.loads 5000 位整數 | ValueError、不是 JSONDecodeError:HIT |
| 邊界F2 / 外家F6 | json.loads('{"a":1,"a":2}') | {'a': 2},前值靜默消失:HIT |
| 邊界(bool) | json true:isinstance(v,int)、v==1、type(v) is int | True True False:HIT |
| 邊界F3 | macOS 建 Lower.py 後 os.path.exists("LOWER.PY") | True(不分大小寫):HIT;Linux 分大小寫是既知事實,沒在 CI 實跑 |
| 邊界F5 / 繞過F5 | ast.walk 掃 FunctionDef:if False 裡的 dead、被重新指派成 None 的 f、巢狀 inner | 全部被當成找得到:HIT |
| 宣稱證據F1 | 讀 tests/analyzer/test_f6_end_to_end.py 檔頭 | 測的是死信重放重新驗證,不是冪等對帳:HIT |
| 外家F3 | 讀計劃 harness 定義;evidence 只存節點編號 | 證據測試檔本身不在任何雜湊內:HIT |
| 外家F4 / 宣稱證據F2 | src/rtb/dsp/server.py 匯入 capability、capabilitykit、httpkit;store.py 匯入 sqlitekit | scope 完整性全靠作者手列:HIT |
| 可測F1 | pyproject testpaths=["tests"];tests/tools/test_mypy_sarif.py 在全套裡 | 放 tests/ 的整合測試會被 checks 工作收集:HIT |
| 可測F2 / 外家F8 | docs/.../Issues/F7端到端在CI上偶爾超過60秒.md | 已知 CI 抖動,使用者裁定紅了就重跑:HIT |
| h10(接手F10) | grep 凍結快照「DSP 儲存層的 CAMPAIGN」 | 0 筆;席位讀的是主工作樹未改的計劃筆記,前掃已修:MISS(已折,不重算) |
| 其餘(接手 F1–F9、F11,繞過 F2–F4、F6,外家 F1、F2、F5、F7、F9,可測 F3,邊界 F4、F6–F9,架構三條) | 讀計劃對應段落 | 屬規格文字缺口,讀原文即成立:HIT |

另:pytest 外掛自動載入(requirements-dev 目前沒有 pytest 外掛,但日後裝了會被自動載入、能改結果),編排者自己補進第 6 步,不是席位發現。

## 帳上 id 對照
b1–b6=繞過 F1–F6;c1–c9=外家 Codex F1–F9;e1、e2=宣稱證據 F1、F2;t1–t3=可測重現 F1–F3;x1–x9=邊界 F1–F9、xb=邊界「manifest_version 是 bool」;h1–h11=接手 F1–F11;a1–a3=架構對齊三條(依報告順序)。
