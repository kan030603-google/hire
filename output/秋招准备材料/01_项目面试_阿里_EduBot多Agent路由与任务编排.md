# EduBot 多 Agent 路由与任务编排

> PROJECT INTERVIEW BRIEF 01｜从“每轮选 Agent”升级为可持续、可暂停恢复、可审计的多轮任务控制面

公司：阿里巴巴 \| 角色：核心开发 / 链路审计【具体岗位待确认】 \| 状态：一期已上线 + 目标架构设计

## 项目介绍口播

### 30 秒版本

我做的是教育大模型的多 Agent 控制链。核心不是把 Query 分成 Main 或 SubAgent，而是让专业任务在多轮里有稳定身份、能被抢断和恢复，并在并发、流式输出和异常情况下保持状态可解释。我参与收敛 Gate、Router、Task/Topic、ToolCall/Handback 链路，并用生产日志完成 163 个多轮 Session、902 请求的全链路审计，定位恢复判定和 LGI 单调性问题。

### 2 分钟版本

项目面向教育大模型的多轮专业任务承接。早期方案要么每轮让 Main 重新选择 Agent，成本高且不连续；要么只记 active\_agent，换题后容易粘住，也无法安全暂停恢复。我们把它抽象成 Gate + Router + Task 状态链：Gate 用高精度规则和单 token 小模型给出继续、抢断、恢复或澄清建议；Router 结合请求级实验配置和 revision 做最终决策，并维护 active/paused Task、Topic、Owner 和 LGI。Main 通过 ToolCall 发起 Handoff，SubAgent 通过 Handback 结束任务，Router 补 ToolResult 形成闭环。工程上关键是把 advice 和 effect 分开、用 CAS 防旧快照覆盖，并分别处理 Chat/Live/SSE 交付。我还做了生产日志审计：对 163 个多轮 Session、902 请求完成全链路对账，终态 898 个正常，同时发现暂停恢复规则精确率和召回都存在机制性问题、5 个 Session 出现 LGI 回退。对应方案是让恢复引入目标语义匹配、对 LGI 做高水位保护，并在 CAS conflict 后按新版本低成本重评。

## 一、项目背景与业务问题

Main Agent 适合开放问答、澄清和能力协调，SubAgent 适合持续讲解、辅导或练习。如果每轮都让 Main 重新选择，时延与成本上升且专业任务容易摇摆；如果只用 active\_agent 粘住，又无法表达换题、暂停、恢复、完成和同 Agent 多任务。因此需要一个独立控制面管理“本轮归谁”和“跨轮任务如何变化”。

| **方案**               | **优势**             | **核心缺陷**                                |
|------------------------|----------------------|---------------------------------------------|
| 每轮 Main 重新选择     | 灵活                 | 重复模型成本；相邻轮抖动；进度难恢复        |
| 只记 active\_agent     | 低时延               | 无法区分任务身份；易错误黏住；不能暂停/恢复 |
| Router + Task 生命周期 | 连续、可恢复、可审计 | 需要状态机、并发控制和协议闭合              |

## 二、我的工作与可交付结果

- 参与统一请求链：把 Scene Direct、Main ToolCall Handoff、active sticky、SubAgent Handback 统一收敛到 Router 循环。

- 参与 Handoff Gate 的规则优先与单 token 小模型方案，明确 advice、apply 与 final target 三层事实。

- 围绕 Session / Turn / Topic / Task / Owner / LGI / revision 建立状态语义，覆盖 CREATE、CONTINUE、PREEMPT、RESUME、COMPLETE。

- 梳理 ToolCall-ToolResult 闭合、Chat/Live/SSE 输出、异常 healing、CAS 与迟到结果等工程边界。

- 基于生产 SLS 做 on 桶全量链路对账、分支定向抽样与人工语义复核，定位 P0/P1 问题并给出可落地方案。

> **不要说过头** 一期已经具备 active/paused 两个互斥槽位、关键转换 CAS 和统一 Router；Task Registry、多 paused、真实 checkpoint/restore、command ledger 与 transactional outbox 是目标设计或算法 Demo 合同，不能表述为已完整上线。

## 三、业务链路

