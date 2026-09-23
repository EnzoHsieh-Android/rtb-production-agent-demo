severity: clean

### 已看:差異測試改呼叫 `_campaign_text` 後,「沒有名稱」變體與名稱轉換是否符合真實流程

`tests/analyzer/test_trust_boundary.py` 的 `_batch()` 現在寫
`text = _campaign_text({} if name is None else {"name": name})`。核對 `src/rtb/analyzer/dsp_client.py:94-101`:

```
def _campaign_text(body: dict[str, Any]) -> dict[str, Any]:
    name = body.get("name")
    if not isinstance(name, str):
        return {"name": None, "truncated": False}
    return {"name": name[:MAX_UNTRUSTED_TEXT_LENGTH],
            "truncated": len(name) > MAX_UNTRUSTED_TEXT_LENGTH}
```

`_campaign_text` 只讀 `body.get("name")`,不碰字典裡其他鍵。測試傳的是只含 `name` 鍵(或空字典)的精簡字典,而正式流程(`dsp_client.py:139` 的 `fetch()`)傳的是完整 `state_body`(含 id/budget/status/version/name);因為函式只看 `name` 這一個鍵,兩種呼法對這支函式而言結果等價,不是造假的簡化。

`name is None` 時傳 `{}`,`body.get("name")` 回 `None` → `not isinstance(None, str)` 為真 → 回 `{"name": None, "truncated": False}`。三筆證據(state/metrics/text)照樣都建立,只是 text 的 payload 是「名稱空值」,不是少一筆證據,跟計劃筆記 S210 註記「沒有名稱(名稱空值)」的描述一致,也跟 `_campaign_text` 自己文件字串「缺少或不是字串記空值,不丟例外」一致。名稱不是 `None` 時直接把 `name` 交給正式的 `_campaign_text` 做截斷,不再在測試裡另抄一份 512 字元的截斷規則——這正是要修的第 1 輪問題(用戶端改截斷規則,差異測試自動跟著測真的流程,不會因為測試裡抄的舊規則而各測各的)。抽了 `oversized`(4096 字元,含代理對字元那份)與 `normal` 兩類實際跑過 `_campaign_text`,確認截斷與 `truncated` 旗標行為與正式函式一致,沒有另外分岔的邏輯。

### 已看:測試從測試碼匯入 `_campaign_text`(底線開頭的私有函式),專案裡有沒有先例

有先例,不是新樣式:
- `tests/executor/test_multi_worker.py:313` `from rtb.executor.execution import ExecutorHalted, _no_progress`(直接匯入私有函式)。
- `tests/domain/test_proposal.py:11` `from rtb.domain import _checks`(直接匯入整個私有模組)。

這兩處都是「測試要跟正式的內部規則綁在一起,不想在測試裡另抄一份」的同類用法,跟這次 `_campaign_text` 的匯入動機一致,沒有看到更好的既有替代做法(例如透過公開介面間接驗證)在本專案裡被採用過。沒有更好做法的線索,不標 finding。

### 已看:拿掉兩句斷言後,S212 是否仍完整守住(臨時目錄變異驗證)

複製 repo 到 `mktemp -d` 臨時目錄(`git -C` 不動 repo 根),對 `test_the_execution_side_never_reads_free_text` 做兩組變異,確認移除 `assert MARKER not in sent["url"]` 與 `assert MARKER not in token`(只留 `MARKER not in repr(decode(token, TEST_KEY))`)後仍會翻紅:

1. **寫入本文夾帶理由摘要**:在 `src/rtb/executor/dsp_client.py` 的 `write()` 裡加一行 `body["risk_summary"] = vars(proposal)["risk_summary"]`(刻意繞過測試第 2 步的 `ast.Attribute`/`getattr` 掃描,不用 `proposal.risk_summary` 這種屬性存取寫法)。跑測試結果:
   ```
   assert sent["body"] is not None and MARKER not in repr(sent["body"])
   AssertionError: ... 'zz-risk-marker-5c1d' is contained here:
     {'expected_version': 3, 'risk_summary': 'zz-risk-marker-5c1d', 'new_budget': 150}
   ```
   紅。(另外用 `proposal.risk_summary` 屬性存取寫法時,連第 2 步「執行行程套件全檔沒有屬性存取讀 risk_summary」的 AST 掃描就先紅,兩層都擋。)

2. **能力憑證聲明夾帶理由摘要**:在 `src/rtb/executor/capability_signer.py` 的 `_encode()` 的 `claims` 字典裡加一鍵 `"rs": vars(proposal)["risk_summary"]`(同樣繞過屬性存取寫法)。跑測試結果:
   ```
   assert MARKER not in repr(decode(token, TEST_KEY))  # 聲明解開來看,不看編碼後的字串
   AssertionError: 'zz-risk-marker-5c1d' is contained here:
     ..., 'rs': 'zz-risk-marker-5c1d', 'tenant': 't-default', 'v': 'c1'}
   ```
   紅。

兩組變異都在拿掉的兩句斷言消失之後,靠留下來的 `sent["body"]` 與 `decode(token, ...)` 兩句照樣翻紅,S212「寫入本文與能力憑證聲明不含理由摘要」沒有因為拿掉那兩句而漏掉。

補查被拿掉的兩句本身的辨識力:
- `MARKER not in sent["url"]`:核對 `src/rtb/executor/dsp_client.py:57-64` 的 `write()`,`url` 由 `self._base + path` 組成,`path` 只由 `proposal.campaign_id` 與 `_WRITE_PATHS` 這個固定字面值對照表(`"budget"`/`"pause"`)組成,程式結構上理由摘要進不了 URL;S212 條文本身也只講「寫入本文與能力憑證聲明」,不含 URL。拿掉這句沒有削弱 S212 的覆蓋範圍。
- `MARKER not in token`(對編碼後的原始 token 字串做子字串比對):`src/rtb/capabilitykit.py` 的 `decode()` 是把 token 的聲明段 base64 解碼、驗簽、`json.loads` 還原成 `claims` 字典,是聲明段內容的完整還原(不是重新正規化後可能漏欄位的那種);`repr(decode(token, TEST_KEY))` 已完整涵蓋聲明段裡的所有內容,理由摘要若真的混進聲明,一定會在解碼後的 `repr` 裡出現。對 base64 編碼後的原始 token 字串做子字串比對本來就抓不到明文子字串(base64 轉碼會打散字元對應),是一句沒有辨識力的斷言,拿掉不影響守備。這跟計劃筆記(`RTB_Phase7提示注入與信任邊界_計劃.md` 第 158 行)「拿掉兩句沒有辨識力的斷言」的描述一致,兩邊對得上。

clean,沒有 blocking 條數。
