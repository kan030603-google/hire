"""Audit and organize the September 2026 extracted-question records.

This is deliberately conservative: it removes malformed fragments/headings and
questions without a direct LLM/Agent application connection, then only merges
records with the same capability signature.  A source is counted once per
core question regardless of how many variants it contributed.
"""
import json, re, unicodedata
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "nowcoder_agent_mvp" / "data" / "september-2026"
SRC = RUN / "extracted-questions.jsonl"
POSTS = RUN / "clean-posts.jsonl"
OUT = RUN / "organized-questions.json"
REPORT = RUN / "semantic-merge-report.json"

def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"^\s*(?:[（(]?\d+(?:\.\d+)*[、.．)）]?|[-•])\s*", "", s)
    return re.sub(r"\s+", "", s).strip(" \t\n\r。；;")

def signature(q, topic):
    q = norm(q).lower()
    # These are deliberately narrow capability-level equivalences, not noun matches.
    rules = [
      (r"(agentloop|agent循环|循环.*终止|停止条件|无限循环|死循环)", "agent-loop-termination"),
      (r"(短期记忆|长期记忆|上下文.*压缩|上下文.*爆|上下文.*超限|会话记忆|记忆.*污染)", "agent-memory-context"),
      (r"(多agent|多智能体|子agent|agent.*协作|agent.*分工)", "multi-agent-orchestration"),
      (r"(工具.*调用.*超时|工具.*失败|tool.*超时|工具.*重试|工具.*错误)", "tool-failure-recovery"),
      (r"(工具.*权限|危险.*工具|越权|写操作权限|mcp.*权限)", "tool-permission-safety"),
      (r"(mcp.*skill|skill.*mcp|mcp.*http|mcp.*api)", "mcp-boundaries"),
      (r"(工具.*检索|工具.*路由|toollist.*多|工具.*过多|动态工具)", "tool-discovery-routing"),
      (r"(rag.*召回.*(优化|提升)|提升.*召回|召回.*准确|召回率)", "rag-retrieval-optimization"),
      (r"(切片|chunk|分块)", "rag-chunking"),
      (r"(rerank|重排|排序策略|bm25.*向量|向量.*bm25)", "rag-ranking"),
      (r"(知识库.*更新|知识库.*保鲜|索引.*增量|文档.*更新|文档.*删除)", "rag-index-freshness"),
      (r"(幻觉|拒答|强行完成)", "hallucination-governance"),
      (r"(评测集.*(设计|构建)|agent.*评测|效果.*评估|评测体系)", "agent-evaluation"),
      (r"(结构化输出|json.*(合法|解析|约束)|流式.*json)", "structured-output"),
      (r"(模型.*超时|调用.*超时|限流|qps|服务.*挂|降级)", "model-service-reliability"),
      (r"(推理.*(成本|时延|延迟|优化)|token.*(消耗|成本|优化)|kv\s*cache)", "inference-performance"),
      (r"(prompt.*(设计|怎么写|system)|系统提示词|上下文.*组装)", "prompt-context-design"),
      (r"(langchain|langgraph|autogen|crewai|框架.*自研|原生.*agent)", "agent-framework-selection"),
      (r"(agent.*架构|从零.*agent平台|agentinfra|agent.*核心模块)", "agent-architecture"),
      (r"(agent.*监控|观测|trace|告警)", "agent-observability"),
      (r"(大模型.*模型.*(选择|选型)|用小模型.*大模型|使用哪些模型)", "model-selection"),
    ]
    for pat, key in rules:
        if re.search(pat, q): return key
    # Exact normalized text is safe; otherwise retain it as an independent question.
    return "exact:" + q

def malformed(q):
    qn = norm(q)
    if len(qn) < 6 or qn.startswith(("”", "——", "（", "【", "比如说", "从数据、", "(我们")):
        return True
    # Section headings and labels are not independently answerable questions.
    headings = ("mcp与agent", "agent项目", "agent框架", "agent架构", "agent系统评估", "skill路由与上下文",
                "skill安全与测试", "ai coding", "知识库搭建", "大模型基础", "部门使用agent的情况",
                "生图、视频模型与降级策略", "工程落地与ai开发工具", "大模型通用题", "大模型推理算子")
    interrogative = re.search(r"(怎么|如何|哪些|什么|为什么|是否|有没有|多少|哪里|区别|优缺点|会不会|能否|应不应该|哪一)", qn)
    if qn.lower() in headings or ("？" not in qn and "?" not in qn and len(qn) < 20 and not interrogative):
        return True
    return False