用户 Query  
→ 入口解析 / 安全审核 / 确定性干预  
→ PromptGet + Handoff Gate + Proactive Gate 并行准备  
→ Router 校验实验开关、revision 与最新 Task 状态  
→ MAIN \| SCENE\_DIRECT \| ACTIVE\_HANDOFF \| HANDOFF\_INTENT  
→ Main / SubAgent 流式执行  
→ ToolCall / Final / Handback / Error 再进入 Router  
→ SSE 交付 + 消息/状态持久化 + 观测闭环

### 五种任务动作

| **动作** | **触发**                       | **状态变化**                      | **LGI 规则**       |
|----------|--------------------------------|-----------------------------------|--------------------|
| CREATE   | Scene 首轮或 Main 新建 Handoff | Main → 新 active；清旧 paused | 新任务从初始值开始 |
| CONTINUE | 当前 Query 仍属 active Task    | 保持 Task/Topic/Owner             | 吸收合法 ACK       |
| PREEMPT  | 换题或明确暂离                 | active → paused；本轮 Main    | 先保存最新确认值   |
| RESUME   | 明确恢复且目标唯一             | paused → active               | 恢复 paused 保存值 |
| COMPLETE | SubAgent Handback              | 清任务并交回 Main                 | 完成后不可恢复     |

## 四、核心算法设计

### 4.1 规则优先 + active continuity 小模型

- 高精度规则先处理明确继续、形式调整、系统控制、暂停/退出、情绪支持等后果清晰的表达。

- 有 active 且规则未命中时，小模型只输出 0/1，不生成业务答案；输入仅保留当前 Query、上一轮用户话术、任务目标、Topic、最近进度和 Agent 能力。

- 用 margin = log P(Main) - log P(SubAgent) 做双阈值：明显继续、明显回 Main、中间不确定区。配置示例为 ≤ -0.5 继续，≥ 2.875 抢断，中间 CLARIFY。

- 异常时如果已确认合法 active，优先 CONTINUE\_ACTIVE；状态本身读失败则不做写操作。这里的 fail-open 是用户连续性开放，观测必须标记 fallback。

### 4.2 paused-only 恢复与为什么后来发现它有问题

一期只有一个 paused 候选，恢复规则尝试综合 Task ID、目标/摘要关键词、Agent 别名和 resume cue。设计初衷是降低误恢复，但线上审计表明纯词面规则既会在“继续聊/接着告别”等语境中误召，又会漏掉真正的短句“继续”和同主题内容追问。更合理的方向是规则召回 + 语义复核，并用 Query 与 paused goal/topic 的匹配分数选择目标。

## 五、工程设计

### 5.1 权责与状态一致性

- Gate 只产 advice；Router 校验实验配置、revision 和目标 Task 后才提交 effect；Task Store 不理解语义。

- 请求级配置做快照，避免 Gate、Router、Main 在同一请求里读取不同版本。

- 关键 PREEMPT/RESUME 使用 revision + CAS，复合状态在 Redis 事务中写入；dispatch 前再次确认 active Task。

- 所有终态应绑定 session\_id + task\_instance\_id + command\_id + expected\_version，避免同 Agent 的旧结果污染新 Task。

### 5.2 协议闭合与流式交付

- Main 交给 SubAgent 是 ToolCall；任务完成或抢断回 Main 时必须补同 tool\_call\_id 的 ToolResult，且新 Query 位于闭合结果之后。

- active 期间 ToolCall 暂时 orphan 是合法开放协议；Handback、Preempt、过期 healing 时必须闭合。

- Task LGI 是任务续传位置，Session 高水位保证全会话帧单调；两者都不是 Agent checkpoint。

- Chat SSE、Live SSE 与 JSON 共用 Router，但 final/error/handoff 的外部表达不同，需要分别验收。

### 5.3 CAS 还不够

CAS 防止旧快照覆盖新状态，但不能防重复命令、状态已提交而 dispatch 未发生、SSE 已发而消息未落盘、同 Session 双流和跨 Worker 主动候选重复消费。目标方案是 command ledger + request owner/epoch + checkpoint/restore + transactional outbox + 下游幂等。

## 六、线上审计与实验结果

