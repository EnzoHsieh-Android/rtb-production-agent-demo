severity: clean

以下是完整報告全文(已依格式規則撰寫):

---

severity: clean

三項指定核對(稽核金鑰編解碼放進 `capabilitykit` 是否越權、`X-Dsp-Audit-Key` 在 `httpclient` 與 `capabilitykit` 兩處各放一份是否算兩套定義、`EXIT_INCOMPLETE = 8` 是否與既有結束代碼表衝突及 Systems 筆記有沒有同步)都沒有查到 r3-delta.patch 新帶出的架構不一致——標頭雙份是沿用 `X-Capability` 既有「不互相匯入、由測試比對兩邊字串一致」的慣例(`src/rtb/httpclient.py:148-153` 新增 `AUDIT_KEY` 成員與既有 `CAPABILITY` 成員同一寫法,`tests/httpclient/test_httpclient.py` 的 `test_client_headers_are_exactly_idempotency_key_and_capability` 同步擴充比對);`encode_audit_key`/`decode_audit_key` 只是把 `capabilitykit.py` 原本就有、且全專案僅此一處的私有 base64url 編解碼原語(`_b64encode`/`_b64decode`)包一層公開介面給稽核金鑰用,沒有另立第二套編碼機制,金鑰環境變數名稱三把(`KEY_ENV`/`APPROVAL_KEY_ENV`/`AUDIT_KEY_ENV`)集中一處管理的既有慣例也照舊;`EXIT_INCOMPLETE = 8` 沒有跟 `ops` 套件(`trace.py`/`metrics.py`/`slo.py` 合計用掉 0/2/3/4/5/6)或 `executor` 套件(2/3/4/5/6/7)既有代碼碰撞,且提交 `83093e6`(已是目前 HEAD)在同一次提交把 `docs/rtb-production-agent-demo-knowledge/Systems/服務水準與燒損告警.md`、`Systems/寫入能力憑證.md`、`Systems/Mock-DSP.md` 同步改寫,結束代碼表、標頭改法與函式位置都補齊了說明,符合本專案「同一次工作內寫回圖譜」的鐵則。

---

補充查證用的檔案位置:`src/rtb/capabilitykit.py:28-29,63-71,80-90`(AUDIT_HEADER 與 encode/decode_audit_key)、`src/rtb/httpclient.py:144-153`(ClientHeader 兩個標頭成員)、`src/rtb/ops/slo.py:44-50,283-299`(結束代碼表與 EXIT_INCOMPLETE 判斷順序)、`src/rtb/dsp/server.py:166-181`(_require_audit_key 改走專用標頭)。
