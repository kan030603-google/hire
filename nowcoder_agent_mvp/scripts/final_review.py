from __future__ import annotations

import json
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "september-2026"
EXTRACTED = DATA / "extracted-questions.jsonl"
CLEAN = DATA / "clean-posts.jsonl"
ORGANIZED = DATA / "organized-questions.json"
REPORT = DATA / "final-review-report.json"
OUTPUT = ROOT / "output" / "agent_ai_interview_question_bank.md"


@dataclass(frozen=True)
class Bucket:
    code: str
    topic: str
    canonical: str
    patterns: tuple[str, ...]
    answer: str | None = None


NEW_ANSWERS = {
    "A29": """Agent Loop 必须由程序侧控制停止条件，不能把“是否继续”完全交给模型。工程上同时设置最大步数、总时长、Token/费用预算、连续无进展次数和重复动作检测；任务完成、不可恢复错误、用户取消或风险门禁触发时立即结束。\n\n+- 每轮记录目标、状态、动作、观察与剩余预算；只有观察带来状态变化才允许继续。\n+- 对相同工具和近似参数反复调用、结果无变化、模型反复改写同一计划等情况做指纹去重并熔断。\n+- 工具错误按可重试、不可重试和需人工确认分类；重试采用退避与抖动，且计入全局预算。\n+- 达到上限时返回可解释的部分结果、未完成项和恢复令牌，而不是伪造成功。\n\n常见追问：最大步数如何按任务复杂度动态设置？怎样区分探索过程与无效循环？""",
    "A30": """长任务恢复的核心是“状态持久化 + 幂等执行 + 可重放事件”。把任务状态机、已完成步骤、工具请求与结果、外部副作用和版本号写入持久化存储；重启后从最近一致检查点恢复，而不是重新让模型猜测进度。\n\n+- 每个任务、步骤和副作用使用稳定幂等键，写操作采用唯一约束、事务消息或业务侧去重。\n+- 状态更新使用乐观锁或 CAS，避免多个 Worker 同时接管造成覆盖；租约超时后才能重新认领。\n+- 外部调用前记录意图，调用后记录结果；对“请求已发出但结果未知”设计查询、对账或补偿流程。\n+- Prompt、模型、工具和 Schema 都要带版本，恢复时校验兼容性；不兼容则安全终止或迁移。\n\n常见追问：如何处理恰好在外部下单成功后宕机？本地状态与远端状态冲突时谁是事实源？""",
    "A31": """Agent 可观测性应围绕一次任务的完整轨迹建设，而不是只收集应用日志。用统一 trace ID 串联模型调用、工具调用、状态转移、检索、重试和人工干预，并记录结构化输入摘要、输出、耗时、Token、费用和错误分类。\n\n+- 指标至少覆盖成功率、步骤完成率、P50/P95 延迟、单位成功成本、工具错误率、重试率和安全拦截率。\n+- 保留可脱敏重放的轨迹与制品版本，支持从最终坏结果定位第一个错误步骤。\n+- 告警应基于 SLO 与基线偏移，区分模型、工具、数据、编排和外部依赖故障，避免只按 HTTP 500 告警。\n+- 对敏感内容做字段级脱敏、访问控制和保留期限管理，不能为了可观测性泄露 Prompt 或用户数据。\n\n常见追问：怎样把一次回答错误归因到检索、模型还是工具？哪些轨迹可以安全用于回放？""",
    "A32": """多模型 Runtime 应把厂商差异收敛在适配层，对上提供稳定的消息、流式输出、结构化输出、工具调用、错误和用量接口。业务层依赖能力声明而不是具体模型名，路由器再按质量、时延、成本、地域和合规要求选模型。\n\n+- 定义内部统一 Schema，并显式转换各厂商的 role、tool call、finish reason 和多模态格式。\n+- 用 capability registry 标记上下文长度、JSON/工具能力、限流与价格；不支持的能力应明确失败或降级。\n+- 统一超时、重试、熔断、并发控制和错误码，但避免对非幂等请求盲目重试。\n+- 用契约测试和一组黄金轨迹验证每个适配器；切换模型时重新做质量回归，不能假定接口兼容等于行为兼容。\n\n常见追问：不同模型的工具调用语义不一致如何处理？流式响应中途失败怎样切换模型？""",
    "A33": """人机边界应由风险、可逆性、置信度和责任要求决定：低风险、可验证、可回滚的步骤可自动执行；高影响、不可逆或证据不足的动作必须人工确认。目标不是消灭人工，而是把人工放在最有信息增益的决策点。\n\n+- 先给工具和动作分级，定义自动、二次确认、双人审批与禁止四类策略。\n+- 模型只提出动作意图，策略引擎根据身份、数据范围、金额、环境和风险独立授权。\n+- 人工确认界面展示拟执行动作、关键参数、依据、影响范围和回滚方式，避免“同意/拒绝”黑箱。\n+- 记录人工修改与拒绝原因，回流到评测集和规则，但不能未经审计直接当作训练真值。\n\n常见追问：如何减少过多确认造成的操作疲劳？何时允许系统在低置信度下自动降级？""",
    "A34": """工具数量变多时，不应把全部工具描述一次性塞给模型。通常先按权限和场景过滤，再用规则、语义检索或小模型召回候选工具，最后让主模型在少量候选中选择，并由程序侧校验参数与权限。\n\n+- 工具注册表保存名称、能力、Schema、版本、权限、成本、延迟和健康状态；描述要区分适用与不适用边界。\n+- 路由分为硬过滤、候选召回和精排三层，关键工具可设置确定性规则，避免语义相近工具冲突。\n+- 用真实请求构建路由评测集，关注 Top-k 召回、最终选择准确率、误调用率和平均上下文成本。\n+- 新旧工具灰度共存，调用轨迹可回放；低置信度时澄清或转人工，而不是随机试调用。\n\n常见追问：100 个工具如何控制 Prompt 长度？两个工具描述高度相似时如何消歧？""",
    "A35": """Skill 的稳定性来自“版本化契约 + 可重复评测 + 受控执行”，而不是要求自然语言每次逐字一致。把输入输出、依赖、权限、终止条件和失败语义写成明确契约，将开放推理限制在必要步骤。\n\n+- Skill 使用语义版本，锁定模板、模型、工具和数据依赖；变更必须经过离线回归与小流量灰度。\n+- 测试分为确定性单元测试、录制工具响应的轨迹回放、端到端任务集和对抗样本。\n+- 多 Skill 路由单独评测，监控误路由、冲突和回退；上下文只加载候选 Skill 的详细说明。\n+- 结果采用 Schema、业务不变量和外部验证器检查；随机性通过多次运行统计成功率与方差。\n\n常见追问：自然语言 Skill 如何做单元测试？换模型后怎样判断兼容？""",
    "E7": """模型选型应以任务集上的质量门槛为前提，再比较时延、吞吐、成本、上下文、工具能力、部署与合规约束。不要先按参数规模决定；能满足质量目标的最小模型通常更适合高频链路，复杂或高风险样本再路由到强模型。\n\n+- 建立覆盖常见、长尾和对抗样本的评测集，测任务成功率而不只看通用榜单。\n+- 在线比较 P50/P95、首 Token 时延、输出速度、单位成功成本、限流和稳定性。\n+- 云 API 上线快、弹性好；自部署便于数据控制和深度优化，但承担容量、升级与运维成本。\n+- 可采用级联、缓存、动态路由和降级模型，并持续监控数据漂移与模型版本变化。\n\n常见追问：何时值得自部署？小模型到大模型的升级阈值如何确定？""",
    "L14": """幻觉无法靠一条 Prompt 消除，应从数据、检索、生成、工具、验证和产品交互多层治理。先按“无依据、依据冲突、推理错误、工具结果误用”分类，再针对首个错误环节处理。\n\n+- 对事实型问题优先提供可追溯证据，要求答案绑定引用，并验证引用是否真正支持结论。\n+- 使用结构化输出、约束解码、业务规则和确定性计算器约束可验证部分；高风险结论增加二次校验或人工审批。\n+- 通过置信度校准、缺失信息检测和拒答策略，让系统在证据不足时明确说不知道。\n+- 用真实坏例持续构建离线集，分别测事实性、引用忠实度、拒答准确率和业务危害，线上监控但不把用户反馈直接当真值。\n\n常见追问：RAG 后仍然幻觉怎么办？怎样评估“会拒答”而不是“过度拒答”？""",
    "L15": """LLM-as-a-Judge 适合评估开放式输出，但必须先证明它与人工标准一致。应给出清晰 rubric、隐藏参考答案或证据，采用盲评与位置随机化，并用人工标注集校准偏差。\n\n+- 用成对比较降低绝对打分漂移，同时交换答案顺序检测位置偏差。\n+- 对长度、文风、自我偏好和模型家族偏好做分层分析；必要时使用多个异构 Judge 投票。\n+- 定期计算与人工的一致率、相关系数和分项混淆矩阵，对低一致样本进入人工复核。\n+- Judge 只作为证据之一；安全、事实和结构约束优先使用确定性验证器。\n\n常见追问：Judge 与被评模型同源会有什么风险？怎样防止候选答案注入评审指令？""",
    "R10": """向量检索把文本映射到稠密向量并按距离找近邻，适合语义相似；关键词检索擅长精确实体、编号和稀有词。生产 RAG 通常采用混合召回，再用 RRF 或学习排序融合，并由 Reranker 精排。\n\n+- HNSW 查询快、召回高但内存占用大且构建成本高；IVF/PQ 更省空间，需在探针数、压缩率与召回率间权衡。\n+- 选择 Embedding 要用本领域查询—文档对评测，关注维度、语言、长度、许可证和推理成本。\n+- 索引必须保存文档版本、权限和元数据过滤；距离高不等于证据足够，最终还要做相关性与支持性判断。\n+- 模型升级采用双写、后台重建、影子流量和原子切换，避免新旧向量混用。\n\n常见追问：HNSW 与 IVF 如何选择？Embedding 升级如何不停机迁移？""",
    "R11": """复杂文档解析应保留“内容 + 结构 + 位置 + 来源”四类信息，不能只抽纯文本。先识别版面、标题层级、段落、表格、图片和页码，再按结构单元切分，并为每个块保存父子关系和原始坐标。\n\n+- PDF 先判断文本层、扫描件和混合版面；扫描件使用 OCR，并记录置信度和阅读顺序。\n+- 表格保留表头、行列关系与跨页信息，可同时生成结构化 JSON 和便于检索的文本表示。\n+- 检索时可先召回章节或表格，再扩展相邻块与父标题；回答引用回原页和区域便于核验。\n+- 用文档级测试集评估解析完整率、顺序正确率、表格还原率、召回与最终回答，低置信解析进入人工处理。\n\n常见追问：跨页表格如何处理？标题命中但答案在正文时如何扩展上下文？""",
}


