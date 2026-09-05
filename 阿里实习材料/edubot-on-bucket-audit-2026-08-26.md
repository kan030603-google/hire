# EduBot 线上 on 桶多轮 Session 链路与语义审计

> **审计窗口**：2026-08-26 00:00:00 - 21:00:00（Asia/Shanghai）
> **数据源**：生产 SLS `ali-shenma-ai-log` / `edu-mentor`
> **报告版本**：v2（2026-08-27 扩充：客户端 LGI 回传实测、并发统计、暂停恢复四点语义统计、决策/编排占比）
> **审计对象**：有效命中 `edu_main_gate=on` 的多轮 Session（链路级全量 163 个 / 902 请求；语义级抽样 18 个 / 388 轮；暂停恢复语义全量盘查全部 48 个含 PAUSE/RESUME Session）

## 总判定

**链路级 PASS（带 1 项已知状态缺陷）；语义级 PARTIAL（短板集中在暂停任务的恢复语义）**

902 个有效 on 请求的 Bucket → Gate → Router 应用 → Task 迁移 → 终态全链路标记完整且内部自洽，无一例桶位错配，终态 898/902 正常完成；8/27 报告中的 `chat stream failed` 批量故障在本窗口不存在（仅 1 个孤立单点）。语义级路由主干（模型 CONTINUE / PREEMPT）精度良好，但**暂停任务的规则式恢复出现双向失准**（假阳性 4/4 全错、假阴性造成同主题 24 轮不恢复），Task checkpoint（LGI）回退在 5 个 Session 复现且根因已定位到触发形态。

| 核心指标 | 数值 |
|---|---|
| 有效 on 请求（off 2,520） | **2,092** |
| 多轮 on Session（全量链路核验） | **163** |
| 多轮 Session 内有效 on 请求 | **902** |
| 终态正常完成 | **898/902** |
| Session 并发发生率（47/163） | **28.8%** |
| 非 a2ui 卡后下轮回传 lgi=0（5/405） | **1.2%** |
| 恢复规则召回/精确率（双向归零） | **0/4 + 0/4** |
| LGI 回退 Session（6 次） | **5** |

## 1. Technical Summary

- **桶命中（全量核验）**：窗口内带 `edu_main_gate` 键的 `prompt_get success` 共 4,613 行，去重后 4,612 个请求（on 2,092 / off 2,520）；on Session 1,353 个，其中多轮（≥2 个有效 on 请求）163 个（含 2 个 `it-stable-router-*` 合成拨测会话）。**同一 Session 内无混桶**。
- **链路级为全量而非抽样**：163 个多轮 Session 的 902 个有效 on 请求逐一关联 Gate 汇总行、Router 应用行、Task 迁移行、终态行，覆盖率 902/902。
- **Gate 口径修正**：本报告以 `handoff_gate: status=success` 为 Gate 成功，**不要求 `source=model`**；规则路径直接产出决策同样是有效 Gate 成功（修正 8/27 报告仅将 `source=model` 计为成功的口径）。本窗口 Gate 成功 580 次 = 模型 329 + 规则 251。
- **状态应用自洽**：需要落状态的决策恰 68 个（CLARIFY 33 + PREEMPT 31 + RESUME 4）= 应用成功 65 + CAS 冲突 3，与 `router_apply` 计数完全对账；**没有任何 on 请求出现 `not_applied_bucket_off`**。
- **终态**：898 个 `chat_request_end` 正常；4 个异常全部归因清楚——2 个客户端主动断开（client disconnect）、1 个 21:00:09 窗口边界截断、1 个 Main 路径 LLM 单点错误（19:45，孤立事件）。
- **分支覆盖显著好于 8/27 审计**：本窗口观测到 PREEMPT_TO_MAIN（模型 28 + 规则 3）、RESUME_PAUSED（规则 4 + 主 Agent 工具 7）、handback COMPLETE（2）、CAS conflict（3）、gate 异常降级 fallback（2，均为拨测流量），此前未验证的分支均已见到线上真实执行。
- **语义级判定（18 个定向样本）**：路由语义 PASS 11 / PARTIAL 5 / FAIL 2；任务编排 PASS 11 / PARTIAL 3 / FAIL 4；终态执行 PASS 17 / FAIL 1。核心缺陷不在“该不该切走”（模型 margin 判定整体可靠），而在“**暂停之后怎么回来**”。
- **客户端 LGI 回传实测（v2 新增）**：非 a2ui 卡下一轮必回传 0 的客户端说法**不成立**——Main 文本回复后下一轮回传 lgi=0 的仅 5/405（1.2%），98.8% 回传非零且逐轮递增；客户端 LGI 实际语义是**会话全局消息序号（含文本消息）**。回 0 的真实触发是“场景入口卡重发/重入”（见 F7）。
- **并发（v2 新增）**：28.8% 的多轮 Session 出现过请求区间重叠（相邻对重叠率 11.4%，重叠时长 p50=7.7s）；3 次 CAS conflict 全部发生在重叠或间隙 ≤103ms 的紧邻请求上（见 §2.6）。
- **暂停恢复四点语义统计（v2 新增；覆盖全部 48 个含 PAUSE/RESUME Session）**：回归意图 13 次，其中目标为最近暂停任务 11 次；想回最近任务而 resume/toolcall 均未兑现 8 次；想回更早任务的 2 次全部被错误恢复到最近任务（见 F9）。恢复规则对显式“继续”指令召回 0/4，自身触发精确率 0/4。

## 2. 链路级核验（163 Session / 902 请求全量）

分层判定原则：任一层成功不能替代下一层；桶命中以解析后的 `matched.edu_main_gate` 为准，不使用请求入口 `bucket` 或文本模糊包含。