| **证据**     | **结果**                                   | **面试解释**                                     |
|--------------|--------------------------------------------|--------------------------------------------------|
| 链路全量核验 | 163 多轮 Session / 902 请求；898 正常终态  | 证明控制链可对账，不等于语义全对                 |
| 决策         | 580 次成功：模型 329 + 规则 251            | 规则路径也是 Gate 成功，不能漏算                 |
| 并发         | 47/163 Session 有重叠；相邻对重叠 11.4%    | 并发是常态；CAS conflict 需重评                  |
| 恢复质量     | 规则触发精确 0/4；显式“继续”召回 0/4       | 样本小但机制缺陷明确；需语义复核                 |
| LGI          | 5 个 Session、6 次非零 → 0             | 场景入口重发导致；服务端需高水位保护             |
| 单日 A/B     | 转讲万物 +14.9pp；退出 -4.7pp；放弃 -2.5pp | 1128/197 Sessions，真实干预率 6.7%，只作初步证据 |

> **指标口径** 实验收益必须同时说出窗口、样本量和真实干预率。线上审计的 18 个语义样本是分支定向抽样，不能把 PASS/PARTIAL/FAIL 分布外推为总体比例。

## 七、关键问题、复盘与下一步

| **问题**              | **根因**                            | **改进**                                                 |
|-----------------------|-------------------------------------|----------------------------------------------------------|
| 恢复规则假阳性/假阴性 | 词面 cue 缺少当前活动语境与目标匹配 | 规则召回后模型复核；goal/topic 与 Query 相似度；候选消歧 |
| LGI 回退              | incoming=0 直接覆盖非零 checkpoint  | max(existing,incoming) 或 CAS 单调；重入单独建模         |
| CAS conflict 无重评   | 正确建议和错误建议都可能被随机丢弃  | 按新 revision 重跑低成本规则；记录冲突轮终态             |
| 任务碎片化            | CLARIFY 与 Main 工具反复新建/恢复   | 统一 CLARIFY 语义；同 Topic 恢复与新建边界               |
| 状态/dispatch 分裂    | 跨系统无事务                        | next state + command result + event + outbox 同事务      |

## 八、面试高频问答

### Q1. 为什么这不是普通意图分类？

**建议回答：**意图分类只回答当前文本像什么；本项目还要结合已有任务、状态版本和恢复资格决定本轮 owner，并维护跨轮 Task 生命周期、协议与流式进度。

- 追问展开：用‘换题—临时问答—恢复—完成’四轮例子解释状态变化。

### Q2. Gate 和 Router 为什么分开？

**建议回答：**Gate 是可替换的算法建议层，Router 是控制权威。分开后可以 shadow、分桶、校准模型而不直接改状态，也能由 Router 做开关、revision、CAS 和最终兜底。

- 追问展开：Gate hit 不能当业务成功，至少还要看 apply、transition、final target 和用户可见结果。

### Q3. 为什么规则在模型前？

**建议回答：**明确暂停/继续等高精度控制意图不需要模型；规则更快、可解释，在历史或模型异常时仍可工作。模型只处理语义模糊区。

- 追问展开：规则不能无限扩张，恢复规则的线上失准正说明复杂语义需要模型参与。

### Q4. 为什么用 logprob margin 而不是最大概率？

**建议回答：**margin 直接表达 Main 与 SubAgent 两个动作的相对证据，并支持不对称双阈值：自动继续和自动抢断的风险不同，中间区交 Main 澄清。

- 追问展开：阈值必须按场景与误判成本校准，不是固定常数。

### Q5. CLARIFY 为什么难？

**建议回答：**它既可以是瞬时让 Main 问一句，也可以被实现成 durable pause。两种语义会影响任务是否可恢复和是否产生碎片，必须先冻结产品合同再编码状态机。

### Q6. 为什么 active 和 paused 不能同时存在？

**建议回答：**这是一期容量简化，不是理论要求。它降低状态组合和恢复消歧成本，但新任务会覆盖旧 paused 的恢复资格。审计显示多数回归指向最近任务，容量暂时可用，短板主要在判定。

### Q7. Topic 和 Task 有什么区别？

**建议回答：**Topic 是语义上下文边界，Task 是某个 Agent 对该目标的一次执行。一个 Topic 最终可以关联多次 Task；一期 topic\_id==task\_id 只是迁移简化。

### Q8. LGI、progress summary、checkpoint 有什么区别？

**建议回答：**LGI 回答客户端播放到哪里，summary 回答用户看到了什么，checkpoint 回答 Agent 内部执行到哪个可恢复安全点。只有 checkpoint 能真正恢复内部工作流。

