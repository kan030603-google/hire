"""Second-pass evidence audit and conservative semantic consolidation."""
import json, re, unicodedata
from collections import defaultdict, Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; RUN=ROOT/'nowcoder_agent_mvp'/'data'/'september-2026'
SRC=RUN/'extracted-questions.jsonl'; POSTS=RUN/'clean-posts.jsonl'; OUT=RUN/'organized-questions.json'; REPORT=RUN/'semantic-merge-report.json'
def norm(s):
 s=unicodedata.normalize('NFKC',s or ''); s=re.sub(r'^\s*(?:[（(]?\d+(?:\.\d+)*[、.．)）]?|[-•])\s*','',s); return re.sub(r'\s+','',s).strip(' \t\n\r。；;')
def clean(s):
 s=norm(s); s=re.sub(r'^(?:面试官(?:上来|会)?(?:直接)?问[：:]|追问[：:]|他说[：:])','',s); s=re.sub(r'^(?:我愣了一下.*?追问[：:]|主要.*?问题[：:])','',s); s=re.sub(r'[（(](?:我|答了|完全不会|实习|占比最大).*?[)）]','',s); return s.strip('“”\" ')
RULES=[
 (r'harness','Agent Harness 的职责与设计'),(r'(agentloop.*(?:终止|停止)|停止条件|死循环|无限循环|直接结束)','Agent Loop 的终止与防循环'),(r'(agentloop|agent循环|autonomousloop|loop.*(?:模块|步骤|action))','Agent Loop 的组成与执行'),
 (r'(短期记忆|长期记忆|长短期记忆|memory.*(?:设计|分类|存储)|记忆.*(?:存储|污染))','Agent 记忆设计'),(r'(上下文.*(?:压缩|超限|爆炸|耗尽|窗口|裁剪)|历史上下文|滑动窗口|摘要)','上下文管理与压缩'),(r'(多agent|多智能体|子agent).*(?:分工|协作|拆分|架构|通信|消息|上下文)','多 Agent 协作设计'),
 (r'(工具.*(?:超时|失败|重试|错误)|tool.*(?:超时|失败|重试)|functioncalling.*(?:失败|超时))','工具调用失败与恢复'),(r'(权限|越权|sandbox|沙箱|危险参数|写操作)','工具调用安全与权限'),(r'(工具.*(?:路由|检索|注册|发现)|tool.*(?:list|路由)|工具过多|动态工具)','工具发现、注册与路由'),(r'(functioncalling|函数调用|工具调用协议|toolcalling)','Function Calling 与工具调用协议'),
 (r'(mcp.*(?:skill|http|api)|skill.*mcp|mcp.*区别)','MCP、Skill 与 API 的边界'),(r'mcp','MCP 接入与服务设计'),
 (r'(rag.*(?:是什么|了解|流程)|完整.*rag|知识库.*(?:构建|设计)|rag.*架构)','RAG 与知识库总体设计'),(r'(chunk|切片|分块)','RAG 切片策略'),(r'(rerank|重排|bm25.*向量|向量.*bm25|召回.*排序|排序.*召回)','RAG 召回与重排'),(r'(召回率|提升.*召回|召回.*(?:优化|准确|质量))','RAG 召回优化'),(r'(知识库.*(?:更新|保鲜)|索引.*(?:增量|更新)|文档.*(?:更新|删除|权限))','知识库更新与索引维护'),(r'(rag.*(?:诊断|排查)|召回.*生成|切片.*召回.*重排)','RAG 问题诊断'),
 (r'(prompt|提示词|systemprompt|系统提示词)','Prompt 与上下文设计'),(r'(结构化输出|json.*(?:合法|解析|约束)|流式.*json)','结构化输出与解析可靠性'),(r'(幻觉|拒答|事实.*错误)','幻觉治理与安全回答'),(r'(评测集|benchmark|badcase|效果.*评估|评测体系|如何评测)','Agent 与 LLM 应用评测'),(r'(监控|观测|trace|告警)','Agent 可观测性'),
 (r'(token.*(?:成本|消耗|预算|优化)|推理.*(?:成本|时延|延迟|优化)|kv.?cache|qps|限流)','模型服务性能、成本与限流'),(r'(模型.*(?:超时|调用失败|降级)|服务.*(?:挂|不可用)|模型.*重试)','模型服务可靠性'),(r'(模型.*(?:选型|选择)|用小模型.*大模型|使用.*(?:哪些|什么)模型)','模型选型'),
 (r'(langchain|langgraph|autogen|crewai|dify|coze|框架.*自研|原生.*agent)','Agent 应用框架选型'),(r'(agent.*(?:架构|平台|核心模块)|从零.*agent)','Agent 系统架构'),(r'(workflow|工作流|planner|规划)','Agent 工作流与规划'),(r'(ai.?coding|辅助开发)','AI Coding 的工程实践'),(r'(多模态.*(?:文档解析|内容审核|内容理解|agent)|图文.*(?:审核|理解)|视频.*agent)','多模态 AI 应用设计')]
