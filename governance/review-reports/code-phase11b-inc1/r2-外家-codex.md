severity: major

第 1 輪外家兩份報告的八項修正已在複本驗收：七個對應測試通過；另以同批雙執行緒重跑，後端只收到 1 次呼叫，第二筆標為共用。以下兩條是本輪仍可重現的問題；全程未呼叫真實 Claude 模型。

空用量仍通過固定輸入量實測，可讓即時呼叫突破展示上限
severity: major
blocking: 是
引句:「fixed = sum(v for k, v in usage.items() if k.endswith("input_tokens")」
file: `src/rtb/modelverify.py:194`
file: `src/rtb/modelverify.py:224`
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11B大模型接入_計劃.md:200`
重現：在 `/tmp/codex-p11b-r2` 以假回應給 `references()` 一個成功但 `usage={}` 的結果，得到 `fixed_input_tokens_seen=0`，`verify().passed=True`。再以假後端回報 500,000 個輸入 token：預留僅 0.0199104 美元，後端已被呼叫，結算後展示已用 1.2 美元。第 1 輪的「量得超過 4000」已被擋住，但「根本沒有量到」仍被當成零。
建議：必要的輸入用量欄位缺失或型別不合時，讓實測失敗；只有取得可驗證的輸入 token 數才允許寫啟用紀錄。

重播接受缺列的批次紀錄，把超過成本門檻的呼叫標成通過
severity: major
blocking: 是
引句:「if recorded != scenario_ids[:len(recorded)]:  # 批次可能停在中途:要是這次子集的開頭一段」
file: `src/rtb/eval/record.py:180`
file: `src/rtb/eval/model_candidate.py:339`
file: `docs/rtb-production-agent-demo-knowledge/Projects/RTB_Phase11B大模型接入_計劃.md:267`
重現：在 `/tmp/codex-p11b-r2` 建立同批兩個有效錄製檔，原價分別為 0.001、0.009 美元；批次紀錄是有效 JSON，但只含第一列。實際重播 2 個情境後，`missing_recordings=0`、`stopped=None`、`flags=[]`；比較表只看到 0.001 美元，將每次成本 0.002 美元門檻標為「過」。缺列的歷史資料因前綴比對而被當成完整批次。
建議：以本次重播的逐情境結果核對批次紀錄的列數、順序及對應批次；有缺列時標批次不一致，停止用該批次計算門檻。

2 條,blocking 2。