### Q9. 为什么 CAS 不够？

**建议回答：**CAS 只保护基于旧版本的写，不解决相同 command 重放、状态与 dispatch 原子性、迟到结果、双流和 SSE/持久化分叉，所以还需要幂等台账、owner/epoch、outbox 和 settled。

### Q10. ToolCall 为什么必须闭合？

**建议回答：**Main 的模型历史要求每个 ToolCall 有配对 ToolResult，否则会认为工具仍未完成，导致重复调用或上下文非法。闭合还必须与最新 Query 保持正确顺序。

### Q11. 线上审计怎么做？

**建议回答：**先按 matched.edu\_main\_gate 精确取 on 请求，再以 session\_id+request\_id 关联 Gate、apply、dispatch、transition、终态六类标记做全量对账；异常分支定向抽样，合成整段时间线做语义复核。

- 追问展开：说明分支抽样不能外推总体比例。

### Q12. 你发现的最关键问题是什么？

**建议回答：**暂停恢复规则双向失准和 LGI 非零回退。前者导致任务误拉回或长期悬置，后者破坏续播单调性；两者都通过全量链路和语义样本相互验证。

### Q13. 如果重做恢复，你会怎么设计？

**建议回答：**先召回合法 paused 候选，再用 Query、goal、topic、recent progress 做语义匹配；规则只处理显式 Task ID 和高精度 cue；低置信度交 Main 澄清，并记录候选、评分、apply 与实际恢复效果。

### Q14. 怎么降低首 token 时延？

**建议回答：**并行 PromptGet/Gate/Proactive，规则前置，active sticky 绕过 Main，Gate 单 token+短 deadline+连接池；但只取消最终不需要的分支，不能把投机 hint 当已提交 route。

### Q15. 目标架构为什么需要 outbox？

**建议回答：**状态提交和跨服务 dispatch 无法放进同一个远程事务。把 dispatch record 与 next state 同事务写入，再由 worker 至少一次投递、下游按 command\_id 幂等，覆盖进程中途崩溃窗口。

## 九、补充八股与项目映射

以下问题来自通用 Agent 面试题库；回答已按本项目的事实边界筛选。通用方案不等同于当前线上能力。

### Q1.1. 一个完整的 Agent 智能体架构一般包括哪些部分？（字节）

建议回答：我通常把 Agent 拆成七部分：输入与安全门禁、模型与指令、规划或路由、工具注册与执行、短期状态和长期记忆、运行时编排、输出与观测。模型只负责提出决策或工具意图；执行器负责参数校验、权限和真实副作用；状态库保存可恢复事实；编排器控制循环、超时和终止条件。关键边界是不能把模型自然语言直接当成系统状态，真正的状态变更应由确定性控制面提交。

项目边界：EduBot、主动服务。

### Q1.2. ReAct 范式是什么？有什么优缺点？（阿里、腾讯、美团）

建议回答：ReAct 是 Reasoning 与 Acting 交替的范式：模型基于目标和当前观察形成下一步判断，发起工具动作，再依据工具返回继续规划，直到得到答案。优点是能先查证再回答、根据环境反馈修正计划，并留下可诊断的动作轨迹；缺点是调用轮数多、时延和成本高，早期错误可能传播，还可能循环或受工具结果中的提示注入影响。生产上要限制工具范围、步数、预算和超时，用结构化状态代替无限自由反思。

项目边界：EduBot 可作延伸对比，但仓库没有证据表明项目采用了经典 ReAct Prompt。

### Q1.3. Agent 执行任务时，Thought、Action、Observation 如何循环协作？（蚂蚁）

建议回答：Thought 表示基于目标、状态和上一次结果形成的下一步判断；Action 是结构化工具调用、状态操作或最终回答；Observation 是执行器返回的成功结果、空结果或错误。运行时把 Observation 写回工作状态，再进入下一轮判断。工程上不依赖或展示完整隐式思维链，只记录计划摘要、动作和结果；每轮还要校验权限、参数和任务版本，并在成功、不可恢复错误、最大步数或时间预算到达时终止。

项目边界：EduBot，可映射为 Main ToolCall、Router 调度、SubAgent 结果或 Handback、再次进入 Router。