| 阶段 | 预期 | 观测（902 有效 on 请求） | 判定 |
|---|---|---|---|
| **PromptGet / Bucket** | 解析后 `matched.edu_main_gate=on`；同 Session 桶位稳定 | 902/902 有效 on；163 个 Session 无混桶；与 off 请求（同窗口 2,520 个）通过 `state_update=not_applied_bucket_off` 严格分流 | ✅ PASS |
| **Handoff Gate** | 有 active Task 时产出决策（模型或规则）；无任务/场景首轮显式 skip；异常降级 fallback | success 580（模型 329 / 规则 251）；skipped 320（无任务 233 + 场景首轮 `scene_bootstrap` 87）；fallback 2（均为 `it-*` 拨测，`gate_error_continue_active` 按设计降级）；error 0 | ✅ PASS（执行面） |
| **Router 状态应用** | 需落状态的决策要么应用成功、要么因 revision 冲突显式拒绝，无静默丢失 | 68 个需应用决策 = success 65 + conflict 3，与 Gate 决策数逐项对账一致；conflict 后决策丢弃但请求继续按旧状态路由（见发现 F6） | ✅ PASS（对账自洽） |
| **Router 最终目标** | 决策与最终路由一致；handback 允许单请求多次迭代 | MAIN 474 / ACTIVE_HANDOFF 275 / SCENE_DIRECT 87 / HANDOFF_INTENT 66（65 讲万物 + 1 omni_conversation）；决策-路由矩阵中仅有的 3 组“不一致”全部可解释（2 个 handback 多迭代 + conflict 保持原路由），无未解释分叉 | ✅ PASS |
| **Task 迁移 / Checkpoint** | Task 生命周期迁移合法；LGI（last_global_index）单调不回退 | 迁移全部 `result=success`：CONTINUE 277、CREATE 146（scene 87 / tool_true 58 / tool_false_fallback 1）、ENSURE_MAIN 74、PAUSE 61、RESUME 11（gate 4 / tool 7）、COMPLETE（handback）2；**但 5 个 Session 发生 6 次 LGI 非零→0 回退**（61→0、25→0、14→0、10→0、5→0、1→0） | ❌ FAIL（单调性被破坏） |
| **终态 Responder** | 最终路由对应的 Agent 完成流式输出 | 898/902 正常 `chat_request_end`；2 个 client disconnect（客户端提前断开，服务端无故障）；1 个窗口边界（21:00:09 完成）；1 个 `chat stream failed`（Main 路径 `llm_call status=error` 单点） | ✅ PASS（99.6%，异常已归因） |

### 2.1 Gate 决策分布（成功 580 次，按来源 × 决策 × 原因）

| 来源 | 决策 | 原因 | 次数 | 说明 |
|---|---|---|---|---|
| model（329） | CONTINUE_ACTIVE | `margin_continue` | 268 | 活跃任务同主题延续，266/268 最终路由 ACTIVE_HANDOFF（2 例 handback 迭代属正常） |
| model（329） | CLARIFY | `margin_uncertain` | 33 | 不确定→暂停任务并切 Main（应用 31 / 冲突 2） |
| model（329） | PREEMPT_TO_MAIN | `margin_preempt` | 28 | 明确离题→暂停任务切 Main（应用 27 / 冲突 1） |
| rule（251） | none | `resume_rule_no_match` | 244 | 暂停态逐轮检查恢复条件，未命中→留在 Main（暂停态**不再调用模型**，见发现 F3） |
| rule（251） | RESUME_PAUSED | `explicit_resume_paused_task` | 4 | 显式恢复关键词命中→恢复暂停任务（**4/4 语义误判**，见发现 F2） |
| rule（251） | PREEMPT_TO_MAIN | `explicit_pause_or_exit` | 3 | 显式退出词命中（真实用户“不讲了”1 次 ✓ + 拨测“退出”2 次 ✓） |

> **口径说明（本次修正）**：Gate 成功 = `status=success`，模型与规则来源**均计入成功**。规则路径不调用模型即产出决策（恢复检查、退出词、恢复词），是暂停/恢复语义的第一道关口；把它排除在“成功”外会导致对暂停态行为的系统性漏审——8/27 报告正是因此把暂停态 244 次规则评估笼统记为 `paused none` 而未审其语义质量。

### 2.2 决策 → 最终路由一致性矩阵

| Gate 决策（原因） | 最终路由分布 | 一致性结论 |
|---|---|---|
| CONTINUE_ACTIVE（`margin_continue`）×268 | ACTIVE_HANDOFF 266；MAIN 1；HANDOFF_INTENT 1 | ✅ 一致。2 个非 ACTIVE_HANDOFF 均为子代理 handback 后的合法多迭代（讲解完成交回 Main / 交回后再 handoff） |
| none（`resume_rule_no_match`）×244 | MAIN 238；HANDOFF_INTENT 6 | ✅ 一致。6 个 HANDOFF_INTENT 为 Main 接管后经工具调用主动创建/恢复任务，属主 Agent 决策权内 |
| CLARIFY（`margin_uncertain`）×33 | MAIN 21；HANDOFF_INTENT 10；ACTIVE_HANDOFF 2 | ✅ 一致。2 个 ACTIVE_HANDOFF 均为 apply conflict 保持原路由；10 个 HANDOFF_INTENT 为切 Main 后主 Agent 工具再切回（含“救回”假 CLARIFY，见 F4） |
| PREEMPT_TO_MAIN（`margin_preempt`）×28 | MAIN 21；HANDOFF_INTENT 6；ACTIVE_HANDOFF 1 | ✅ 一致。1 个 ACTIVE_HANDOFF 为 apply conflict；6 个 HANDOFF_INTENT 为 preempt 后 Main 就新话题再建任务（标准设计流） |
| RESUME_PAUSED（explicit resume）×4 | ACTIVE_HANDOFF 4 | ✅ 链路一致（但语义全错，见 F2） |
| PREEMPT_TO_MAIN（`explicit_pause_or_exit`）×3 | MAIN 3 | ✅ 一致 |

> 结论：**控制面（决策如何变成路由与状态）在本窗口没有发现任何一次不可解释的分叉**；所有“决策≠路由”的案例都落在三类已知机制内——handback 多迭代、conflict 保持原状、Main 工具二次决策。

### 2.3 终态异常归因（4/902）

| 类型 | 数量 | 归因 | 定性 |
|---|---|---|---|
| `chat stream failed` | 1 | 19:45:14，S09 最后一轮，Main 路径 `llm_call_count status=error`；该轮为丧亲话题的高敏感内容输入 | ❌ 服务端单点。与 8/27 的批量 stream fail 无关联（本窗口全天该错误仅 20 条、无聚集） |
| client disconnect | 2 | `chat stream aborted without complete frame (client disconnect)`，chunks=0/1 时客户端断开 | ◼ 客户端行为，服务端无故障 |
| 窗口边界 | 1 | 请求 20:59 进入，`chat_request_end` 落在 21:00:09（窗口外 9 秒） | ◼ 统计截断，扩窗复核确认正常完成 |

### 2.4 163 个多轮 Session 编排形态画像