AI_TERMS = re.compile(r"agent|智能体|大模型|llm|rag|知识库|检索|向量|embedding|rerank|重排|prompt|模型|mcp|tool|工具调用|langchain|langgraph|ai coding|ai工具|多模态|推理|kv\s*cache|harness|skill", re.I)
GENERIC = re.compile(r"^(go的内存模型|redis采用|jvm的内存模型|threadlocal|yaml好了后续呢|一个完整数据|什么是.*协程|goroutine|spark|kubectl)", re.I)

def reason(row, bodies):
    q, ev, sid = norm(row.get("question")), row.get("evidence") or "", str(row.get("sourceId"))
    body = bodies.get(sid, "")
    if not body or ev not in body: return "missing-or-unlocatable-evidence"
    if malformed(q): return "malformed-fragment-or-heading"
    if GENERIC.search(q) or not AI_TERMS.search(q): return "not-directly-agent-or-ai-application"
    # Candidate self-narration / meta-comment rather than an interview question.
    if re.search(r"(我心想|给后面的人建议|正确思路是|我的表现你认为|面试官介绍当前岗位)", q):
        return "self-narration-or-meta-comment"
    return None

def main():
    bodies = {}
    for line in POSTS.read_text(encoding="utf-8").splitlines():
        p = json.loads(line)
        bodies[str(p.get("contentId"))] = p.get("body") or ""
    rows = [json.loads(x) for x in SRC.read_text(encoding="utf-8").splitlines() if x.strip()]
    accepted, rejected = [], []
    for r in rows:
        why = reason(r, bodies)
        if why: rejected.append({"sourceId": r.get("sourceId"), "question": r.get("question"), "confidence": r.get("confidence"), "reason": why})
        else: accepted.append(r)

    groups = {}
    for r in accepted:
        key = (r.get("topic") or "未分类", signature(r["question"], r.get("topic") or ""))
        groups.setdefault(key, []).append(r)

    topics = defaultdict(list)
    kept_by_source_question = {(str(r["sourceId"]), norm(r["question"])): r for r in accepted}
    followup_total = 0
    for (topic, key), items in sorted(groups.items()):
        # first source wording remains canonical; frequency then favors a stable repeated wording.
        texts = [norm(x.get("originalQuestion") or x["question"]) for x in items]
        counts = Counter(texts)
        canonical = sorted(counts, key=lambda t: (-counts[t], texts.index(t)))[0]
        variants = sorted(set(texts))
        followups = []
        for x in items:
            parent = norm(x.get("followUpOf"))
            if parent:
                # retain only an evidenced, independently accepted continuous follow-up.
                label = norm(x.get("originalQuestion") or x["question"])
                if label != canonical:
                    followups.append({"question": label, "followUpOf": parent, "sourceId": str(x["sourceId"])})
        uniq_follow = []
        seen_fu = set()
        for f in followups:
            k = (f["question"], f["followUpOf"], f["sourceId"])
            if k not in seen_fu: seen_fu.add(k); uniq_follow.append(f)
        followup_total += len(uniq_follow)
        sids = sorted({str(x["sourceId"]) for x in items})
        topics[topic].append({"canonicalQuestion": canonical, "variants": variants,
                              "followUps": uniq_follow, "companies": sorted({x.get("company") for x in items if x.get("company")}),
                              "occurrences": len(sids), "sourceIds": sids})
    result = {"topics": [{"topic": t, "questions": qs} for t, qs in sorted(topics.items())]}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {"inputRecords": len(rows), "acceptedRecords": len(accepted), "rejectedRecords": len(rejected),
              "rejectedByReason": dict(Counter(x["reason"] for x in rejected)), "rejected": rejected,
              "topicCount": len(topics), "coreQuestionCount": sum(map(len, topics.values())),
              "followUpCount": followup_total, "uniqueSourcePosts": len({str(x["sourceId"]) for x in accepted})}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__": main()