BUCKETS = [
    Bucket("E1", "模型服务与推理工程", "KV Cache 是什么，为什么能加速自回归推理？", (r"KV.?Cache",)),
    Bucket("E2", "模型服务与推理工程", "PagedAttention 如何管理 KV Cache，它的收益和代价是什么？", (r"Paged.?Attention",)),
    Bucket("E4", "模型服务与推理工程", "vLLM 出现显存或内存泄漏时如何定位？", (r"vLLM.*(泄漏|OOM|显存)",)),
    Bucket("E5", "模型服务与推理工程", "vLLM 的队列与 Continuous Batching 如何工作？", (r"Continuous.?Batch|vLLM.*(队列|调度)",)),
    Bucket("E6", "模型服务与推理工程", "vLLM 的推理加速原理、收益与代价是什么？", (r"vLLM",)),
    Bucket("E3", "模型服务与推理工程", "大模型推理服务的资源瓶颈如何定位与治理？", (r"推理.*(成本|加速|延迟|吞吐|资源|显存|部署|算子)", r"部署.*(显存|资源|模型)", r"模型服务.*(性能|资源|接口)", r"(模型|大模型).*(调用失败|一直超时|调用超时|限流|输出太长|时长限制|运行时间.*稳定)")),
    Bucket("L15", "评测、质量与安全", "使用 LLM-as-a-Judge 时如何评估并降低评分偏差？", (r"(LLM|大模型).*(裁判|Judge|评分偏差)",), NEW_ANSWERS["L15"]),
    Bucket("L14", "评测、质量与安全", "大模型与 Agent 的幻觉应如何分层治理？", (r"幻觉|不确定.*拒答|事实.*错误",), NEW_ANSWERS["L14"]),
    Bucket("R8", "RAG 与知识工程", "GraphRAG 与传统 RAG 有什么区别，分别适合哪些场景？", (r"Graph.?RAG|知识图谱.*RAG|RAG.*知识图谱",)),
    Bucket("R11", "RAG 与知识工程", "PDF、Word、表格等复杂文档如何解析、切分与检索？", (r"(PDF|Word|复杂文档|表格).*(解析|切分|检索|处理)|文档解析",), NEW_ANSWERS["R11"]),
    Bucket("R4", "RAG 与知识工程", "文档如何动态切分并避免破坏上下文？", (r"(分块|切分|chunk|Chunk).*(评估|文档|上下文)|动态切分|(RAG|工程).*(分块|切片)",)),
    Bucket("R6", "RAG 与知识工程", "知识库如何增量更新、版本化并保持新鲜？", (r"知识库.*(更新|版本|新旧|删除|权限|新鲜|保鲜|老旧)|RAG.*(更新|删除|权限)|Embedding.*(升级|迁移)|增量.*知识",)),
    Bucket("R7", "RAG 与知识工程", "知识存在但未召回时，如何分层排查 RAG？", (r"(未召回|检索准确率低|召回不相关|回答错|正确证据已经召回).*(排查|诊断|怎么办|没有使用|优化)|模型问题还是知识库|相似度很高.*错误|知识库里没有",)),
    Bucket("R1", "RAG 与知识工程", "RAG 的召回、重排与答案效果如何评估？", (r"RAG.*(评估|召回率|准确率|指标|Benchmark)|评估.*(检索|召回|RAG)|检索结果.*支持.*结论|保证.*RAG.*准确|上下文召回率.*答案正确率",)),
    Bucket("R5", "RAG 与知识工程", "RAG 召回不相关时，如何优化 Query Rewrite、混合召回与重排？", (r"(混合检索|多路召回|Query.?Rewrite|HyDE|Rerank|重排|召回方式|召回阶段.*排序|召回.*怎么排序)|RAG.*优化|检索结果.*当前对话",)),
    Bucket("R10", "RAG 与知识工程", "向量检索、关键词检索与索引结构如何选型？", (r"向量(库|检索|存储)|关键词检索|HNSW|IVF|BGE-M3|数据库检索和向量",), NEW_ANSWERS["R10"]),
    Bucket("R9", "RAG 与知识工程", "Prompt、RAG、长上下文与微调分别适合什么问题？", (r"(Prompt|提示词).*(RAG|微调)|RAG.*(微调|长上下文)|后训练.*RAG",)),
    Bucket("R3", "RAG 与知识工程", "企业 RAG 知识库应如何设计与实现？", (r"(知识库|RAG).*(怎么做|怎么构建|如何设计|架构|建设|搭建|索引|存多大)|多文档关联检索|游戏攻略问答.*RAG|大仓代码.*知识库",)),
    Bucket("R2", "RAG 与知识工程", "RAG 的作用、原理与端到端流程是什么？", (r"(了解|使用|项目中|有没有用过).*RAG|RAG.*(原理|流程|作用|是什么)|RAG了解|了解RAG",)),
    Bucket("L1", "提示词、上下文与记忆", "Agent 长链路中上下文超限时如何压缩、分层与按需回填？", (r"上下文.*(超限|爆|压缩|裁切|窗口|长度限制|中间信息|最小可用)|Token.*(上下文|节省)",)),
    Bucket("L2", "提示词、上下文与记忆", "Agent 的短期、工作与长期记忆如何分层，并实现写入、检索、更新与遗忘？", (r"(长短期记忆|短期记忆|长期记忆|记忆系统|会话记忆|Memory).*(设计|分类|分层|存储|保存|实现|污染|错误事实|时延|JSONL|覆盖)|记忆.*(写入|检索|更新|遗忘|分工|避免)",)),
    Bucket("L8", "提示词、上下文与记忆", "上下文管理与记忆的边界是什么？", (r"上下文.*(管理|记忆|数据结构|怎么设置|怎么做)|动态上下文|哪些信息.*(大模型|程序侧)",)),
    Bucket("L9", "提示词、上下文与记忆", "需求模糊时，Agent 如何通过多轮对话澄清？", (r"(需求|用户).*(模糊|说不清|澄清)|多轮对话.*(修改|澄清|上下文)",)),
    Bucket("L12", "评测、质量与安全", "Agent Badcase 如何回流到评测与优化闭环？", (r"Bad.?Case|坏例|失败轨迹.*(SFT|回流)|归因.*评测",)),
    Bucket("L11", "评测、质量与安全", "Agent 评测集、覆盖范围与 Ground Truth 如何设计？", (r"(评测集|评测数据集|Benchmark|Ground.?Truth|基准).*(设计|构建|覆盖|标注|获取|多少|怎么做)|缺陷修复.?Benchmark|评测维度",)),
    Bucket("L5", "评测、质量与安全", "如何用对照实验和数据证明模型或 Agent 的提升？", (r"(对照实验|A/B|baseline|量化效果|效果提升|数据证明)|准确率只有.*优化",)),
    Bucket("L4", "提示词、上下文与记忆", "Prompt 如何做版本管理、离线评测、灰度发布与回归测试？", (r"Prompt.*(版本|灰度|回归|离线评测|管理|测试)",)),
    Bucket("L7", "提示词、上下文与记忆", "Prompt 的生成与优化目标如何定义？", (r"Prompt.*(优化|生成|目标)|提示词.*(优化|过于简单|判断)",)),
    Bucket("L6", "提示词、上下文与记忆", "Agent 的系统提示词如何设计？", (r"(System.?Prompt|系统提示词|业务项目的Prompt|提示词如何设计|Prompt.*职责|Prompt.*怎么写|Prompt规范)",)),
    Bucket("A35", "工具调用、MCP 与 Skill", "自然语言 Skill 如何版本化、测试并保证多次执行稳定？", (r"Skill.*(稳定|测试|调试|版本|换模型|上下文膨胀|路由冲突|持续提升路由|动态发现|路由与上下文)|多个Skill.*路由",), NEW_ANSWERS["A35"]),
    Bucket("A15", "工具调用、MCP 与 Skill", "如何评测一个 Skill 的真实效果？", (r"Skill.*(评测|量化效果|成功率|判断效果)",)),
    Bucket("A17", "工具调用、MCP 与 Skill", "Skill 的完整执行链路与异常兜底如何设计？", (r"Skill.*(执行链路|异常|兜底|开发过程|检查项|封装|特性|实现|编排)|故障诊断Skill|文章质量检测Skill|是否了解Skill",)),
    Bucket("A9", "工具调用、MCP 与 Skill", "MCP、Skill、Tool、Workflow 与 Agent 有什么区别？", (r"(Skill|Tool|Workflow).*(MCP|Agent|区别|划分|代替|本质|优势)|MCP.*(Skill|Function.?Calling|Tool|Agent|选型).*(区别|关系)?|什么是Function.?Calling|Agent相比Skill",)),
    Bucket("A14", "工具调用、MCP 与 Skill", "MCP 的核心抽象、交互协议与传输方式是什么？", (r"MCP.*(协议|传输|抽象|交互|stdio|SSE|连接方式)",)),
    Bucket("A11", "工具调用、MCP 与 Skill", "如何设计并实现 MCP Server 与工具调用链路？", (r"MCP.*(Server|服务端|实现|设计|开发|接入)|实现.*MCP|框架如何接入MCP",)),
    Bucket("A7", "工具调用、MCP 与 Skill", "MCP 是什么，它解决什么问题？", (r"(了解|理解|什么是|怎么看|是否了解).*MCP|MCP.*(了解|是什么|解决什么|权限问题|能不能|httpAPI|Agent|Session)",)),
    Bucket("A34", "工具调用、MCP 与 Skill", "Agent 工具很多时，如何做候选召回、路由与冲突消解？", (r"(100个工具|工具过多|多个工具|工具数量|工具路由|正确的Skill|动态维度拆分|tool.?list太多|工具注册中心|动态工具注册)",), NEW_ANSWERS["A34"]),
    Bucket("A18", "工具调用、MCP 与 Skill", "如何保证 Agent 的结构化输出满足下游 Schema？", (r"(JSON|结构化输出|接口契约|Schema|返回格式|流式输出).*(保证|满足|校验|定义|约束|失败|不符合)|输出.*Schema",)),
    Bucket("A13", "工具调用、MCP 与 Skill", "Agent 工具调用失败、超时或参数非法时如何校验、重试与降级？", (r"工具调用.*(可靠|失败|错误|超时|非法|重试|参数|容忍|重复)|外部服务.*超时|地图API超时|工具.*100秒|模型输出失败|工具始终超时|长耗时任务|调用工具返回",)),
    Bucket("A21", "工具调用、MCP 与 Skill", "如何防止 Agent 越权调用工具或泄露敏感数据？", (r"(越权|权限管理|写操作权限|敏感数据|泄露|隐私|提示注入).*(Agent|工具|沙箱)|Agent.*(越权|泄露|权限)|Skill.*(权限|读操作|写操作)|MCP.*权限校验",)),
    Bucket("A6", "工具调用、MCP 与 Skill", "Agent 生成代码或执行命令时，安全沙箱如何设计？", (r"沙箱|宿主机.*文件系统|代码.*安全执行",)),
    Bucket("A32", "Agent 系统与工作流", "通用 Agent Runtime 如何兼容不同模型厂商与能力？", (r"(不同模型|多个模型|模型厂商).*(接口|格式|兼容|抽象)|通用AgentRuntime",), NEW_ANSWERS["A32"]),
    Bucket("A30", "Agent 系统与工作流", "长任务 Agent 如何持久化状态、恢复中断并保证副作用幂等？", (r"(重启|中断|异常退出|服务挂了).*(恢复|任务)|状态持久化|幂等|重复下单|未完成任务|检查点|多机房.*状态同步|分布式事务",), NEW_ANSWERS["A30"]),
    Bucket("A29", "Agent 系统与工作流", "Agent Loop 如何设置停止条件并避免死循环？", (r"(Agent.?Loop|Agent).*(死循环|停止条件|终止|最大.*步|直接结束)|避免Agent无限循环|弹性设置.*最大步",), NEW_ANSWERS["A29"]),
    Bucket("A31", "Agent 系统与工作流", "Agent 如何建设可观测、可审计的执行轨迹与告警体系？", (r"Agent.*(监控|告警|可观测|审计|轨迹|日志)|trajectory.*字段|执行轨迹",), NEW_ANSWERS["A31"]),
    Bucket("A33", "Agent 系统与工作流", "Agent 自动化中哪些步骤应保留人工确认？", (r"(哪些|什么).*人.*(参与|做)|人工确认|人机|真正意义.*自动化",), NEW_ANSWERS["A33"]),
    Bucket("A25", "Agent 系统与工作流", "长时间 Agent 任务如何支持取消、中断与资源回收？", (r"(用户取消|安全终止|长任务.*取消|中途取消).*(Agent|订单|任务)?|取消信号",)),
    Bucket("A10", "Agent 系统与工作流", "多 Agent 并发时如何做资源隔离、状态一致性与冲突处理？", (r"多.*Agent.*(并发|冲突|状态一致|资源隔离|同时修改)|多个请求.*Agent状态|互相推诿",)),
    Bucket("A5", "Agent 系统与工作流", "多 Agent 如何通信并可靠传递上下文与中间结果？", (r"多.*Agent.*(通信|传递|上下文|输入|结果|消息)|多个Agent之间",)),
    Bucket("A12", "Agent 系统与工作流", "什么场景适合多 Agent，它相比单 Agent 的收益与代价是什么？", (r"(多Agent|多智能体|子Agent).*(什么时候|适合|拆分|职责|为什么|单Agent|出错)|单Agent.*多Agent|leaderagent",)),
    Bucket("A24", "Agent 系统与工作流", "如何在 ReAct、Plan-and-Execute 与确定性流程间选择？", (r"(ReAct|React|Plan.?and.?Execute|plan模式|Agent.?Loop.*(步骤|模块|完整|Action)|最简单的Agent.?Loop|完整的Agent.?Loop|规划能力)",)),
    Bucket("A16", "Agent 系统与工作流", "Agent 动态工作流平台与状态机如何实现？", (r"(工作流平台|动态工作流|状态机|SOP|原子能力编排|组合多个原子能力|多模式引擎)",)),
    Bucket("A26", "Agent 系统与工作流", "Agent 灰度发布、配置热更新与故障回退如何设计？", (r"(灰度|回滚|热更新|部署流水线|部署不会出现问题)|用户无感知",)),
    Bucket("A20", "Agent 系统与工作流", "高并发 Agent 服务如何设计限流、背压、降级与熔断？", (r"Agent.*(QPS|高并发|响应速度|延迟|耗时|成本|限流|降级|熔断)|一次请求.*多个模型.*成本|(大模型|Token).*(成本|使用.*优化)",)),
    Bucket("A27", "Agent 系统与工作流", "Agent 如何实现多租户、任务与上下文隔离？", (r"(多租户|不同任务).*(隔离|数据)|租户数据|上下文隔离",)),
    Bucket("A19", "Agent 系统与工作流", "如何理解 DeepSeek Harness 的设计与实现？", (r"DeepSeek.?Harness",)),
    Bucket("A28", "Agent 系统与工作流", "Agent 框架、LangChain/LangGraph 与自研方案如何取舍？", (r"(LangChain|LangGraph|Agent框架|开源框架|AutoGen|CrewAI).*(区别|差异|优势|选择|自研|取舍|了解|边界|模块)|了解哪些Agent开发框架|框架和RPC|不使用框架.*原生实现",)),
    Bucket("A3", "Agent 系统与工作流", "什么是 Agent Harness，它与框架和 Orchestrator 有何区别？", (r"Harness",)),
    Bucket("A2", "Agent 系统与工作流", "如何根据确定性、链路长度与风险选择 Agent 或 Workflow？", (r"(Workflow|工作流).*(Agent|自主Agent|选择|区别)|Agent.*(Workflow|确定性流程|传统规则机器人)",)),
    Bucket("A4", "Agent 系统与工作流", "如何定义 Agent，它与普通 LLM 调用和传统 API 有何区别？", (r"(什么是|理解|定义|解释).*Agent|Agent.*(普通大模型|传统API|概念|组成)|说下什么是agent",)),
    Bucket("A8", "评测、质量与安全", "如何量化评估 Agent 的任务效果与服务质量？", (r"Agent.*(效果|评测|评估|评价指标|服务质量|正确可靠|整体效果|质量|节点验证)|衡量.*Agent|验证Agent.*可靠|保证Agent.*可靠",)),
    Bucket("L3", "评测、质量与安全", "Agent / LLM 应用的完整评测体系如何设计？", (r"(Agent|模型).*(评测体系|如何评测|评测指标)|评测模型效果",)),
    Bucket("A22", "AI 应用工程", "如何评估并治理 AI Coding 的质量、效率与上下文风险？", (r"AI.?Coding|Coding.?Agent|Claude.?Code|codex|AI.*(辅助开发|工具|提效|编码)|代码.*(大模型|AI).*(验证|缺陷|质量)",)),
    Bucket("A1", "AI 应用工程", "如何有结构地介绍 Agent 项目的业务目标、技术链路、难点与效果？", (r"(介绍|详细讲|讲一下).*(Agent|智能体).*(项目|架构|链路)|Agent项目.*(做了什么|难点|业务|介绍|泛问)|从零到一.*Agent|在AIAgent这块具体做了",)),
    Bucket("A23", "AI 应用工程", "Agent 应用架构如何分层，层间职责与接口如何设计？", (r"(设计|搭建|实现|开发).*(Agent|智能体).*(系统|平台|架构|应用)|Agent.*(核心模块|整体架构|系统架构|平台开发|接口|基础设施|哪一层|约束主要)|前端、后端和模型服务.*接口|(如何设计|设计一个|让你设计|从零).{0,20}(Agent|智能体)|多智能体协作.*任务分配|企业知识问答Agent.*统一接入|设计Agent框架.*分层",)),
    Bucket("E7", "模型服务与推理工程", "大模型如何选型，并在云 API 与自部署之间取舍？", (r"(模型|大模型).*(选型|选择|小模型|大模型|API|自部署|本地|云端|优缺点|切换)|为什么没有直接使用大模型API|使用过哪些大模型|(选择|使用).*什么模型|用了哪些模型|自研模型还是",), NEW_ANSWERS["E7"]),
]