| 形态 | Session 数 | 说明 |
|---|---|---|
| 纯任务连续（CREATE→CONTINUE…） | 62 | 单任务同主题多轮承接，一期核心路径 |
| 有 PAUSE 无 RESUME | 39 | 任务暂停后留在 Main（是否该恢复需语义判断，见 F3） |
| 纯 Main（含无任务闲聊/游戏） | 32 | 全程未建任务或仅 ENSURE_MAIN |
| Main 起步后建任务（含 tool_true） | 17 | 主 Agent 工具决策切入讲解 |
| 有 RESUME（gate 规则或 Main 工具） | 9 | 恢复分支，语义质量见 F2/F5 |
| 有 handback COMPLETE | 2 | 子代理主动完成交回 |
| 合成拨测（it-*） | 2 | gate fallback 降级验证，语义审计排除 |

### 2.5 统计级占比：Gate 决策面与任务编排面（v2 新增）

占比给两套分母：全部 902 个有效 on 请求（反映流量面贡献），以及对应状态的评估轮（反映决策面行为）。

| 决策类型 | 次数 | 占对应状态评估轮 | 占全部 on 请求（902） |
|---|---|---|---|
| **延续（CONTINUE_ACTIVE，含 fallback 降级 2）** | 270 | **80.8%**（/334 活跃任务态轮） | 29.9% |
| **抢断合计（含 CLARIFY）**：模型 CLARIFY 33 + 模型 PREEMPT 28 + 规则 exit 3 | 64 | **19.2%**（/334 活跃任务态轮）；其中 CLARIFY 9.9%、模型 PREEMPT 8.4%、规则 exit 0.9% | 7.1% |
| **恢复任务（RESUME_PAUSED，规则）** | 4 | **1.6%**（/248 暂停任务态轮），且 4/4 语义误判 | 0.4% |
| 暂停态不恢复留在 Main（none/`resume_rule_no_match`） | 244 | 98.4%（/248 暂停任务态轮） | 27.1% |
| 无任务/场景首轮 skip（不参与决策） | 320 | - | 35.5% |

> 另有一个重要的衍生比例：抢断/退出导致的 61 次 PAUSE 中，后续被任何形式 RESUME 的共 11 次（18%）。但其中 5 次是无回归意图的误恢复——**有真实意图支撑的恢复仅 6/61（≈10%），其中目标任务也选对的只有 4 次**；其余 82% 的暂停任务至 Session 结束仍悬置。

| 任务编排动作（on 请求内 571 次迁移） | 次数 | 占迁移总数 | 频率（次/100 请求） | 细分 |
|---|---|---|---|---|
| CONTINUE（延续活跃任务） | 277 | **48.5%** | 30.7 | trigger 全部为 active |
| **CREATE（创建任务）** | 146 | **25.6%** | 16.2 | 场景直入 87（59.6%）/ 主 Agent 工具 58（39.7%）/ 工具否定兜底（tool_false_fallback）1 |
| ENSURE_MAIN（Main 轮 Topic 保障） | 74 | 13.0% | 8.2 | - |
| **PAUSE（暂存任务）** | 61 | **10.7%** | 6.8 | 全部由 gate 决策触发（CLARIFY 31 / 模型 PREEMPT 27 / 规则 exit 3，含冲突未落地 3 次之外的实际落地数） |
| **RESUME（恢复任务）** | 11 | **1.9%** | 1.2 | gate 规则 4（4/4 误判）/ 主 Agent 工具 7（1 次无意图误恢复 + 2 次目标选错 + 4 次合理） |
| COMPLETE（handback 完成交回） | 2 | 0.4% | 0.2 | - |

### 2.6 并发统计与 CAS conflict 竞态定位（v2 新增）

并发定义：同一 Session 内两个请求的服务端处理区间（首条链路标记行至 `chat_request_end`）存在时间重叠。流式回复很长（讲万物单次可达 15-120s），而用户（尤其语音儿童用户）会在播报中途继续说话，并发是常态而非异常。

| 口径 | 数值 | 说明 |
|---|---|---|
| **Session 并发发生率** | **28.8%**（47/163） | 至少出现一对重叠请求的多轮 Session 占比 |
| 相邻请求对重叠率 | 11.4%（84/739） | 后一请求在前一请求未结束时到达 |
| 请求卷入率 | 14.6%（132/902） | 至少与一个同 Session 请求重叠过的请求占比 |
| 重叠时长分布 | p50 7.7s / p90 22.0s / max 120.9s | 长重叠集中在讲万物长旁白期间用户连续插话的 Session |
| **CAS conflict 与并发的关系** | 3/3 紧邻 | 与前一请求的间隙分别为 **-3ms（重叠）/ +47ms / +103ms**；冲突窗口是“前一请求异步尾部状态写 vs 当前请求 gate 快照→apply”，区间重叠不是必要条件，**紧邻快速连发（≤百毫秒级间隙）同样触发** |
| conflict 发生率 | 4.4%（3/68） | 占需落状态决策数；占全部 on 请求 0.33%。冲突后决策丢弃无重评，结果好坏随机（见 F6） |

> 注：区间以链路标记行为边界，未计入流结束后的异步尾部写（aimemory/状态落盘），因此并发率是下界；conflict 的竞态窗口实际比区间重叠更宽，与实测“3/3 紧邻触发”一致。

## 3. 语义级审计（18 个抽样 Session / 388 有效 on 轮）

抽样方式为**分支定向覆盖**（非随机）：确保规则恢复、工具恢复、handback、CAS 冲突、模型 PREEMPT/CLARIFY、LGI 回退、流失败、超长会话、普通会话各分支至少 1-2 个样本。语义判定以**整段 Session** 的用户 Query 序列、Gate 决策、Router 最终目标、Task 迁移顺序共同判断；单轮路由正确不代表 Session 级语义正确。