### Q1.4. Agent 执行失败时，如何做错误重试和反思？具体实现逻辑是什么？

建议回答：我不会对所有失败统一重试。先按错误类型分流：网络错误、408、429、5xx 等瞬时错误做有限次数的指数退避加抖动；参数、权限和内容安全错误直接停止；语义失败则把结构化错误、已完成步骤和剩余工具反馈给规划器，最多重规划一次。副作用调用必须带幂等键，整体设置次数、时间和 Token 上限。所谓反思应产出可验证的错误分类和下一动作，而不是无限生成自我评价；最终仍失败就降级、返回部分结果或说明缺失信息。

项目边界：EduBot、主动服务、用户画像。

### Q1.5. 为什么 Agent 经常出现幻觉或乱调工具？怎么缓解？（阿里）

建议回答：常见原因是工具描述重叠、一次暴露过多工具、参数 Schema 太宽、上下文存在冲突指令，或模型在证据不足时仍被要求必须完成任务。缓解时先用路由或状态机缩小可用工具集合，写清每个工具的适用与禁用条件，参数使用严格 Schema、枚举和服务端校验，并把工具返回当作不可信输入。高风险动作还要鉴权、幂等和确认；证据不足时允许拒答，并持续监控误调用率、无效参数率和循环次数。

项目边界：EduBot；智能搜索可映射到证据优先和不足时兜底。

### Q1.7. Agent 和 Workflow 有什么区别？（京东）

建议回答：Workflow 的步骤、分支和终止条件主要由开发者预先定义，强调稳定、可复现和可测试；Agent 则由模型根据目标和环境动态选择下一动作，适合路径难以穷举的开放任务，但成本和风险更高。实际生产通常采用混合结构：外层 Workflow 控制权限、状态和关键节点，模型只在分类、规划或生成等不确定节点做选择。是否使用 LLM 不是判断标准，关键看执行路径的控制权主要在代码还是模型。

项目边界：四个简历项目都相关；EduBot 是 Agent 与确定性控制面的混合，主动服务是分层图，智能搜索和用户画像主要是 Pipeline。

### Q2.1. 介绍一下 Function Call 的流程，模型如何知道应该调用哪个工具？（快手、虾皮、小红书、字节）

建议回答：应用先把工具名称、用途、参数 Schema 和约束随请求提供给模型；模型结合用户意图、系统指令和工具描述，选择直接回答或生成结构化 ToolCall。应用收到后做 Schema、权限和业务校验，真实执行工具，再用对应 call\_id 把 ToolResult 传回模型；模型据此生成答案或继续调用。模型不知道函数内部实现，只根据元数据做概率选择，因此宿主还要用工具白名单、tool choice 和状态机控制实际可调用范围。

项目边界：EduBot 的 Main ToolCall Handoff。

### Q2.2. Function Call 返回的 JSON 不标准怎么解决？（阿里）

建议回答：优先使用原生 Function Calling、严格 JSON Schema 或 Structured Outputs，而不是让模型自由生成一段“像 JSON”的文本。服务端仍要解析并做 Schema 校验，拒绝未知字段、错误类型和非法枚举；失败时把精简的字段级错误返回模型，允许有限次数修复。语法层只能做确定性的安全归一化，涉及下单、写库等副作用时不能猜测参数；超过重试上限就受控失败，并记录原始输出、模型版本和 Schema 版本。

项目边界：EduBot。

### Q2.3. 工具调用超时或返回空值时，如何设计 Prompt 让 Agent 反馈用户？（快手）

建议回答：工具应统一返回结构化状态，例如 success、empty、timeout、error，并附带是否可重试和哪些信息可以向用户公开；Prompt 明确禁止把失败解释成“业务数据不存在”。瞬时超时且调用幂等时可以有限重试，再尝试缓存或备用工具；真实空结果则说明当前条件下未查到，并建议用户补充范围。写操作的提交状态不确定时不能盲目重试，应先查询状态。最终只向用户说明影响和下一步，不泄露堆栈、密钥或内部服务名。

项目边界：EduBot；主动服务有相似的超时静默降级，但它不是 Function Call 项目证据。

### Q2.4. 多个工具调用链路如何调度？是否有异常 fallback 策略？（阿里、淘天）

