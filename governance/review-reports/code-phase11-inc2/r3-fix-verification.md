# 第 3 輪(末輪)修正驗收(協調者機械重現,2026-09-24)

代碼審上限三輪,第 3 輪折入不再派新席;協調者把 819374c 的 git archive 複本放到 /tmp/p11i2-verify,放進兩份審查員探針重跑。

- 外家 Codex 探針 test_r3_external_probe.py(三支原本斷言漏洞存在):三支全部失敗 = 三個洞都關了——巢狀未呼叫函式不再誤擋、break 之後的死碼被擋、非 pytest 的 fixture 裝飾器被擋(訊息「用了來源不明的 fixture 裝飾器」)。
- 正確性席探針 test_zz_r3probe.py(15 例,印結束代碼):
  - 擋下(code=1)13 例:模組 pytestmark、類別 pytestmark、裝飾器變數 parametrize、pytest_generate_tests、try 包的同名 fixture、if 包的同名 fixture、if not True、if 空 tuple、if 1 == 2、for 空 tuple、break 之後、continue 之後、兩邊 return 之後。
  - 通過(code=0)2 例:手段只在 except、try else 死——已照協調者裁定列為天花板,寫進 Systems/宣稱驗證器 的 RULE。
- 架構對齊席的巢狀函式誤擋:由 Codex 第一支探針與新的正向測試覆蓋。