| 样本 | Session ID | 语义主线 | on 轮 | 关键链路事件 | 路由语义 | 任务编排 | 终态 | 关键发现 |
|---|---|---|---|---|---|---|---|---|
| S01 | `mentorx-zmvgttv8zqs8hz37452rxoc1qb1zeyuw` | 儿童闲聊：取名/唱歌/游戏下载（工具建任务后离题） | 97 | tool_true 建任务→模型 PREEMPT×2 ✓→规则 RESUME×1 ✗ | PARTIAL | PARTIAL | PASS | 唱歌语境说“继续啊，继续接还有呢”被恢复词规则误判，把 28 分钟前暂停的“巨人的花园”讲解任务错误拉回讲万物（下一轮用户即再度 PREEMPT 离开） |
| S02 | `mentorx-b43x9hq6p9c4eseia5x2llotdc1d0qgf` | 同一用户续聊：闻鸡起舞→取名/接龙闲聊 | 68 | 规则 RESUME×3 全错；CLARIFY 应用×2、conflict×1 | FAIL | FAIL | PASS | “我们继续聊吧”“接着，我们到时候再讲吧，我现在想休息一下”三次误触发恢复——最后一句语义是告别，却被恢复到讲解任务；任务被反复 RESUME/PAUSE 打摆 |
| S03 | `mentorx-jcm0bhjthfkef3yvbgkanqgkfssrrlvy` | 军事武器连续问答（机炮/坦克/军舰） | 21 | CONTINUE×17 ✓；CLARIFY×2（1 次被 Main 工具 resume 救回、1 次转建新任务） | PARTIAL | PASS | PASS | “军舰底部装甲为什么不加厚”明显是同主题延续却被判 uncertain；Main 工具恢复同一任务补救成功，代价是额外一跳；二战史转题建新任务合理 |
| S04 | `mentorx-djxz6nya0pm9giayb13rrf63j0mgubda` | 七律·长征讲解（含 ASR 噪声） | 10 | PREEMPT（用户抱怨跑题）；CLARIFY（ASR“新浪”→“细浪”）；3 个任务；LGI 14→0 | PARTIAL | FAIL | PASS | 用户投诉“讲的是七律长征，你怎么赶上球了？”被当离题 PREEMPT；诗内词语提问被 CLARIFY 切走；一首诗被拆成 3 个任务实例，主题连续性由 Main 工具反复缝合 |
| S05 | `mentorx-g6y8iy8cy2cvn7ljl104n5nenar67v0t` | 偏正短语讲解→练习 | 7 | CONTINUE×5 ✓→“能做几道题吗”→讲万物 handback COMPLETE→Main 出题 | PASS | PASS | PASS | handback 分支线上首次核验通过：讲解任务干净完结、Topic 交回、Main 无缝接管练习 |
| S06 | `mentorx-g2op4kd7yyfhgdzqeolc2z8e1lo4os9v` | 乘法口诀背诵→出题 | 5 | handback COMPLETE→Main→tool_false_fallback 再建任务切回讲万物 | PASS | PASS | PASS | 单请求内 handback + 再 handoff 的多迭代路由正确；`tool_false_fallback` 触发器值得关注（工具未显式请求 handoff 时的兜底建任务） |
| S07 | `mentorx-8egp0rl1e48m6sh41dlwcab1cgmgxqp2` | 荀彧字号→项羽许褚→无意义音 | 5 | CLARIFY 应用 ✓→新话题 tool 建任务 ✓→“下雨。”正确 PREEMPT 被 conflict 丢弃 | PARTIAL | PARTIAL | PASS | conflict 的负面案例：模型正确判定 PREEMPT，CAS 失败后决策静默丢失，无意义输入仍由讲万物承接 |
| S08 | `mentorx-whryf4m0igftd6h1gz4rofxyeg6m1qkm` | 山海经神兽 | 4 | “好。”被判 CLARIFY→conflict 拦下→维持讲解 | PASS | PASS | PASS | conflict 的正面案例：单字确认被误判 uncertain，冲突反而保住正确路由——与 S07 互为镜像，说明 conflict 结果是随机的而非机制性纠错 |
| S09 | `mentorx-78m3oy231citlndilrfpfu4p1mazhlad` | 满族历史→丧亲情绪倾诉 | 18 | CONTINUE×3 ✓→CLARIFY 暂停切 Main ✓→Main 情感承接 12 轮→末轮 stream failed | PASS | PASS | FAIL | 历史讲解→情绪支持的切换语义正确；但全场唯一的服务端失败恰好落在丧亲倾诉最敏感的一轮（llm error），且 12 轮高危情绪对话中未见安全升级动作（内容侧问题，超出路由范围但需转告） |
| S10 | `mentorx-qqb9ux3n6xvik5013j412jjw7uxd8cav` | 酒店 RevPAR 概念学习 | 11 | CONTINUE×10 全对；用户重发原始场景 Query→LGI 61→0 | PASS | PARTIAL | PASS | LGI 回退触发形态实锤：客户端重发场景初始 Query（携带 lgi=0）直接覆盖服务端高水位 61，下一轮从 1 重计 |
| S11 | `mentorx-e57golkj6c7m056vmtho4dc4au5jolpx` | 志愿军讲解→换话题→军队厨师 | 8 | “停止刚才的话题”PREEMPT ✓→新问题被 Main 工具 resume 回旧任务 ✗；LGI 10→0、5→0 | PARTIAL | FAIL | PASS | 用户显式说换话题并暂停成功，下一个全新问题却被 Main 工具“恢复”到刚被叫停的旧任务——任务身份与目标语义错位，叠加两次重发原始 Query 的 LGI 清零 |
| S12 | `mentorx-1mo0uf61z88apzs3lrl4hzin5a5a863x` | 商家跑路投诉/建议独白 | 14 | CONTINUE×9 ✓→“不讲了。”规则 exit ✓→Main 收尾 | PASS | PASS | PASS | 窗口内唯一一次真实用户触发 `explicit_pause_or_exit`，语义与执行完全正确 |
| S13 | `mentorx-ole3l0j9pdmm0epi2xc9mml1xzfozs12` | 静夜思→腿肿健康咨询 | 10 | 模型 PREEMPT→暂停旧任务→Main 就新话题 tool 建新任务→CONTINUE×8 ✓ | PASS | PASS | PASS | “换话题→暂停→新任务”的教科书式执行；一期设计意图的正向样板 |
| S14 | `mentorx-pnmfckgp3hqdnj3rwjr9bw7xt5fgu39s` | Minecraft 齿轮方块考据 | 46 | CONTINUE×18 ✓→“6 点睡觉”CLARIFY 暂停 ✓→用户下一轮即回归原主题，此后 24 轮全部 `resume_rule_no_match` 留在 Main | FAIL | FAIL | PASS | 恢复规则假阴性的极端案例：同主题延续无显式关键词就永不恢复（暂停态只跑规则、不请模型），讲解任务挂起 24 轮直至会话结束；Main 兜底回答且出现对用户错误游戏设定的逢迎（内容侧） |
| S15 | `mentorx-cv44yfzaqnvikr6lb4zm3ec8blkqmutj` | 小蝌蚪找妈妈→脑筋急转弯 | 23 | CLARIFY 暂停切 Main ✓→Main 玩游戏 20 轮；末轮 client disconnect | PASS | PASS | PASS | 活动切换（讲解→游戏）暂停合理，用户未再回原主题，任务保持 paused 正确 |
| S16 | `mentorx-jnhu9x3op36j141f170d8p0eatekaap9` | 猜谜游戏（全程无任务） | 33 | 33 轮 gate 全部 skip（无任务）→MAIN | PASS | PASS | PASS | 持续性游戏活动始终由 Main 承载、不建任务——行为一致；“游戏是否应任务化”是设计边界问题而非缺陷 |
| S17 | `mentorx-zzq2daqlmn81pn44ow8d3u8u12btwgp7` | MBTI/ESFP 隐喻讨论 | 4 | scene 建任务→CONTINUE×3 ✓ | PASS | PASS | PASS | 普通样本，全对 |
| S18 | `mentorx-t2ygxlnxxh9b3esmgftftxb36cokbevt` | 沟通技巧（戳破错误不伤人） | 4 | CONTINUE×3 ✓（含“接着来”，在**活跃态**由模型正确判 CONTINUE） | PASS | PASS | PASS | 与 S01/S02 对照：同样的“接着”，活跃态由模型判断即正确，暂停态关掉模型退回规则判断即出错 |

