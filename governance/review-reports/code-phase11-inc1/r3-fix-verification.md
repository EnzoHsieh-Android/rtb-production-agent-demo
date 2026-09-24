# 第 3 輪(末輪)修正驗收(協調者機械重現,2026-09-24)

代碼審上限三輪,第 3 輪折入不再派新席;協調者拿三位審查員留下的重現腳本,在修正後版本 b91e384 的 git archive 複本(/tmp/p11r3-verify)重跑。

| 發現 | 重現腳本 | 修正前 | 修正後 |
|---|---|---|---|
| s1 addopts -o / --override-ini 改 pythonpath | /tmp/p11r3-corr/exp/e1.py | 0 通過 | 1 擋下(ini_options 只准 pythonpath、testpaths) |
| s2 conftest sys.path.insert | e2.py E2 | 0 通過 | 1 擋下(改了 sys.path) |
| s3 別的模組改登錄表 | e5.py | 0 通過 | 1 擋下(模組層屬性指派) |
| s4/t2 巢狀類別、推導式 setattr | e4.py、e2.py E3/E4 | None 放行 | 擋下 |
| t1 pytest_plugins list 後 append | codex test_review_adversarial_r3.py | 驗證器回 0 | 驗證器回 1(對抗測試斷言失敗) |
| t2 推導式 setattr 換掉 Handler.handle | 同上 | 驗證器回 0 | 驗證器回 1 |

正向(不誤擋):property 鏈、overload、typing.overload、方法 overload、推導式變數、無值註記、巢狀類別正常定義、async overload 都回 None。

殘留(非本輪發現,審查員探測順手寫到):模組層 `for H.handle in (...)` 仍回 None;已交實作者在增量 2 修正時一併補(for/with/:= 目標是屬性鏈也算指派)。
