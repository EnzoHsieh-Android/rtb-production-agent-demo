---
type: verification
status: pass
date: 2026-09-24
valid_under:
  - 程式版本為 main 403eb39(推送範圍 eda93c1..403eb39,含增量 1、增量 1 代碼審三輪的修正、增量 2);Python 3.14.6,macOS 本機;五份宣稱清單在 claims/;驗證器指令 python tools/verify_claims.py claims/;使用者本人裁定的兩條(清單不帶結果、F7 在新工作多跑一次照舊紅燈就重跑)
  - "2026-10-03 補:之後驗證器(證據測試加 -P)與 claims/*.json 多次改動,驗證器與它的測試在 [[Verification/Phase12增量3驗收紀錄]]、[[Verification/Phase14增量3驗證紀錄]]、[[Verification/Phase14增量4驗證紀錄]] 重跑通過;prompt-injection 的 policy 在 Phase 14 增量 3 改寫,語意由該增量代碼審的資安席與鏡頭 A 另外看過(卷證 governance/review-reports/code-phase14-inc3),記在 [[Verification/Phase14增量3驗證紀錄]];之後 claims 只更新指紋、沒改 policy,也沒新增第六條宣稱"
revalidate_when: "改動 tools/verify_claims.py、tools/claim_hashes.py、claims/*.json、CI 的 claims 工作、pyproject 的 pytest 設定,或五條宣稱範圍內的程式時重跑驗證器與它的測試;新增第六條宣稱或改寫任一條 policy 時,另外派審查員看語意是否仍充分"
tags:
  - type/verification
  - status/pass
plan_refs:
  - "[[Projects/RTB_Phase11證據清單與驗證器_計劃]]"
---
# Phase11驗收紀錄

驗證對象:[[Systems/宣稱驗證器]]、[[Systems/Mock-DSP]](合約措辭改窄);依據計劃 [[Projects/RTB_Phase11證據清單與驗證器_計劃]]。

## 結論

Phase 11 完成:五條安全宣稱(冪等與結果不明、權限與護欄、並行、提示注入、總曝險)各有一份 JSON 證據清單,由專案內的驗證器做機械檢查——格式、存在與路徑、依賴閉包與雜湊新舊、列舉覆蓋、故障注入的證據真的引用了手段,最後自己在乾淨環境跑一次證據測試——印出通過或擋下與原因。清單不帶任何結果欄;驗證器只做機械檢查,不讀審查結論、不依賴 lumos。本機與 CI 用同一條指令。推上 main(eda93c1..403eb39),CI 一次綠、沒有重跑。下面的缺口與天花板如實標明,不宣稱比這更多。

## 對照交接文件 Phase 11 完成條件

- **至少涵蓋冪等/結果不明、權限/護欄、並行、提示注入、總曝險**:claims/ 底下五份清單,驗證器把五個名字寫成必要常數,少一份就擋。[test:test_a_missing_required_claim_is_blocked]、[test:test_the_required_claims_are_the_five_in_the_plan]、[test:test_the_committed_claims_pass]。
  - 本機實跑正式清單:通過,77 支證據測試(展開參數化後的實際筆數見驗證器輸出),約 42 秒。證據涵蓋 F1–F7 端到端與各自的單元、並行測試;冪等那份用 DSP 路由層的 CAMPAIGN_WRITE_ACTIONS 列舉改預算與暫停兩種動作。
  - 權限與護欄沒有既有 ★INVARIANT★,20 支證據逐支挑選,理由寫在 [[Systems/宣稱驗證器]]〈權限護欄那份清單怎麼挑的〉。