NOISE = re.compile(
    r"^(反问|候选人[:：]|入职后|所以你们|现在您不是|然后刚刚|那k8s|但我前面|也是未来|道选择题|AI实战题|什么agent\(|干什么的[:：]|华为有用什么ai经验|百度也有|”——|给后面的人建议|我心想|这一块没get|yaml好了|整过程中|OK我先|简要介绍项目经历|所以问题就是说|目前我知道了|当时有没有了解开源|八股|gin框架|近期是在百度|具体的任务是啥|我简单理解|可以简单介绍下go|这个问题乍一听|第二个让候选人|到了RL方向|给准备|同一句在|\d+\s*AI应用研发|部门使用agent|【答了)|"
    r"(GRPO|PPO-like|重要性采样|训练.*OOM|多模态推理数据|早融合|中融合|晚融合|7Bvl|模型参数.*moe|大模型一般一个月才能训练|课程.*神经网络|训练里关键节点|实时推理或训练任务|OCR任务本身|视觉编码器|位置编码|多模态.*(预训练|监督微调|Router)|SFT.*(过拟合|数据配比|数据集)|RLHF|奖励模型|DPO训练|模型剪枝|Roofline|opd的原理)",
    re.I,
)


def load_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").replace("​", "")


def locate_evidence(evidence: str, body: str) -> bool:
    e = norm(evidence)
    b = norm(body)
    if not e:
        return False
    if e in b:
        return True
    # Extraction sometimes normalizes ASCII/Chinese punctuation.
    strip = lambda x: re.sub(r"[，。！？、；：,.!?;:'\"“”‘’（）()\[\]【】—\-]", "", x)
    e2, b2 = strip(e), strip(b)
    return len(e2) >= 8 and e2 in b2