建议回答：先把调用关系建成依赖图：有数据依赖的步骤串行，无依赖的只读步骤可以并行；调度器再根据意图、当前状态、权限、成本和时延预算选择工具。每次调用都带 call\_id、deadline、幂等键和状态，结果标准化后再进入下一步。可选工具失败时可跳过、切缓存或备用源并返回部分结果；关键工具失败则停止后续依赖。整体还要限制调用次数和循环深度，并用链路追踪区分选择错误、执行失败和降级成功。

项目边界：EduBot；当前项目是一轮一个 Handoff Tool，任意多工具 DAG 属于通用方案，不是一期现状。

### Q2.5. 大模型经常捏造工具调用参数，怎么处理？（阿里）

建议回答：参数不能由模型单方面决定。定义工具时尽量使用严格 Schema、枚举、长度和格式限制，关闭额外字段；用户可读名称先由服务端解析成真实 ID，不允许模型编造数据库主键、路径或权限。调用前执行 Schema、业务范围、资源存在性和授权校验；缺少关键值时向用户澄清，非法值返回字段级错误，最多修复一次。高风险写操作增加预览确认和幂等键，确保重复调用不会产生重复副作用。

项目边界：EduBot。

### Q3.4. Function Calling、MCP、Tool Use 三者是什么关系？（虾皮、携程、字节）

建议回答：Tool Use 是上层概念，指模型借助外部能力完成任务；Function Calling 是模型 API 中表达工具选择和结构化参数的一种机制；MCP 是宿主与工具提供方之间进行发现、连接和调用的协议。典型链路是宿主从 MCP Server 获取工具定义，注册给模型，模型生成 Function Call，宿主校验后发起 MCP tools/call，再把结果反馈给模型。Function Calling 也可以直接调用本地函数，不必经过 MCP。

项目边界：EduBot 可用来解释 ToolCall，但该项目没有使用 MCP。

### Q4.8. 不同 Agent 之间的记忆如何共享？（百度）

建议回答：我会采用“权威存储加受控投影”，不让 Agent 直接共享全部 Prompt 或私有运行时。用户级稳定事实可以共享；Topic 摘要按话题读取；Task 的目标、进度、工具状态和 checkpoint 必须按 task\_id 隔离。Router 根据接收方权限只投影必要字段，交接时传递结构化摘要、来源、时间和版本，并用 ToolCall/ToolResult 闭合协议。共享状态还要有单一写入权、版本校验和审计日志。

项目边界：EduBot。

### Q5.1. 如何让多个 Agent 协同工作？举一个具体的协同机制例子。（字节）

建议回答：核心是统一控制面，不让多个 Agent 随意互调。以 EduBot 为例，Main 通过 ToolCall 表达移交意图，Router 创建带 task\_id、topic、goal 和 owner 的任务，再调度专业 Agent；专业 Agent 完成后通过 Handback 返回，Router 补齐 ToolResult 并切回 Main。用户中途换题时，Gate 只给出 PREEMPT 建议，Router 将旧任务暂停；用户返回后再恢复原任务和进度。状态变更用 revision 和 CAS 防止旧请求覆盖新状态。

项目边界：EduBot。

### Q5.2. 多 Agent 协作常见模式有哪些？（阿里）

建议回答：常见模式包括：Supervisor 把子 Agent 当工具统一调度；Handoff 在不同 Agent 间转移对话控制权；Router 按领域分流并可并行汇总；固定 Pipeline 按预定义步骤串联；共享黑板让多个角色围绕同一状态协作；以及辩论、评审或投票模式。选择依据是是否需要直接与用户连续对话、任务能否并行、上下文是否要隔离，以及控制权是否必须集中。EduBot 属于 Router 与 Handoff 的组合。

项目边界：EduBot。

### Q5.3. 如何保证多智能体协同的效果？（小红书）

建议回答：不能只看接口是否成功，我会从合同、不变量和评测三层保证。合同层统一输入输出、任务身份、ToolCall 配对和失败语义；状态层保证单一 owner、版本校验、幂等、超时与迟到结果隔离；评测层同时检查路由准确性、任务迁移、最终 responder、时延和业务结果。EduBot 对 163 个多轮 Session、902 个请求做过全链路对账，技术终态基本正常，但语义抽样仍发现恢复判定和 LGI 单调性问题，说明链路成功率不能代替任务级验收。