APP=re.compile(r'agent|智能体|rag|知识库|检索|向量|embedding|rerank|重排|prompt|提示词|大模型|llm|模型服务|mcp|function.?calling|函数调用|工具调用|tool|sandbox|沙箱|langchain|langgraph|dify|coze|harness|skill|ai.?coding|上下文|幻觉|token|推理',re.I)
PURE=re.compile(r'(?:^|[，、：: ])(?:c\+\+|go|jvm|redis|mysql|threadlocal|goroutine|spark|clickhouse|kubectl|k8s|roofline|cuda|a100|gpu|pytorch|sft|rlhf|grpo|ppo|dpo|预训练|后训练|微调|视觉编码器|位置编码|算子|moe)(?:$|[，、：:？? ])',re.I)
def malformed(q):
 q=norm(q)
 if len(q)<6 or q.startswith(('”','——','【','（','(我们','比如说','从数据、')) or re.search(r'(正确思路是|给后面的人建议|我心想|我的表现你认为|面试官介绍当前岗位)',q): return True
 return q.lower() in {'mcp与agent','agent项目','agent框架','agent架构','agent系统评估','skill路由与上下文','skill安全与测试','ai coding','知识库搭建','大模型基础','部门使用agent的情况','生图、视频模型与降级策略','工程落地与ai开发工具','大模型通用题','大模型推理算子','agent工作流'}
def decide(r,bodies):
 q=clean(r.get('question')); ev=r.get('evidence') or ''; body=bodies.get(str(r.get('sourceId')),'')
 if not body or ev not in body:return 'missing-or-unlocatable-evidence'
 if malformed(q):return 'malformed-fragment-or-heading'
 if len(q)>80:return 'candidate-answer-or-unbounded-transcript-fragment'
 if PURE.search(q) and not APP.search(q):return 'pure-backend-or-model-training-without-ai-application-link'
 if not APP.search(q):return 'not-directly-agent-or-ai-application'
 if re.search(r'(roofline|grpo|ppo|dpo|rlhf|sft|预训练|后训练|视觉编码器|位置编码|大模型推理算子)',q,re.I) and not re.search(r'(agent|应用|服务|部署|系统|场景|rag)',q,re.I):return 'pure-model-training-or-multimodal-internals'
 return None
def sig(q):
 q=clean(q).lower()
 for p,k in RULES:
  if re.search(p,q,re.I):return k
 q=re.sub(r'[？?!.。，“”"()（）]','',q);q=re.sub(r'(是什么|是什么呢|有哪些|有哪几种|吗)$','',q);return '独立题：'+q
def canon(items):
 cs=[clean(x.get('originalQuestion') or x['question']) for x in items];cs=[x for x in cs if not malformed(x) and len(x)<=80]
 cs.sort(key=lambda x:(0 if re.search(r'[？?]|怎么|如何|什么|哪些|为什么|是否|有没有|能否',x) else 1,len(x)));return cs[0] if cs else clean(items[0]['question'])
def main():
 bodies={}
 for x in POSTS.read_text(encoding='utf8').splitlines():p=json.loads(x);bodies[str(p.get('contentId'))]=p.get('body') or ''
 rows=[json.loads(x) for x in SRC.read_text(encoding='utf8').splitlines() if x.strip()];ok=[];bad=[]
 for r in rows:
  why=decide(r,bodies)
  (bad if why else ok).append({'sourceId':r.get('sourceId'),'question':r.get('question'),'confidence':r.get('confidence'),'reason':why} if why else r)
 groups=defaultdict(list);idx={}
 for r in ok:
  k=(r.get('topic') or '未分类',sig(r['question']));groups[k].append(r);idx[(str(r['sourceId']),norm(r['question']))]=k
 names={'Agent系统':'Agent 系统与工作流','RAG与知识库':'RAG 与知识库','工具调用与协议':'工具调用、MCP 与安全','提示词与上下文':'提示词与上下文','模型服务与推理':'模型服务与推理工程','评测与质量':'评测、质量与安全','AI应用工程':'AI 应用工程'}; assembled={};fus=defaultdict(list)
 for k,items in groups.items():
  ss=sorted({str(x['sourceId']) for x in items});assembled[k]={'canonicalQuestion':canon(items),'variants':sorted({clean(x.get('originalQuestion') or x['question']) for x in items}),'followUps':[],'companies':sorted({x.get('company') for x in items if x.get('company')}),'occurrences':len(ss),'sourceIds':ss}
 for r in ok:
  parent=norm(r.get('followUpOf'))
  if parent:
   target=idx.get((str(r['sourceId']),parent),idx[(str(r['sourceId']),norm(r['question']))]);fus[target].append({'question':clean(r.get('originalQuestion') or r['question']),'followUpOf':clean(r.get('followUpOf')),'sourceId':str(r['sourceId'])})
 for k,fs in fus.items():
  seen=set();assembled[k]['followUps']=[f for f in fs if not ((f['question'],f['followUpOf'],f['sourceId']) in seen or seen.add((f['question'],f['followUpOf'],f['sourceId'])))]
 topics=defaultdict(list)
 for (raw,_),q in assembled.items():topics[names.get(raw,raw)].append(q)
 result={'topics':[{'topic':t,'questions':sorted(qs,key=lambda q:q['canonicalQuestion'])} for t,qs in sorted(topics.items())]};OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
 report={'version':'second-pass','inputRecords':len(rows),'acceptedRecords':len(ok),'rejectedRecords':len(bad),'rejectedByReason':dict(Counter(x['reason'] for x in bad)),'rejected':bad,'topicCount':len(topics),'coreQuestionCount':sum(map(len,topics.values())),'followUpCount':sum(len(q['followUps']) for q in assembled.values()),'uniqueSourcePosts':len({str(x['sourceId']) for x in ok})};REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
if __name__=='__main__':main()
