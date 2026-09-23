severity: clean

## 分層與依賴方向

- 測試放的位置跟既有慣例一致。`tests/adversarial_samples.py` 放在 `tests` 根目錄,跟既有共用素材模組 `tests/capability_samples.py:1`(同樣是根目錄、同樣開頭一段「為什麼要獨立成模組」的說明)同一層;不是照 `tests/domain/proposal_samples.py:1` 放進子套件,理由也合理(對抗性文字要給 `tests/analyzer` 用,不專屬 domain 或 executor 一側)。
- 跨分析與執行兩側的結構測試放在 `tests/analyzer` 合理,有先例。`tests/analyzer/test_boundaries.py:17` 本來就在做「原始碼掃描 + 端到端串 dsp/executor/inbox」這類跨套件邊界測試(S52/S53),不是分析端內部單元測試的專屬目錄;新檔 `tests/analyzer/test_trust_boundary.py:1` 延續同一種「邊界測試放分析端」的位置慣例,而不是照 `rtb.executor` 的職責另開 `tests/executor/test_trust_boundary.py`。
- 測試匯入 `tests.executor.fakes` 有先例,不是第一次跨側借用。`tests/dsp/test_void.py:18` 已經是 `tests/dsp` 匯入 `tests.executor.fakes` 的 `proposal`/`write_config`;新測試裡 `tests/analyzer/test_trust_boundary.py` 的 `from tests.executor.fakes import proposal, write_config`(對應引句見下)是同一種跨目錄借用,方向與既有先例一致。

## 命名與錯誤處理

- `tests/adversarial_samples.py` 的結構(模組層 docstring 說明「為什麼要獨立」+ UPPER_SNAKE 常數 + 底下資料)跟 `tests/capability_samples.py:1`(`TEST_KEY`/`DEFAULT_TENANT`/`LIFETIME`)、`tests/domain/proposal_samples.py:1`(`CREATED`/`EXPIRES`/`REJECTED_OVERRIDES`)同構,`NAME_LIMIT`、`CATEGORIES`、`SAMPLES` 命名風格一致。
- `NAME_LIMIT = 4096` 用註解指向真正常數 `rtb.dsp.store.MAX_CAMPAIGN_NAME_LENGTH` 而不是 import,這個做法不是新引入的隨意行為——既有的 `tests/dsp/test_campaign_name.py:22` 本來就是 `NAME_LIMIT = 4096`(同樣沒 import store 的常數),新檔只是照抄既有的重複硬編慣例。
- `test_trust_boundary.py` 裡的小型私有輔助函式(`_evidence`、`_batch`、`_fingerprint`)全部位置參數、無 `noqa: PLR0913`,跟 `tests/analyzer/test_flow.py:103`(`_to_analyzing(store, task_id="t1", campaign_id="c1")`)、`tests/executor/test_execution.py` 一批 `_not_found(h)` 這類「測試檔內部小輔助函式用位置參數」的既有寫法一致,不用比照 `capability_samples.claims()` 那種對外共用 API 才需要的具名參數 + `noqa` 處理。
- 沒發現命名或錯誤處理上跟既有慣例不一致之處。

## 第二種做法

- `policy.py` 的改動沒有另立機制:`TrustClass`、`MAX_UNTRUSTED_TEXT_LENGTH` 是既有 `src/rtb/domain/evidence.py:24`、`:34` 就有的型別(2026-09-23 已裁定,非本次 patch 新增),`_payload` 只是多讀一個既有欄位做為篩選條件,沒有引入平行的信任判斷邏輯。
- S212 的原始碼掃描沒有另開一套掃描工具,而是重用 `tests/analyzer/test_boundaries.py` 已建立的模式:對整個套件 `rglob("*.py")` + `ast.walk` 找違規 + `assert offenders == []`,並且同樣附了「守衛的守衛」(`assert len(files) >= 7`,對照 `test_boundaries.py` 沒有但 `tests/analyzer/test_flow.py:408` 的 `assert len(sql_literals) >= 4` 是同一種寫法;`tests/dsp/test_campaign_name.py` 也用同構掃描)。這是延伸既有掃描機制去掃一個新目標(`risk_summary` 屬性讀取),不是重寫一份新的原始碼掃描框架。
- 沒有找到專案裡已存在、被重複實作的「同功能工具」——risk_summary 的屬性讀取掃描此前不存在,新增合理;沒發現引入第二種做法的痕跡。

不對齊共 0 條,其中 major 0 條。

⚠ 交編排者:三格機械反查(受影響測試/共改夥伴/呼叫者)皆為 0,且圖譜沒有釘到節點,以上比對全靠審查員自己在 `/Users/enzo/rtb-3b` 現場核對(如 `tests/dsp/test_void.py:18`、`tests/analyzer/test_boundaries.py` 的掃描寫法),沒有圖譜合約可供二次核銷,若後續有節點補上,建議再核一次此份判斷是否仍成立。
