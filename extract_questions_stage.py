import json, re
from pathlib import Path

ROOT = Path('nowcoder_agent_mvp/data/september-2026')
src = ROOT / 'clean-posts.jsonl'
dst = ROOT / 'extracted-questions.jsonl'

AI = re.compile(r'Agent|agent|RAG|rag|Prompt|prompt|MCP|mcp|LangChain|LangGraph|Dify|Coze|模型|大模型|智能体|知识库|检索|重排|上下文|记忆|工具调用|工具|Function ?Calling|函数调用|评测|幻觉|微调|推理|向量|embedding|工作流|多智能体|AI ?应用|AI Coding|AIGC|VLM|轨迹|Harness|Sandbox|Skill|workflow|Planner|planning|Token|KV ?Cache|模型服务|模型输出|模型API|API接口|意图识别|模型编排|知识问答|多模态')
QUESTION = re.compile(r'[？?]|^(?:[-*•]\s*)?(?:\d+[.)、]|[一二三四五六七八九十]+[、.)])')

def clean_segment(s):
    s = re.sub(r'^\s*(?:[-*•]\s*)?(?:\d+[.)、]|[一二三四五六七八九十]+[、.)])\s*', '', s)
    s = re.sub(r'^\s*[“"「『]|[”"」』]\s*$', '', s)
    return re.sub(r'\s+', ' ', s).strip(' ：:，,；;')

def split_questions(line):
    # Keep each explicit question independently answerable where possible.
    parts = re.split(r'(?<=[？?])\s*', line.strip())
    return [clean_segment(x) for x in parts if clean_segment(x)]

def topic_for(q):
    if re.search(r'RAG|检索|召回|重排|BM25|知识库|切片|向量', q, re.I): return 'RAG与知识库'
    if re.search(r'MCP|工具|Function|函数调用|Skill|Sandbox', q, re.I): return '工具调用与协议'
    if re.search(r'Agent|智能体|多智能体|Harness|Workflow|工作流|Loop|Planner|轨迹', q, re.I): return 'Agent系统'
    if re.search(r'Prompt|提示词|上下文|记忆|Token', q, re.I): return '提示词与上下文'
    if re.search(r'评测|Benchmark|效果|Bad ?Case|幻觉|准确率', q, re.I): return '评测与质量'
    if re.search(r'模型|推理|微调|训练|KV ?Cache|部署|显存', q, re.I): return '模型服务与推理'
    return 'AI应用工程'

def subtopic_for(q):
    t = topic_for(q)
    return {'RAG与知识库':'召回、重排与评测','工具调用与协议':'工具编排、可靠性与安全','Agent系统':'架构、循环与状态','提示词与上下文':'Prompt、记忆与上下文管理','评测与质量':'指标、数据集与幻觉治理','模型服务与推理':'模型选型、部署与优化','AI应用工程':'应用架构与工程落地'}[t]

rows = [json.loads(x) for x in src.read_text(encoding='utf-8').splitlines() if x.strip()]
out = []
seen = set()
for row in rows:
    body = row.get('body') or ''
    previous = None
    for raw in body.splitlines():
        raw = raw.strip()
        if not raw or not QUESTION.search(raw) or not AI.search(raw):
            continue
        for q in split_questions(raw):
            if len(q) < 5 or not AI.search(q):
                continue
            # Avoid obvious non-question metadata and hashtag lines.
            if q.startswith(('#','面试岗位','岗位：','方向：','届别：')):
                continue
            key = (row['contentId'], q)
            if key in seen: continue
            seen.add(key)
            # Explicit interview wording is high confidence; narrative/uncertain wording is reviewable.
            explicit = bool(re.search(r'你|如何|怎么|什么|是否|能否|为什么|介绍|请|了解|说说|设计|判断|保证|评估|解决|处理|实现|区别|比较|包含|有哪些|吗', q))
            conf = 0.88 if explicit else 0.74
            follow = previous if re.search(r'追问|那如果|进一步追问|继续追问|具体来说|如果降级也失败|如果现在|如果让你|那如果', q) and previous else None
            obj = {'question': q, 'originalQuestion': q, 'topic': topic_for(q), 'subtopic': subtopic_for(q), 'followUpOf': follow, 'company': row.get('company'), 'sourceId': row['contentId'], 'evidence': raw, 'confidence': conf}
            out.append(obj)
            previous = q

dst.write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in out), encoding='utf-8')
print(json.dumps({'posts': len(rows), 'questions': len(out), 'sources': len({x['sourceId'] for x in out})}, ensure_ascii=False))