项目边界：EduBot。

### Q5.4. 什么情况下选择单 Agent，什么情况下选择多 Agent？（快手）

建议回答：如果任务边界单一、工具数量少、上下文可以共享，而且同一套权限和评价标准即可覆盖，我优先选择单 Agent，因为链路更短、成本和故障面更小。只有当领域知识和工具明显异构、需要不同权限或长期任务状态、需要并行处理，或者单模型因工具过多而路由不稳时，才引入多 Agent。增加 Agent 数量本身不是收益，必须用任务成功率、成本、时延和可维护性证明拆分有效。

项目边界：EduBot、主动服务。

### Q5.5. 项目中子 Agent 之间的上下文怎么传递？（阿里）

建议回答：不应复制整段会话，而应传递最小任务包：目标、约束、当前 Query、task\_id、topic、版本、必要的稳定事实、已有产物引用和期望输出 Schema。工具中间过程和未完成计划按 Task 隔离，交接方返回结果、证据、不确定性和剩余事项。EduBot 当前由 Router 投影 task\_goal、previous\_user、progress\_summary、LGI 等有界状态；完整 task-scoped context、checkpoint\_ref 和多 paused Registry 仍是目标设计。

项目边界：EduBot。

### Q5.6. Agent 之间如何实现相互调用？（美团）

建议回答：常见做法是把子 Agent 封装成带结构化 Schema 的工具，或者通过 Handoff 协议转移控制权。无论哪种，都应携带 task\_id、call\_id、deadline、幂等键和明确返回协议，避免递归调用与重复副作用。EduBot 中 Main 发出 ToolCall，由 Router 准备任务后调用 SubAgent；SubAgent 用 Handback 返回，Router 补 ToolResult 闭环。当前没有开放任意 SubAgent 直接调用另一个 SubAgent，能力切换必须回到 Router。

项目边界：EduBot。

### Q5.7. 多 Agent 协作如何做上下文管理和共享？（小红书）

建议回答：可以把上下文分成 Session、Topic、Task 和 Turn 四层。Session 放身份、稳定偏好与公共策略；Topic 保存话题摘要和相关事实；Task 保存该次执行的目标、进度、工具状态与 checkpoint；Turn 只放本轮 Query、路由建议和流式中间状态。Main 默认读取当前 Topic 和受控的 Session 信息，SubAgent 默认只读自己的 Task，需要跨层信息时由控制面显式投影，并通过权限、版本和来源信息防止串扰。

项目边界：EduBot。

### Q6.2. 主 Agent 规划任务时，怎么保证拆解步骤合理？用了哪种 Prompt 策略？（阿里）

建议回答：我会让模型按固定 Schema 输出 step\_id、目标、依赖、所需工具、预期产物和终止条件；Prompt 中提供总目标、已知事实、约束、可用工具、禁止操作、最大步骤数和少量正确示例。生成后仍由程序校验依赖闭环、参数类型、权限和预算；执行中根据 Observation 局部重规划，而不是一次计划到底。项目中如果模型只负责路由或分类，就应明确它不是通用任务分解器。

项目边界：EduBot，用于说明 Main 只表达 Handoff 意图，Router 才是状态权威。

### Q6.3. Agent 如何结合工具、知识和规划自主运行？

建议回答：可以把它理解为 Goal、Plan、Act、Observe、Update 的闭环。知识通过 RAG 或结构化记忆按需进入当前状态；工具以明确 Schema、权限和超时暴露；Planner 根据目标、约束和已有 Observation 选择下一步；执行结果写回状态，满足完成条件则结束，否则修正计划或降级。生产环境还要限制最大轮数、危险工具审批、幂等和可追踪性，保证“自主”仍处在可控边界内。

项目边界：EduBot。

## 资料依据与表达边界

> **边界** “当前一期”“算法 Demo”“目标形态”须分开陈述；线上审计结论可以说，未落地的 Task Registry、checkpoint/outbox 只能说设计或演进方案。

主要依据：

> 阿里实习材料/01-路由与Handoff完整业务逻辑.md；阿里实习材料/02-任务编排与Topic完整设计.md；阿里实习材料/04-统一架构与链路工程设计.md；阿里实习材料/edubot-on-bucket-audit-2026-08-26.md；阿里实习材料/各类实验收益.txt
