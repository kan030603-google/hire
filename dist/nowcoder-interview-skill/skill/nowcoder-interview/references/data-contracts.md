# 数据契约

所有 JSON 使用 UTF-8；JSONL 每行一个对象。未知字段用 `null`，不要编造。

## 采集计划

`nowcoder_agent_mvp/data/september-2026/collection-plan.json`

必需顶层字段：`targetWindow`、`currentlyCollectableThrough`、`companyCount`、`sourceSelectorCount`、`categoryCount`、`unitCount`、`units`。每个单元必须包含 `id`、`company`、`sourceCompanySelectors`、`category`、`sort` 和 `sourceCompanySelectionRequired: true`。

## 运行目录

- 正式模式：`nowcoder_agent_mvp/data/september-2026/`
- 冒烟模式：`nowcoder_agent_mvp/data/smoke-test/`

下文的 JSON/JSONL 文件均写入当前模式的运行目录。冒烟模式只能读取正式或历史数据作为测试输入，不能覆盖它们。

## 候选帖子

列表层输出当前运行目录的 `candidates.json`：

```json
{
  "run": {"mode":"smoke|full","startedAt":"ISO-8601","updatedAt":"ISO-8601"},
  "candidates": [
    {
      "url":"https://www.nowcoder.com/...",
      "title":"原始标题",
      "company":"规范企业组",
      "sourceCompanySelector":"牛客实际选项",
      "category":"二级类别",
      "publishedAt":"ISO-8601 或 null",
      "unitId":"company-001"
    }
  ]
}
```

同一 URL 只保留一条；若跨类别或企业重复，在数组字段中合并来源上下文。

## 正文

当前运行目录的 `posts.jsonl` 每行至少包含 `url`、`pageTitle`、`body`、`createdAt`、`contentId`、`fetchError`。成功记录必须有非空 `body`；失败记录必须保留 `fetchError`，以便续跑。

## 确定性清洗

调用 `scripts/clean_posts.py` 对正文再做一次时间窗、明确的他公司标题误召回和内容指纹去重。产物为：

- `clean-posts.jsonl`：唯一允许进入语义抽取阶段的正文；每条增加 `contentFingerprint`、`detectedTitleCompanies` 和 `attributionStatus`。
- `rejected-posts.jsonl`：保留原记录、`contentFingerprint`、`detectedTitleCompanies` 和 `rejectionReasons`，不得静默丢弃。
- `clean-summary.json`：记录输入、接受、拒绝数量，原因分布、发现的重复组与时间窗。

站点企业选择器是必需的召回约束，但不是企业归属的最终证据。标题明确指向另一家企业时，当前采集单元必须隔离该帖子。

## 抽取题目

当前运行目录的 `extracted-questions.jsonl` 只能读取 `clean-posts.jsonl`，每行表示一个可独立回答的问题：

```json
{
  "question":"规范化问题",
  "originalQuestion":"尽量保留原始问法",
  "topic":"主题",
  "subtopic":"子主题",
  "followUpOf":null,
  "company":"企业组",
  "sourceId":"contentId 或 URL 指纹",
  "evidence":"支持该问题确实出现的简短正文片段",
  "confidence":0.0
}
```

不得从经验描述中凭空生成面试题。`evidence` 必须是可在 `sourceId` 对应正文中定位的原文片段；无法判断是否为面试官问题时降低 `confidence`，不要伪装成确定事实。

## 归并结果

当前运行目录的 `organized-questions.json` 包含 `topics`；每个题目必须有 `canonicalQuestion`、`variants`、`followUps`、`companies`、`occurrences` 和 `sourceIds`。`occurrences` 必须能由抽取记录重新计算。

## 最终题库与测试报告

- 正式输出：`output/agent_ai_interview_question_bank.md`
- 冒烟输出：`output/smoke-test-question-bank.md`
- 正式运行状态：`data/september-2026/run-state.json`
- 冒烟状态：`data/smoke-test/run-state.json`
- 冒烟报告：`data/smoke-test/report.json`

报告至少记录各阶段状态、模型、输入输出计数、最早/最晚日期、错误与是否通过。冒烟模式不得覆盖正式产物。