### 3.1 Session 级判定分布（18 样本）

- **路由语义**：PASS 11 / PARTIAL 5 / FAIL 2
- **任务编排**：PASS 11 / PARTIAL 3 / FAIL 4
- **终态执行**：PASS 17 / FAIL 1

> 样本为分支定向选择且刻意超采异常分支，以上计数不能外推为线上总体比例；线上多数流量是 S17/S18 型纯连续会话（163 个多轮 Session 中 62 个纯任务连续 + 32 个纯 Main）。

## 4. 核心发现

### F1（链路 ✓）主干控制面全量自洽，8/27 的两大链路疑点在本窗口不复现

- 902 请求无一例桶位错配、无一例决策-路由不可解释分叉、无 Task 迁移零失败。
- 8/27 报告的“Main 终态 0/10 全失败”为该时段 `chat stream failed` bug 所致；本窗口 Main-bound 474 个请求正常完成率 99.6%（仅 1 单点 + 2 客户端断开），**Main 分支基础履约能力本身没有问题**。
- 8/27 未验证的 PREEMPT_TO_MAIN / RESUME_PAUSED / handback / conflict 分支在本窗口均有真实执行，链路行为符合设计。

### F2（语义 ✗）暂停任务的规则式恢复——假阳性：命中 4 次全部错

- `explicit_resume_paused_task` 全窗口共 4 次，S01×1、S02×3（同一名儿童用户），**4/4 语义误判**。命中语句：“继续啊，继续接还有呢”（唱歌/接歌词）、“我们继续聊吧”（要继续取名）、“接着”（游戏接龙中）、“我们到时候再讲吧，我现在想休息一下”（**告别语**）。
- 共同错误模式：恢复词（继续/接着/再讲）**只做字面匹配，未校验当前对话活动与暂停任务的语义相关性**。被错误复活的讲解任务与当前语境已隔 12-28 分钟；恢复后用户立即再次 PREEMPT、再次打断，或被强行拉去讲解。S02 一个 Session 内 RESUME/PAUSE 摆动 3-4 次。
- 正向对照：S18 的“接着来”在**活跃态**由模型 margin 判断为 CONTINUE，完全正确——问题不在“继续类词本身不可判”，而在**暂停态缺少模型参与**，退回规则决策。

### F3（编排 ✗）暂停任务的规则式恢复——假阴性：同主题回归 24 轮不恢复

- S14：讲万物解释 Minecraft 齿轮方块 19 轮后，用户一句无关话（“6 点睡觉”）触发 CLARIFY 暂停——这一步正确；但用户**下一轮就回到原主题**并连续讨论 24 轮，每轮 `resume_rule_no_match`，任务挂起直到会话结束。
- 163 个多轮 Session 中有 39 个属“有 PAUSE 无 RESUME”形态，暂停态规则评估共 244 次。规则只认显式关键词，**基于话题相似度的恢复完全缺失**；这正是 8/27 报告 P1“Paused Task 无语义恢复”的问题，本窗口以更完整的证据复现。
- 与 F2 合并看：暂停态恢复判断**双向失准**——该恢复的不恢复（语义相同、无关键词），不该恢复的乱恢复（有关键词、语义无关）。方向性结论：恢复判断需要与活跃态同等的模型参与（或至少给规则加语义校验），而不是继续加词表。

### F4（语义 ~）CLARIFY 的双刃剑：Main 工具能救回假阴性，但代价是任务碎片化

- 假 uncertain 被救回：S03“军舰底部装甲为什么不加厚”明显延续军事讨论，被 CLARIFY 切到 Main 后，Main 工具当轮 resume 同一任务——净效果正确，多一跳延迟。
- 救回失败变碎片：S04 一首《七律·长征》被 PREEMPT/CLARIFY 拆成 3 个任务实例（含 ASR 噪声“新浪”），Main 反复用工具缝合，Task 目标与主题的对应关系已经失真。
- ASR 噪声是 CLARIFY/PREEMPT 误判的显著诱因（S04“新浪”/“细浪”、S09 大量方言语音转写），语音场景的 Gate 输入质量需要单列评估。

### F5（编排 ✗）Main 工具恢复的任务身份漂移

- S11：用户显式“停止刚才的话题，我要换一个”（PREEMPT 成功暂停），下一个全新问题（富豪为何雇军队退伍厨师）却被 Main 工具 RESUME 回刚被叫停的“志愿军八枪不倒”任务。路由结果（讲万物答题）用户无感，但任务目标与实际讨论内容错位，后续 checkpoint、续讲、记忆归属都会挂到错误的任务上。
- S04 的工具 resume 恢复的是“新浪是什么”子问题任务而非原诗讲解任务——恢复目标选择只有“最近暂停”一种策略，缺少与新 Query 的目标匹配。

### F6（链路 ~）CAS conflict 双向影响，决策丢弃后无重评

