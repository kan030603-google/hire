# Nowcoder Interview Skill

一个可独立安装的 Codex Skill，用于按企业白名单、岗位二级类别和月份采集牛客面试经验，抽取面试题、语义去重、整理追问链并生成参考答案。

## 安装

把 `skill/nowcoder-interview` 整个目录复制到：

```text
$CODEX_HOME/skills/nowcoder-interview
```

重新打开 Codex 任务后，可以用“使用 nowcoder-interview skill，采集 9 月面试经验”等自然语言触发。

## 初始化执行项目

Skill 首次执行时会按需创建可写工作区。也可以手动初始化：

```powershell
python .\skill\nowcoder-interview\scripts\bootstrap_workspace.py --destination <你的工作区目录>
```

这会创建 `<你的工作区目录>/nowcoder_agent_mvp`。配置、采集脚本和状态文件都在该目录内；已安装 Skill 只作为只读模板使用。

## 包含内容

- `SKILL.md`：完整编排入口和硬约束。
- `references/`：原始工作流、筛选政策与阶段数据契约。
- `agents/openai.yaml`：Skill 展示元数据。
- `assets/nowcoder_agent_mvp/`：企业配置、采集计划和稳定脚本。
- `scripts/bootstrap_workspace.py`：把随附模板复制到可写工作区。

Computer Use 不是硬依赖。可使用浏览器界面，也可使用先经页面行为验证的牛客页面后端接口；两种通道都必须保留企业选择、类别、排序、分页和时间窗证据，不能退化成全站抓取后按标题过滤。

## 数据与合规

发布包不包含抓取的帖子、登录态、Cookie、采集状态或生成的题库。使用时请遵守牛客服务条款并控制访问频率；遇到登录失效、验证码、权限页或站点结构变化时应停止对应单元，不得规避限制。

本包未预设开源许可证。公开发布前请根据你的分发意图选择并加入合适的 `LICENSE`。

## 上传到 GitHub

在本目录内执行：

```powershell
git init
git add .
git commit -m "Add standalone Nowcoder interview skill"
git branch -M main
git remote add origin <你的空 GitHub 仓库 URL>
git push -u origin main
```