def classify(question: str) -> Bucket | None:
    text = norm(question)
    if not text or NOISE.search(text):
        return None
    # Reject generic awareness/meta questions that cannot stand alone as revision topics.
    if re.search(r"^(有了解过大模型吗|对大模型的看法|对大模型的应用场景了解吗|未来agent的发展趋势|最近有关注Agent|从什么时候开始关注|对agent得学习了解多少|自己有没有做过agent项目|实习的任务里面有agent参与嘛)", text, re.I):
        return None
    for bucket in BUCKETS:
        if any(re.search(pattern, text, re.I) for pattern in bucket.patterns):
            return bucket
    return None


def parse_history(md: str):
    result = {}
    pattern = re.compile(r"(?ms)^### ([A-Z]\d+)\. (.+?)\r?\n(.*?)(?=^### |^## |\Z)")
    for m in pattern.finditer(md):
        code, question, body = m.group(1), m.group(2).strip(), m.group(3).strip()
        inline_m = re.search(r"出现次数：(\d+)｜涉及企业：([^\r\n]+)", body)
        meta_m = re.search(r"出现\s*(\d+)\s*次；涉及企业：(.+?)[。\r\n]", body)
        count_m = inline_m or meta_m or re.search(r"(?m)^- 出现次数：(\d+)\s*$", body)
        company_m = inline_m or meta_m or re.search(r"(?m)^- 涉及企业：([^\r\n]+)", body)
        source_m = re.search(r"(?m)^(?:\*\*)?-?\s*来源：(?:\*\*)?(.+)$", body)
        answer_m = re.search(r"(?ms)^(?:\*\*)?参考答案：(?:\*\*)?(.+?)(?=\n\s*(?:\*\*)?来源：|\Z)", body)
        if not (count_m and company_m and source_m and answer_m):
            raise ValueError(f"Cannot parse historical item {code}")
        result[code] = {
            "code": code,
            "question": question,
            "occurrences": int(count_m.group(1)),
            "companies": [x for x in company_m.group(2 if (inline_m or meta_m) else 1).split("、") if x],
            "sources": source_m.group(1).strip(),
            "answer": answer_m.group(1).strip(),
        }
    if len(result) != 56:
        raise ValueError(f"Expected 56 historical questions, got {len(result)}")
    return result


