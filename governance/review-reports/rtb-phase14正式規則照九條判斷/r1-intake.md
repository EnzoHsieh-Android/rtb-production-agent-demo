preflight-4: ran

# r1 收件與前掃(rtb-phase14正式規則照九條判斷)

前掃員(sonnet)只讀逐項掃 ①未定義 ②壞引用 ③範圍矛盾 ④機械宣稱驗語意;① ② ③ 各 0 條,④ 1 條(語意類),編排者照下列修改真檔:

- ④〈現況〉第一條:修改前「`src/rtb/eval/rubric.py` 提供最後五格的三類答案。」→ 修改後「`src/rtb/eval/rubric.py` 只認 Phase 10 自己的 5 格答案,`investigation_cases.py` 的 `_PHASE10`／`VERDICT` 再把它們映到九格裡的第 1、2、7、8、9 格(不連續),其餘四格(裁定 8、裁定 12 三條)的答案直接寫在 `VERDICT`。」依據 `src/rtb/eval/investigation_cases.py:74-84`。未動核心裁定節。

其餘機械可驗宣稱(租約算式 26/34/42/66、四查詢 5 次 HTTP、F7 300/8/300、F4/F6 接續時序、要改寫合約表對原條款的描述)前掃員逐一開檔核對,皆相符。