- 3 次 conflict：S08 拦下了错误 CLARIFY（净收益）；S07 丢掉了正确 PREEMPT（净损失）；S02 丢掉一次 CLARIFY（该轮语义本可暂停）。冲突保护本身按设计工作，但**被拒绝的决策直接消失、当轮不重评**，结果好坏纯看运气。
- 建议把“conflict 后当轮结果”纳入观测（当前只有 `state_update=conflict` 计数），并评估低成本重评（如冲突后按最新 revision 重跑规则段）。

### F7（编排 ✗）LGI 回退：客户端回传行为全量实测，“非 a2ui 卡必回 0”的说法不成立

针对客户端协调问题（客户端称“非 a2ui 卡的下一轮会回退到 0”），对 163 个多轮 Session 全部 903 个请求的 `chat_request_input.meta_data.last_global_index` 做了全量提取，与上一轮最终 responder（讲万物=a2ui frame 卡 / Main=纯文本）配对得到 740 个相邻对：

| 上一轮回复类型 | 相邻对 | 下一轮回传 lgi=0 | 下一轮回传 lgi>0 | 说明 |
|---|---|---|---|---|
| **非 a2ui 卡（Main 纯文本）** | 405 | **5（1.2%）** | **400（98.8%）** | 回 0 的 5 例中：2 例是新任务创建轮的合理 0（CREATE 从 0 起）、3 例无任务动作无害 |
| **a2ui 卡（讲万物 frame）** | 333 | 10（3.0%） | 323（97.0%） | 回 0 的 10 例：拨测 4 + 真实用户 6；真实 6 例全部是**重发场景入口原 Query**型行为，其中 5 例直接造成活跃任务非零→0 覆盖 |
| 其他（omni / 无标记夹缝请求） | 2 | 1 | 1 | 回 0 的 1 例即第 6 次回退（S10，61→0）；重置轮前 2.8s 有一条无链路标记的夹缝请求 |

- **客户端回传 LGI 的真实语义 = 会话全局消息序号（含文本消息）**，而非“a2ui 帧序号，非 a2ui 归零”。实证：S14 暂停后 24 个纯 Main 文本轮，客户端回传 63→64→…→88 逐轮 +1；S01 纯闲聊段同样逐轮递增。**回答客户端协调问题：并非所有非 a2ui 卡的下一轮都回传 0，实测回 0 比例仅 1.2%（98.8% 回传递增非零）**。
- 回 0 的真正触发形态是**场景入口卡重发/重入**（回退轮 Query 与首轮场景 Query 逐字相同，如“酒店每间售房收益，到底算啥”“志愿军八枪不倒，是兴奋剂吗”）；客户端把重入当作新会话起点携带 `last_global_index=0`，服务端直接赋值覆盖已到 61 的 checkpoint（与 8/27 报告源码结论一致：`redis_handoff.py` 无 `max()` 保护）。
- **协调结论**：服务端加高水位（`max(existing, incoming)` 或 CAS 单调）对现有客户端行为**无兼容风险**——正常轮次客户端永远回传递增值，不存在“合法的同任务归零”；唯一需要与客户端对齐的是**场景卡重发时的重入预期**：回 0 应被服务端识别为“重入/重讲”信号（显式重建任务或重新开讲），而不是写入 checkpoint；另需对 incoming=0 覆盖非零单独打点监控。

### F8（观察）需要转出路由范围的内容侧问题

- S09：12 轮丧亲/贫困/自杀相关高敏感倾诉全程由 Main 普通人设承接，未见安全升级或干预动作；唯一的服务端失败恰发生在敏感一轮（是否内容安全拦截需要内容侧核实 `llm_call error` 的具体错误类型）。
- S14：Main 对用户虚构的游戏设定（“空气齿轮方块”）持续逢迎确认，事实性幻觉配合。
- S13：健康/用药咨询（腿肿、肾炎吃什么药）由讲万物任务承接多轮；医疗类话题的任务边界与免责策略值得内容侧确认。

### F9（统计）暂停任务恢复：Session 粒度四点语义盘点（v2 新增，覆盖全部 48 个含 PAUSE/RESUME Session）

对窗口内全部 48 个含 PAUSE/RESUME 的真实用户 Session（61 次 PAUSE、11 次 RESUME、244 次暂停态评估）逐轮人工语义标注。**回归意图事件**定义：暂停/完成后，用户 Query 语义指向某个存在过任务的 topic（连续同目标轮次合并为一次事件）；强=明确要求继续/重发任务 Query/就该 topic 提出内容问题，弱=在其他活动语境中引用该 topic。

| 统计点 | 结果 | 明细 |
|---|---|---|
| **点 1：想回到“存在过任务的 topic”的回归意图事件总数**（不限最近任务） | **13 次**（强 10 + 弱 3，共 34 轮） | 典型形态：同主题内容追问（MC 齿轮 24 轮、爬天都峰 4 轮）、单字“继续”×4、重发任务原 Query×2、“从头再讲一遍”×1、退出后回头确认内容×1；另有 2 次弱引用（游戏语境提及、要求语音播报）。对照：244 次暂停态评估中绝大多数轮确实无回归意图（闲聊/新话题），“不恢复留在 Main”在大多数轮次上是对的 |
| **点 2：其中目标恰为上一个（最近暂停）topic/task** | **11/13（84.6%）** | 另外 2 次目标为更早任务（S04 想回《七律·长征》主任务而非最近的“新浪”子问题任务；二胡 Session 想回原二胡任务而非最近的“钢丝材质”任务）——**绝大多数回归需求指向最近任务，“只恢复最近一个”的能力设计在目标选择上大体够用，但见点 4** |
| **点 3：想回上一个 topic，但 resume 与 toolcall 均未兑现** | **8/11（72.7%）** | 未兑现 8（含强 6：MC 齿轮 24 轮、爬天都峰、单字“继续”×3、退出后回头确认；弱 2），全部由 Main 直接回答。兑现 3：tool resume 2（“继续”×1、“弄出来吧”×1）+ 新建重讲 1（“从头再讲一遍”→新任务，符合重讲意图）。nuance：未兑现中 1 次用户刚显式退出（“不讲了”后回头确认），Main 承接尚合理 |
| **点 4：想回更早的 topic，被错误 toolcall 恢复到上一个任务** | **2/2（100%）** | S04：说“七律·长征”想回诗讲解，tool 恢复了最近暂停的“新浪是什么”任务；二胡 Session：重发原任务 Query“二胡空弦有杂音”，系统 PAUSE 当前任务后又 tool 恢复了同一个“钢丝材质”任务（且伴随 lgi 25→0 覆盖）——**恢复目标选择只有“最近暂停”一种策略，想回更早任务的样本全部选错** |
| 反向：无回归意图却被恢复（误恢复） | 5 次 | 规则 4（S01×1、S02×3，“继续/接着/再讲”词面误中，含把告别语当恢复指令）+ 工具 1（S11 显式“换话题”后新问题被 resume 回旧任务） |
| **“继续”类显式指令的规则表现** | **召回 0/4，精确 0/4** | 暂停态下用户说单字/短句“继续”共 4 次：规则命中 0 次（3 次未兑现、1 次靠 Main 工具救回）；而规则实际命中的 4 次全部是非恢复语境误召。同样的词命中与否行为不一致（具体匹配条件需对照代码确认），但无论匹配逻辑如何，**纯词面规则在两个方向上都已被证伪** |
| 同轮缝合（抢断切 Main 后同轮 tool 再切回讲万物） | 16 次 | CLARIFY→HANDOFF_INTENT 10 + PREEMPT→HANDOFF_INTENT 6；用户无感知但多一跳延迟，且部分以新建任务而非恢复方式缝合（任务碎片化来源） |