- **驗證器能因缺檔、未執行測試、證據過期、宣稱範圍超過證據而擋下**:
  - 缺檔、路徑不合規則(絕對路徑、..、符號連結、逐段大小寫):[test:test_a_missing_file_or_symbol_is_blocked]。
  - 未執行或沒通過(沒收集到、跳過、預期失敗、非嚴格預期失敗卻通過、失敗、逾時、pytest 找不到節點):[test:test_a_skipped_or_failing_or_missing_test_is_blocked]、[test:test_a_parametrized_or_unknown_node_id_is_named_in_the_block]、[test:test_a_run_that_leaves_no_xfail_record_is_blocked]。驗證器自己跑,不讀外部結果檔:[test:test_the_verifier_runs_the_tests_itself]。
  - 證據過期(範圍內任一檔的雜湊跟清單不一致;範圍由驗證器沿匯入與 pytest_plugins 算出依賴閉包,少列就擋;pytest 設定只准 testpaths 與 pythonpath,addopts 整個不准):[test:test_a_stale_scope_hash_is_blocked]、[test:test_a_file_missing_from_the_dependency_closure_is_blocked]。
  - 宣稱範圍超過證據(policy 用了範圍詞卻沒有列舉、列舉有項目沒被任何證據標到、登錄表讀不出字面常數):[test:test_an_uncovered_enumerated_item_is_blocked]、[test:test_a_registry_module_the_verifier_cannot_follow_is_blocked]。真實案例:Mock-DSP 原合約寫「每一種寫入動作版本不符都拒收」,路由另有作廢;查程式確認作廢本來就不比版本,改窄措辭,冪等清單只列舉會改廣告狀態的兩種動作。
- **故意只填「已完成」的自我宣稱無法通過**:清單每一層白名單,result、passed、status 等任何結果欄都擋;計劃列的造假示範全部有測試證明會擋——只寫「已完成」、改碼沒重算雜湊、宣稱範圍大於證據、測試被跳過、conftest 改結果但沒重算雜湊。[test:test_a_manifest_with_any_unknown_key_is_blocked]、[test:test_every_forgery_in_the_plan_is_blocked]。
- **語意審查與機械驗證職責分離**:驗證器只做機械檢查,不讀任何審查結論(使用者裁定);清單改動走既有代碼審,審查員依交接文件第 15.4 節寫評分依據、輸入版本(清單的 sha256)與理由。驗證器與產品互不匯入,也不呼叫 lumos。[test:test_the_verifier_and_the_product_do_not_import_each_other]、[test:test_the_tools_directory_bans_importing_the_product_through_ruff]。
- **驗證器結果可在本地與 CI 重現**:本機與 CI 同一條指令;CI 另開跟 checks 平行的 claims 工作,接線檢查鎖住指令字串、工作歸屬、不准 continue-on-error、|| true、if、shell、defaults。子行程用 -E -s 並清掉 PYTEST_ 開頭的變數,設定檔只准 pyproject 一份、pythonpath 只准 src,不受本機環境影響。輸出印驗證器與每份清單的 sha256 和提交編號,讓一次結果對得回是哪一版。[test:test_the_ci_runs_the_claims_verifier_and_it_can_fail]、[test:test_the_pytest_run_ignores_outside_config_and_environment]、[test:test_the_output_names_the_verifier_and_manifest_versions]。
  - CI 上 claims 工作的實跑:run 35973739801,「通過:5 條宣稱,跑了 77 支證據測試全部通過」,工作約 83 秒。

## 兩個增量各做了什麼

- **增量 1:清單格式與驗證器第 1–4、6 步、五份清單、CI 平行工作。** 合約 [S800]–[S805]、[S807]–[S816]。
- **增量 2:第 5 步故障注入、只印不寫的雜湊輔助、造假示範、Mock-DSP 措辭改窄、暫停同鍵測試。** 合約 [S806]、[S817]。暫停同鍵那支補上前,原本沒有任何測試守住暫停的同鍵只套用一次(拿掉這支後 dsp 與 executor 其餘 883 支全綠),補了之後冪等宣稱才放寬回「每一種寫入動作」。

Phase 11 合約共 18 條,全部綁測試、懸空 0。

## 怎麼驗的(2026-09-24)

- CI:run 35973739801(推送 403eb39):checks 工作 2094 passed、pytest 208.79 秒、整個工作約 3 分 45 秒;claims 工作通過、約 83 秒,兩個工作平行、都一次綠。
- 本機:全套 2047 條通過(約 127 秒)、ruff 與 mypy 乾淨,lumos spec-trace 18 條全綁;驗證器實跑正式清單通過(77 支證據測試,約 42 秒);雜湊輔助回報沒有不一致。
- 設計審:high 兩輪。第 1 輪 7 席 44 條(折 43、1 條重現不到);第 2 輪 3 席 delta 審 13 條全折。卷證 governance/review-reports/rtb-phase11證據清單與驗證器/。
- 代碼審:
  - 增量 1:第 1 輪 3 席 12 條、第 2 輪 13 條(協調者裁定改走「看不懂就擋」)、第 3 輪 6 條(協調者裁定改成白名單;末輪不再派新席,協調者以三份審查員探針在修正後版本重跑驗收,紀錄 r3-fix-verification.md)。上限三輪,沒有第 4 輪。卷證 governance/review-reports/code-phase11-inc1/。
  - 增量 2:第 1 輪 14 條加協調者轉來 1 條(for、with 的屬性目標)、第 2 輪 9 條、第 3 輪 7 條(改成白名單;協調者以兩份探針重跑驗收,15 例中 13 例擋下、2 例是記進天花板的 except 與 try-else 寫法,紀錄 r3-fix-verification.md)。卷證 governance/review-reports/code-phase11-inc2/。
