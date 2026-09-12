# Nowcoder Agent Interview Collector

这是 `nowcoder-interview` Skill 的可写执行项目模板。请通过 Skill 根目录的 `scripts/bootstrap_workspace.py` 把它复制到工作区后再运行，避免把采集数据写进已安装的 Skill。

## 基础流程

```powershell
python .\scripts\build_collection_plan.py
python .\scripts\collect_filtered_nowcoder.py
python .\scripts\fetch_posts.py
python .\scripts\clean_posts.py --input .\data\september-2026\posts.jsonl --output .\data\september-2026\clean-posts.jsonl --rejects .\data\september-2026\rejected-posts.jsonl --summary .\data\september-2026\clean-summary.json --expected-company-from-candidate
```

- `build_collection_plan.py` 依据企业白名单和类别生成采集单元。
- `collect_filtered_nowcoder.py` 通过经验证的牛客页面接口分页收集候选帖子，并保存断点状态。
- `fetch_posts.py` 抓取候选帖正文，默认跳过已成功项目。
- `clean_posts.py` 执行时间窗、精确公司名、URL 和正文指纹检查。

语义筛选、跨帖去重、追问链和参考答案由 Skill 编排当前 Codex 会话的子代理完成，不需要 OpenAI API Key。

运行时生成的 `data/` 和中间输出默认不进入 Git；公开分享前仍应检查仓库中没有 Cookie、登录态或抓取正文。