> 方向性结论：① 回归需求真实存在但不高频（13 次 / 248 暂停态轮 ≈5%），且 84.6% 指向最近任务——一个 active + 一个 paused 的一期状态机容量设计与需求分布匹配；② 短板在判定而非容量：该回的 72.7% 没回去、不该回的回了 5 次、想回更早的 100% 选错目标；最可靠的恢复实际上是 Main 工具路径（LLM 参与），但它的目标选择和触发稳定性都不足——与 F2/F3 的定性结论互证：恢复判定需要模型参与 + 目标匹配，而非继续加词表。

## 5. 问题与处置优先级

| 优先级 | 问题 | 样本证据 | 影响 | 建议动作 |
|---|---|---|---|---|
| **P0** | Task LGI 无高水位保护，客户端重发原始 Query 即清零 | S10（61→0）、S11（10→0、5→0）、S04（14→0）等 5 Session 6 次；全量实测（F7）：非 a2ui 卡后下轮回 0 仅 1.2%，客户端 LGI 实为全局消息序号，危险归零只来自场景卡重发 | 断点续讲、去重、跨请求一致性读到错误 checkpoint | 持久化改 `max(existing, incoming)` 或 CAS 单调更新（实测对现有客户端行为无兼容风险）；与客户端仅需对齐“场景卡重发/重入信号”的处理语义（重建/重讲而非覆盖）；对 incoming=0 覆盖非零单独打点告警 |
| **P0** | 暂停态恢复判定双向失准（规则假阳性 4/4 + 假阴性长挂起） | F9 全量盘点：回归意图 13 次中该回的 72.7% 未兑现；误恢复 5 次（含把告别语当恢复指令）；“继续”指令规则召回 0/4、精确 0/4；想回更早任务的 2/2 被恢复到错误目标；语义正确的恢复仅 6/61 次暂停（≈10%） | 误恢复直接把无关任务推给用户；不恢复使任务永久悬置、讲解断档；目标选错污染任务身份 | 暂停态引入模型参与（或规则命中后模型复核相关性）；恢复词规则加“当前活动上下文”否决条件；以 paused task goal/topic × 新 Query 相似度做恢复候选与目标选择；同步落地 candidate_decision → apply_result → final_target → task_transition 的串联观测 |
| P1 | Main 工具 resume 的目标任务选择错误（身份漂移） | S11 显式换话题后新问题被 resume 回旧任务；S04 resume 到子问题任务 | 任务目标与实际内容错位，污染 checkpoint 与记忆归属 | resume 工具增加目标匹配（goal/topic vs 新 Query），不匹配时改走新建任务 |
| P1 | CLARIFY 边界含糊 + ASR 噪声诱发任务碎片化 | S03 延续问题被 uncertain；S04 一首诗拆 3 任务；S08 单字确认被 uncertain | 多余的 Main 中转与任务实例膨胀，Task 语义边界失真 | 建议回归集（收录本报告 S03/S04/S08/S14 案例与 ASR 噪声形态）；评估 margin 阈值与短语音输入的置信策略 |
| P1 | conflict 丢弃决策无重评、无结果观测；并发是常态 | S07 正确 PREEMPT 被丢弃；S08 错误 CLARIFY 被拦下（结果随机）；§2.6：Session 并发发生率 28.8%，conflict 3/3 发生在重叠或 ≤103ms 紧邻请求，发生率 4.4%（/需落状态决策） | 冲突轮的路由正确性不受控；并发常态化意味着冲突会持续发生 | conflict 后按新 revision 低成本重评；打点冲突轮最终路由与下一轮决策，评估真实损益；竞态窗口在“前请求异步尾部写”，可评估尾部写前置或带版本合并 |
| P2 | 内容侧转办：高敏感情绪对话无安全升级、事实逢迎、医疗话题边界 | S09、S14、S13 | 超出路由审计范围，存在合规与体验风险 | 转内容安全/人设团队核实（含 S09 末轮 `llm error` 的错误类型定位） |
| P2 | on 桶混入拨测流量；`tool_false_fallback` 触发器语义待确认 | 2 个 `it-stable-router-*` Session；S06 | 污染实验统计口径；兜底建任务路径缺少说明 | 实验统计排除 `it-*`；确认 `tool_false_fallback` 的设计意图与预期频率 |

## 6. 与 8/27 审计报告的关系

- **时间窗口选择生效**：8/27 报告受当日 `chat stream failed` bug 影响，10 个 Main-bound 请求 0/10 完成，导致“Main 分支端到端未成功”“Main 执行错误掩盖路由效果”等结论走偏。本窗口（8/26 00:00-21:00）该故障不存在，Main-bound 474 请求正常率 99.6%，**可确认 Main 分支链路本身健康，8/27 的 Main 终态问题应归因于当日故障而非路由架构**。
- **口径修正**：8/27 的“Gate 成功 = `status=success` 且 `source=model`”口径有误——Gate 成功不要求调用模型，规则决策同为成功。修正后本报告新增了对规则路径（251 次）的语义审计，并因此发现 F2/F3 两个此前不可见的核心问题。
- **结论延续与增强**：LGI 回退（8/27 P0）复现并补齐触发形态证据；Paused 恢复缺失（8/27 P1）从“未观测到 RESUME”升级为“规则恢复双向失准”；且本窗口已真实观测到 RESUME_PAUSED / PREEMPT_TO_MAIN / handback / conflict 全部分支，填补了 8/27 的验证空白。

