---
name: nowcoder-interview
description: 采集并整理牛客网 Agent 开发与 AI 应用研发面试经验。用于按企业白名单、岗位二级类别和月份批量抓取帖子，抽取面试题，跨帖子语义去重，整理追问链并生成参考答案；也用于续抓、失败恢复和小规模冒烟测试。
---

# 牛客 Agent / AI 应用面经

把当前会话作为总调度器。先完整读取 [工作流](references/workflow.md)、[筛选政策](references/screening-policy.md) 和 [数据契约](references/data-contracts.md)，再执行任务。本文件中的独立安装规则优先于工作流里针对原始开发仓库的“工作目录”说明。

## 初始化工作区

1. 将包含本文件的目录记为技能根目录。
2. 用 `git rev-parse --show-toplevel` 定位当前工作区根目录；若当前目录不在 Git 仓库中，就以当前目录为工作区根目录。
3. 如果工作区根目录已有 `nowcoder_agent_mvp`，直接使用它。否则运行：

   ```powershell
   python <技能根目录>/scripts/bootstrap_workspace.py --destination <工作区根目录>
   ```

4. 运行时生成的 `data/`、`output/` 和状态文件只能写入工作区内的 `nowcoder_agent_mvp`，不得写回已安装技能目录或修改 `assets/` 模板。
5. 读取执行项目的 `config/collection.september-2026.json` 和其中引用的企业注册表。除非用户明确修改范围，否则不得绕过企业筛选、扩大类别或抓取时间窗外内容。
6. 运行或修改文件前，读取工作区适用的 `AGENTS.md`；写入批次前确认已有成功记录并使用断点续跑。

## 不可放宽的采集约束

- 企业白名单、岗位二级类别、月份和 `rejectUnfilteredQueries=true` 都是硬约束。
- Computer Use 不是硬依赖。可以使用浏览器界面，也可使用先经页面行为验证的牛客页面后端接口。
- 接口通道必须先确认端点、字段和值；不得猜测参数，不得全站抓取后再按标题或公司关键词过滤。
- 每个采集单元必须保存企业选择返回的 `companyId`、岗位 `jobId`、`level`、`order=3`、分页范围和越过时间窗的证据。
- Computer Use 缺失表示当前运行环境没有提供该工具，不代表用户禁止。除非用户明确要求，不要为此安装或修复浏览器插件。
- 企业名只允许 Unicode NFKC 规范化并移除类别为 `Cf` 的不可见格式字符后做严格匹配；禁止模糊匹配。必须保留源站原始企业名和 `companyId`。例如 DeepSeek 企业名末尾含 `U+200C` 时，只移除该格式字符后严格比较。
- 遇到登录失效、验证码、权限页或站点结构变化时停止对应单元并报告，不得规避限制。

## 基础命令

从工作区根目录运行：

```powershell
python nowcoder_agent_mvp/scripts/build_collection_plan.py
python nowcoder_agent_mvp/scripts/collect_filtered_nowcoder.py
python nowcoder_agent_mvp/scripts/fetch_posts.py
python nowcoder_agent_mvp/scripts/clean_posts.py --input nowcoder_agent_mvp/data/september-2026/posts.jsonl --output nowcoder_agent_mvp/data/september-2026/clean-posts.jsonl --rejects nowcoder_agent_mvp/data/september-2026/rejected-posts.jsonl --summary nowcoder_agent_mvp/data/september-2026/clean-summary.json --expected-company-from-candidate
```

重复执行会读取已有状态继续。要重新检查已完成单元，为 `collect_filtered_nowcoder.py` 增加 `--refresh-completed`；只重试失败单元时增加 `--retry-failed`。

## 阶段编排

严格按工作流执行：预检计划、采集、确定性清洗、语义筛选、跨帖合并与追问链、参考答案、最终审计。语义阶段使用当前 Codex 会话的子代理，不需要 OpenAI API Key：

- 采集与去重编排：`gpt-5.6-terra`，`medium`。
- 语义筛选：`gpt-5.6-luna`，`medium`。
- 终审与答案：`gpt-5.6-sol`，`high`。

只有所有阶段通过数据契约、脚本校验和冒烟测试后，才能声称指定月份采集完成。最终报告必须包含完成、失败和跳过单元数，各阶段输入输出计数，拒绝原因，问题数、来源数、未完成原因，以及实际使用的模型和采集通道。