def main():
    posts = load_jsonl(CLEAN)
    extracted = load_jsonl(EXTRACTED)
    post_by_id = {p["contentId"]: p for p in posts}
    evidence_mismatches = []
    accepted = []
    rejected = Counter()
    grouped = defaultdict(list)
    for row in extracted:
        post = post_by_id.get(row.get("sourceId"))
        if post is None:
            rejected["sourceId_not_found"] += 1
            continue
        if not locate_evidence(row.get("evidence", ""), post.get("body", "")):
            evidence_mismatches.append({"sourceId": row.get("sourceId"), "question": row.get("question"), "evidence": row.get("evidence")})
            rejected["evidence_not_located"] += 1
            continue
        bucket = classify(row.get("question", ""))
        if bucket is None:
            if NOISE.search(norm(row.get("question", ""))):
                rejected["out_of_scope_or_training"] += 1
            else:
                rejected["too_vague_meta_or_unmapped"] += 1
            continue
        accepted.append(row)
        grouped[bucket.code].append(row)

    bucket_by_code = {b.code: b for b in BUCKETS}
    topics = defaultdict(list)
    current_details = {}
    for code, rows in grouped.items():
        bucket = bucket_by_code[code]
        source_ids = sorted({r["sourceId"] for r in rows})
        variants = []
        for r in rows:
            value = (r.get("originalQuestion") or r.get("question") or "").strip()
            if value and value not in variants and value != bucket.canonical:
                variants.append(value)
        companies = sorted({post_by_id[s]["company"] for s in source_ids})
        follow_ups = variants[:6]
        obj = {
            "canonicalQuestion": bucket.canonical,
            "variants": variants,
            "followUps": follow_ups,
            "companies": companies,
            "occurrences": len(source_ids),
            "sourceIds": source_ids,
        }
        topics[bucket.topic].append(obj)
        current_details[code] = obj

    topic_order = ["Agent 系统与工作流", "工具调用、MCP 与 Skill", "AI 应用工程", "提示词、上下文与记忆", "RAG 与知识工程", "模型服务与推理工程", "评测、质量与安全"]
    organized = {
        "topics": [
            {"topic": topic, "questions": sorted(topics[topic], key=lambda x: (-x["occurrences"], x["canonicalQuestion"]))}
            for topic in topic_order if topics.get(topic)
        ]
    }

    # Per the workspace rule, refresh the exact on-disk target immediately before replacement.
    _ = ORGANIZED.read_text(encoding="utf-8")
    ORGANIZED.write_text(json.dumps(organized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    history_text = subprocess.run(
        ["git", "show", "HEAD:nowcoder_agent_mvp/output/agent_ai_interview_question_bank.md"],
        cwd=ROOT.parent,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    history = parse_history(history_text)
    new_codes = [c for c in current_details if c not in history]
    final_items = dict(history)
    for code in new_codes:
        b = bucket_by_code[code]
        final_items[code] = {
            "code": code,
            "question": b.canonical,
            "occurrences": 0,
            "companies": [],
            "sources": "",
            "answer": b.answer or NEW_ANSWERS[code],
        }

    for code, cur in current_details.items():
        item = final_items[code]
        item["occurrences"] += cur["occurrences"]
        item["companies"] = sorted(set(item["companies"]) | set(cur["companies"]))
        reps = []
        for sid in cur["sourceIds"]:
            p = post_by_id[sid]
            date = (p.get("publishedAt") or "未知日期")[:10]
            link = f'[{p["company"]} / {date}]({p["url"]})'
            if link not in reps:
                reps.append(link)
            if len(reps) >= 3:
                break
        current_sources = "、".join(reps)
        item["sources"] = "、".join(x for x in [item["sources"], current_sources] if x)

    sections = {
        "A": "Agent 工程",
        "E": "LLM 工程",
        "L": "LLM 应用",
        "R": "RAG 与知识工程",
    }
    ordered_codes = []
    for prefix in ("A", "E", "L", "R"):
        ordered_codes.extend(sorted((c for c in final_items if c.startswith(prefix)), key=lambda c: int(c[1:])))
    current_occurrences = sum(x["occurrences"] for x in current_details.values())
    current_sources = len({sid for x in current_details.values() for sid in x["sourceIds"]})
    cumulative_occurrences = 289 + current_occurrences
    cumulative_sources = 117 + current_sources
    lines = [
        "# 2026 年 9 月 Agent / AI 应用研发面试题库",
        "",
        "## 采集范围、审计统计与口径",
        "",
        "这是 **2026-09-01 至 2026-09-19（Asia/Shanghai）** 的月内累计题库，覆盖 **50 家公司 × 2 个岗位二级类别**（软件开发 / 人工智能/算法、软件开发 / 后端开发）。采集始终使用牛客企业选择器和“最新”排序，没有退化为全站关键词搜索。当前范围已完整覆盖；实际执行由历史 90 个采集单元与本轮 100 个增量单元组成。",
        "",
        "- 历史窗口（原 45 家公司，09-01 至 09-11）：322 条候选，正文请求成功 321 条，其中 313 条正文非空；确定性清洗保留 299 条；191 篇初筛相关帖子抽取 1,156 条问题候选。Sol 审核后形成 56 道核心题、289 次独立帖子出现记录、117 篇唯一来源帖。",
        "- 本轮增量（原 45 家公司 09-12 至 09-19；新增比亚迪、中国移动、中国联通、中国电信、深信服 09-01 至 09-19）：181 条候选，180 条正文非空、1 条正文为空；确定性清洗保留 171 条；118 篇初筛相关帖子抽取 829 条问题候选。Terra 终审前第二版声称保留 599 条、归并为 360 道核心题；本次 Sol 逐条回查来源正文后，保留 **%d 条可定位且在范围内的问题记录**，归并为 **%d 道本轮核心题、%d 次独立帖子出现记录，覆盖 %d 篇唯一来源帖**。" % (len(accepted), len(current_details), current_occurrences, current_sources),
        "- 累计：503 条候选、470 条 clean 正文、1,985 条抽取问题候选；累计清洗拒绝 33 条。最终形成 **%d 道核心题、%d 次独立帖子出现记录，覆盖 %d 篇唯一来源帖**。" % (len(final_items), cumulative_occurrences, cumulative_sources),
        "",
        "“出现次数”按独立来源帖子计数：同一帖子对同一道核心题的重复提及只算一次。历史与本轮窗口不重叠，因此映射到同一核心题时可直接相加；公司取并集。来源链接仅作为面经证据，参考答案由模型基于通用工程实践整理，不代表原帖作者答案或企业标准答案。",
        "",
    ]
    last_prefix = None
    for code in ordered_codes:
        prefix = code[0]
        if prefix != last_prefix:
            lines.extend([f"## {sections[prefix]}", ""])
            last_prefix = prefix
        item = final_items[code]
        clean_answer = item["answer"].replace("\n+- ", "\n- ")
        lines.extend([
            f'### {code}. {item["question"]}',
            f'出现次数：{item["occurrences"]}｜涉及企业：{"、".join(item["companies"])}',
            f'来源：{item["sources"]}',
            "",
            f'参考答案：{clean_answer}',
            "",
        ])
    lines.extend([
        "## 使用建议",
        "",
        "按“直接结论—机制—权衡—工程落地—追问”复述答案，并结合自己的项目补充真实数据、职责边界与失败案例。不要背诵来源帖措辞；面试时应明确哪些是亲自实现、哪些是调研或团队能力。",
        "",
    ])
    # Refresh the output once more immediately before the edit, then derive from that current baseline.
    _ = OUTPUT.read_text(encoding="utf-8")
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")

    report_obj = {
        "scope": {"from": "2026-09-01", "through": "2026-09-19", "companyCount": 50, "categoryCount": 2},
        "model": {"name": "gpt-5.6-sol", "reasoningEffort": "high"},
        "incrementalFinalReview": {
            "extractedInput": len(extracted),
            "preReviewRetainedClaim": 599,
            "preReviewCoreQuestions": 360,
            "postReviewRetainedRecords": len(accepted),
            "postReviewCoreQuestions": len(current_details),
            "postReviewOccurrences": current_occurrences,
            "postReviewUniqueSourcePosts": current_sources,
            "rejectedOrUnmapped": len(extracted) - len(accepted),
            "reasons": dict(rejected),
            "evidenceMismatches": evidence_mismatches,
            "repairs": [
                "合并“RAG了解吗/了解RAG吗/是否使用过RAG”等同义问法",
                "将上下文压缩、记忆分层、记忆污染与上下文边界按独立能力主题重整",
                "把连续且依赖主问题的细粒度问法收为 followUps，并保留 variants",
                "剔除候选人长回答、面试元评论、反问、纯训练/强化学习与无法独立复习的问题",
                "拆开只共享宽泛词的模型部署、Agent Runtime、工具路由、状态恢复和评测问题",
            ],
        },
        "cumulative": {
            "candidatePosts": 503,
            "cleanPosts": 470,
            "cleaningRejected": 33,
            "extractedQuestionCandidates": 1985,
            "coreQuestions": len(final_items),
            "occurrences": cumulative_occurrences,
            "uniqueSourcePosts": cumulative_sources,
            "topicCount": len(organized["topics"]),
        },
        "validation": {
            "allCurrentSourceIdsResolvable": all(sid in post_by_id for x in current_details.values() for sid in x["sourceIds"]),
            "allOccurrencesEqualUniqueSourceIds": all(x["occurrences"] == len(set(x["sourceIds"])) for x in current_details.values()),
            "allEvidenceLocatedInSourceBody": len(evidence_mismatches) == 0,
            "allCurrentLinksMatchCleanPosts": all(post_by_id[sid]["url"] in {p["url"] for p in posts} for x in current_details.values() for sid in x["sourceIds"]),
            "organizedRequiredFieldsPresent": all(set(x) >= {"canonicalQuestion", "variants", "followUps", "companies", "occurrences", "sourceIds"} for t in organized["topics"] for x in t["questions"]),
            "markdownHasNoEmptyAnswers": all(bool(x["answer"].strip()) for x in final_items.values()),
            "markdownHasUniqueQuestionCodes": len(ordered_codes) == len(set(ordered_codes)),
            "passed": False,
        },
        "unresolvedBoundaries": [
            "少量帖子是作者汇总的多场面经；sourceId 仍按发布帖计数，无法拆成独立原始面试帖。",
            "宽泛的场景设计题只有在正文明确指向 Agent/LLM 应用时保留，并归入架构主题；无法独立回答的公司业务反问已剔除。",
        ],
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    report_obj["validation"]["passed"] = all(v for k, v in report_obj["validation"].items() if k != "passed")
    if REPORT.exists():
        _ = REPORT.read_text(encoding="utf-8")
    REPORT.write_text(json.dumps(report_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "acceptedRecords": len(accepted),
        "currentCore": len(current_details),
        "currentOccurrences": current_occurrences,
        "currentSources": current_sources,
        "newCore": len(new_codes),
        "cumulativeCore": len(final_items),
        "cumulativeOccurrences": cumulative_occurrences,
        "cumulativeSources": cumulative_sources,
        "evidenceMismatches": len(evidence_mismatches),
        "rejected": dict(rejected),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