## 7. 口径与定义

- **有效 on**：`prompt_get success` 日志中解析后的 `matched.edu_main_gate == 'on'`；不使用请求入口 bucket、SSE bucket 或文本模糊包含。matched 中无该键的请求视为未命中实验（不计 on/off）。
- **多轮 Session**：同一 `session_id` 下 ≥2 个不同 `request_id` 的有效 on 请求。
- **Gate 成功**：`handoff_gate: status=success`，**model 与 rule 来源均计入**；skipped（无任务/scene_bootstrap）与 fallback 单列。
- **链路正确**：桶命中、Gate 决策产出、Router 状态应用（成功或显式冲突）、最终路由与决策可解释一致、Task 迁移合法、终态完成，六层同时满足。
- **路由语义 PASS/PARTIAL/FAIL**：Session 整体视角下，切换/延续/恢复决策与用户意图一致为 PASS；个别轮次误判但被系统或用户低成本纠正为 PARTIAL；存在把用户明确意图导向相反路由（误恢复、长期不恢复）为 FAIL。
- **任务编排 PASS/PARTIAL/FAIL**：任务身份稳定、迁移与语义对应、checkpoint 单调为 PASS；存在 checkpoint 风险或轻度实例膨胀为 PARTIAL；任务身份错位、被反复错误拉扯或长期悬置为 FAIL。
- **终态 PASS/FAIL**：以服务端归因为准；client disconnect 与窗口边界不计 FAIL。
- **回归意图事件（F9）**：暂停/完成后用户 Query 语义指向某个存在过任务的 topic，连续同目标轮次合并为一次事件；强=要求继续/重发任务 Query/就该 topic 提内容问题，弱=在其他活动语境中引用。活跃任务期间重发场景卡不计入（归入 F7 重入行为）。
- **并发（§2.6）**：同 Session 内两请求的服务端处理区间（首条链路标记行至 `chat_request_end`）时间重叠；未计异步尾部写，为下界口径。

## 8. 方法论

1. 解析 `matched` 字典，得到 2,092 个有效 on 请求，聚合出 163 个多轮 Session（含 2 个拨测）。
2. 对 163 个 Session 逐一拉取六类链路标记行（`handoff_gate` 汇总、`handoff_gate_router_apply_count`、`dispatch_stream iteration`、`handoff_task_transition`、`chat_request_end`、`chat stream failed`），以（session_id, request_id）精确关联，构建 902 个 on 请求的逐请求链路档案，先完成全量链路级对账。
3. 按分支定向抽样 18 个 Session：规则恢复 2、工具恢复 2、handback 2、conflict 2、preempt 2、clarify 2、LGI 回退 2、流失败 1、超长 1、普通 2。
4. 对抽样 Session 拉取安全审核请求行中的用户 Query 原文与 Main Agent final 回复，按时间序合成完整 Session 时间线（Query + Gate 决策 + 状态应用 + 最终路由 + Task 迁移 + 终态），逐轮进行语义复核后给出 Session 级三维判定。
5. 异常项（fallback、conflict、no-end、stream failed、决策-路由不一致）逐个下钻至原始日志定位归因，不允许留“未解释”项。
6. **v2 客户端 LGI 实测**：全量提取 163 个 Session 全部 903 个请求的 `chat_request_input.meta_data.last_global_index`，与上一轮最终 responder（由 Router iteration 终态判定 a2ui/文本）配对统计 740 个相邻对；并用 S14/S01 的逐轮序列验证客户端 LGI 的全局消息序号语义。
7. **v2 暂停恢复四点盘点**：对全部 48 个含 PAUSE/RESUME Session 拉取全量用户 Query 时间线（含任务创建轮 Query 作为 topic 锚点），逐轮人工语义标注回归意图、目标任务与系统兑现方式。
8. **v2 并发**：用链路档案中每请求的标记行时间区间做同 Session 区间重叠检测，并对 3 个 conflict 请求单独计算与前一请求的时间间隙。

## 9. 局限性

- 语义抽样为分支定向、刻意超采异常，判定分布不能外推为总体比例；总体健康度以链路级全量数据为准。
- 规则恢复假阳性 4/4 来自同一用户的两个 Session，样本量小；结论“规则词面匹配缺少语境校验”由案例机制支撑，误触发率需更长窗口量化。
- Gate 输入原文日志现仅记录长度（`handoff_gate_input` 无全文），CLARIFY/PREEMPT 误判的归因基于用户 Query 与决策输出的对照，无法复核模型实际收到的上下文拼装。
- 子代理（讲万物）回复以帧日志片段与旁白索引核验为主，未做逐帧内容质量评审；本报告的“终态完成”指流式链路完成，不背书回复内容质量。
- S09 末轮 `llm_call error` 的具体错误类型（模型异常 vs 内容拦截）需要模型侧日志对账，本报告仅定位到发生位置。
- 本报告未做源码核对，机制解释（如 LGI 覆盖写入路径、恢复词规则的具体匹配条件）沿用 8/27 报告的源码解释性结论或留待代码确认，与本窗口日志证据相互印证。尤其“继续”一词规则命中与否行为不一致（F9），具体匹配逻辑需读代码后再下结论。
- F9 的回归意图为人工语义判定，存在主观边界（已用强/弱两档降低争议）；事件数量级小（13），比例结论（如 72.7% 未兑现）适用于定性方向而非精确发生率；并发率为区间下界口径（未计异步尾部写）。



数据来源：生产 SLS `ali-shenma-ai-log` / `edu-mentor`（GetLogsV2 全量拉取 + 本地解析对账）。样本编号 S01-S18 的真实 session_id 已列入 §3 表格（独立交付口径）；逐请求链路档案、客户端 LGI 全量数据、暂停恢复标注时间线与并发明细留存于内部工作目录（`.tmp/audit0826/`）。报告正文不含 user_id/utdid 等用户与设备标识；为支撑语义结论，正文引用了必要的最短用户话语片段。审计窗口内约束：`edu_main_gate` 实验桶由上游 Quark AB 平台按桶值相等匹配，本报告不假设 utdid 哈希分组。

*本文档由 `edubot-on-bucket-audit-2026-08-26.html`（v2）导出；精简交付版见同目录 `edubot-on-bucket-audit-2026-08-26-lite.html`。*