- 變異檢查(/tmp 複本,專案檔沒動):增量 1 初版 64 道、代碼審第 1 輪修正 29 道、第 2 輪修正 21 道、增量 1 代碼審第 3 輪修正 20 道、增量 2 初版 22 道再加 1 道、增量 2 代碼審第 1 輪修正 17 道,每一道拿掉一條擋下規則;中途存活的全部查明,不是補測試就是刪掉冗餘碼,最後全數翻紅。

## 使用者本人裁定(2026-09-24)

- 情境題:證據是舊版本跑出來的、之後改過宣稱範圍內的程式 → 擋下。
- 每條宣稱一份 JSON 清單;專案內一支驗證器,本機與 CI 同一個指令;驗證器自己在目前版本跑測試,不採信外部結果;清單不准有「結果」欄;驗證器只做機械檢查,語意是否充分留給獨立審查員,驗證器不採信審查員結論;跟 lumos 各自獨立。
- 驗證器平行工作再跑一次 F7(撞到已知 CI 抖動的機會變兩倍):照舊紅燈就重跑,新工作的重跑也算進 [[Issues/F7端到端在CI上偶爾超過60秒]] 回頭條件的次數。(已被取代:使用者 2026-09-24 改裁照 [[Projects/F7效能_計劃]] 做法 1 實作、2026-09-25 再把 F7 上限放寬到 120 秒,見 `tests/executor/test_f7_end_to_end.py` 末行;該 Issue 已 resolved,不再計次。)

## 缺的證據與天花板

- 重算 conftest 雜湊後改結果的鉤子照樣通過,有一支測試把這個天花板釘住([test:test_a_conftest_that_rewrites_results_and_is_rehashed_passes_which_is_the_ceiling])。
- 雜湊輔助指令是 python -B -m tools.claim_hashes claims/:python -m 會先把輔助本身編成 .pyc,程式裡攔不到,所以靠 -B。
- 雜湊重貼:作者改了程式之後直接重算雜湊寫回,機器分不出「重看過」還是「只是重貼」;雜湊輔助讓重貼更容易。唯一的效果是清單差異進提交、讓審查員看到。REVISIT 見計劃〈實務隱患〉。
- 依賴閉包的看不懂就擋(代碼審第 2、3 輪):import *、match、類別屬性指派、sys.path、pytest 設定只准 testpaths 與 pythonpath、解析不到的匯入一律擋;這是白名單,不是證明所有寫法都看得懂。
- covers 是作者自己標的,驗證器只查有標、項目在列舉裡、測試跑過且通過,不查那支測試真的測了那一項;故障注入只證明有注入、有斷言,不證明注入有意義。
- 驗證器分不出範圍詞掛在 policy 的哪個子句,只查整份清單的 covers 聯集。
- 依賴閉包只看靜態匯入與字面的外掛名稱;動態匯入、子行程啟動的程式看不到。看不懂的寫法(import *、match、類別屬性指派、pytest 原生設定表、多列 pythonpath)一律擋,不去模擬。
- 範圍詞是固定清單,換成清單外的說法(例如用「都」)掃不到。
- 語意審查入口目前只靠代碼審流程,沒有機械守衛(使用者維持 REVISIT,不另加)。

## 偏離與缺口

- 推送沒有用 --no-verify:推送前的檢查(表態閘、受波及合約測試 25 支)全過。提交時「每支檔有家」擋下過一次混合提交,照規矩拆成功能提交與文件提交兩個,不是繞過。
- 增量 1 初版與增量 2 的部分測試是對已寫好的碼補的(補變異缺口時),有效性靠變異翻紅證明,不是先紅後綠;每輪新規則的主要測試都是先寫、先紅。
- 延遲與執行時間是本機單次量測,只當量級參考;CI 時間見上方(checks 約 3 分 45 秒、claims 約 83 秒)。
