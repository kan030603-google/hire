# V1 技术总览

> 日期: 2026-04-02
> 版本: V1.1（离线构建 + 本地展示 + 服务通话记录接入）
> 代码量: ~4,200 行 Python，29 个源文件
> 定位: 本文档详细到可从零重构整个系统核心链路

---

## 一、系统定位

离线构建 + 本地展示的**用户画像与长期记忆 Demo**。从高途在线教育平台的用户行为数据中，自动提取三层画像和三层记忆。

覆盖场景：K12 教育（小/初/高各学科、中高考）、成人教育（考公/考编/教资/会计/考研/法考/医考）、职业技能（编程/IT/语培）、个人特质（性格/成长/学习风格）。

**构建与展示完全分离**：
- 构建阶段（`build_all.py`）：调用 LLM，耗时较长，产出 per-user JSON
- 展示阶段（Streamlit）：纯读取 JSON，零 LLM 调用，秒级加载

---

## 二、核心架构

### 2.1 三层记忆链路

```
原始行为数据 (parquet, 9 张表 + 服务通话记录)
    ↓ [Rule] 结构化 + 过滤无效记录
HistoryEvent（行为流水，append-only，最小行为单元）
    ↓ [Rule+LLM] 按窗口聚合（session / 天 / 周）
EventSummary（事件摘要，持久化中间层）
    ↓ [LLM] 按周批次抽取 + merge 协议
FactMemory（事实记忆，category+tag+statement，有演化历史）
```

### 2.2 三层用户画像

```
L1 StaticProfile  ← 纯规则，从用户特征宽表直接映射
L2 StatProfile    ← 统计规则（付费/活跃/参与强度），不依赖 LLM
L3 InterestProfile← LLM 从最终态 FactMemory + 最近4周 EventSummary 一次性生成
```

### 2.3 完整 Pipeline 顺序

```
1. [Rule]  加载原始数据（8 张表 + 课程维表 + 服务通话记录）
2. [Rule]  构建 HistoryEvent（过滤无效记录）
3. [Rule]  L1 静态画像
4. [Rule]  L2 统计画像（第一轮，暂不含 subject_engagement）
5. [LLM]   EventSummary 生成（AI搜索/社区/课程→LLM，订单→规则）  ← call #1
5.5[Rule]  补充 L2 subject_engagement（融合课程/订单/EventSummary tags）
6. [LLM]   逐周循环：FactMemory 候选抽取                          ← call #2
7. [LLM]   逐周循环：FactMemory Merge                             ← call #3
8. [LLM]   L3 兴趣画像生成（全部周完成后，从最终态一次性生成）    ← call #4
9. [Rule]  组装 DemoUserBundle → 写入 JSON
```

---

## 三、Schema 定义

### 3.1 HistoryEvent

```python
class HistoryEvent(BaseModel):
    id: str = Field(default_factory=_new_id)        # UUID hex
    user_key: str
    source_type: str    # "ai_search" / "course_behavior" / "order" / "community" / "service_call"
    event_time: datetime
    structured_data: dict
    created_at: datetime = Field(default_factory=datetime.now)
```

**structured_data 字段（按 source_type）**：

| source_type | 字段 |
|---|---|
| ai_search | session_id, message_type(1=用户/2=AI), card_type(10=纯文本/1=AI回答/3=推荐问题), content, session_time |
| course_behavior | event_type, course_number, clazz_number, page_id, dt |
| order | price, order_status(2=已付费), channel_name, course_number, clazz_number, create_time, paid_time, refund_time, refund_price |
| community | moment_id, poster_number, actions(list), dt |
| service_call | task_id, call_type_content, communication_summary_content, learning_character_content, weak_points, learning_interests_habits, refund_intention_content, renew_resistance, course_satisfaction_content, parent_expectations, school_score, school_rank, school_course_progress, school_version, child_schedule, study_leader, listen_way, parent_accompanying_listening, english_foundation, weekend_arrangements, school_curriculum_adaptation |

**时间字段解析优先级**：
- AI 搜索：`create_time`（毫秒时间戳，÷1000 转 datetime）
- 课程行为：`event_time`（"2026-01-04 20:18:32:871"，`%Y-%m-%d %H:%M:%S:%f`）> `dt`（"20260210"，`%Y%m%d`）
- 订单：`paid_time` > `create_time`（"2024-08-02 10:31:14"，`%Y-%m-%d %H:%M:%S`）
- 社区互动：`dt`（"20260210"，`%Y%m%d`）

**入库过滤规则**：
- 课程行为：`course_number` 非空且不在 {"None", "0", "-1", "nan"} → 仅保留 11.6%
- 社区互动：`actions` 列表中至少一个值为 1（click/like/comment/save/share）→ 纯曝光过滤，仅保留约 1.7%
- AI 搜索 / 订单：无过滤，100% 入库
- 服务通话：按 task_id 去重（保留 event_time 最晚的一条），100% 入库

---

### 3.2 EventSummary

```python
class EventSummary(BaseModel):
    id: str = Field(default_factory=_new_id)
    user_key: str
    source_type: str    # "ai_search_session" / "course_daily" / "order_weekly" / "community_daily" / "service_call"
    window_start: datetime
    window_end: datetime
    content: str        # 摘要文本（LLM 生成或规则拼接）
    tags: list[str]     # 学科/主题标签，下游用于 subject_engagement
    history_event_ids: list[str]
    created_at: datetime = Field(default_factory=datetime.now)
```

---

### 3.3 FactMemory

```python
class FactMemory(BaseModel):
    id: str = Field(default_factory=_new_id)
    user_key: str
    category: Literal["preference", "status", "need", "ability", "trait"]
    tag: str            # merge 分组键（与 category 组合）
    statement: str      # 一句话结论
    source_summary_ids: list[str]
    created_at: datetime    # 批次 EventSummary 最晚 window_end（事件时间）
    updated_at: datetime
    status: Literal["active", "inactive"] = "active"
    merged_from: Optional[str] = None   # 指向最早被替代的旧 fact id
```

**Category + 固定 Tag（V1）**：

每个 category 末尾保留"其他"兜底，用于暂时无法归类但有画像价值的信息，后续用户量大后再视频率决定是否新增固定 tag。

| category | 固定 tag |
|---|---|
| preference | 学科偏好, 课程类型偏好, 学习方式偏好, 讲师偏好, 内容风格偏好, 其他 |
| status | 学习年级, 家庭角色, 备考阶段, 购课状态, 其他 |
| need | 作业解题, 学科辅导, 备考目标, 升学规划, 考证需求, 职业发展, 成长需求, 其他 |
| ability | 数学能力, 语文能力, 英语能力, 物理能力, 化学能力, 生物能力, 历史能力, 地理能力, 政治能力, 综合学习能力, 其他 |
| trait | 学习性格, 学习风格, 行为习惯, 家庭角色特质, 成长驱动, 其他 |

**Tag 固定的意义**：merge 分组键为 `category + tag`，tag 自由生成会产生"学科偏好"/"课程学科偏好"/"学科内容偏好"等同义词，导致同类信息永远无法触发 merge。固定 tag 后，同一用户的同类信息保证落在同一个 slot，merge 可靠运作。

**V1 覆盖率**：基于 5 个种子用户 995 条 active FactMemory 测算，覆盖约 **90%+**（78% 精确命中 + 约 12% LLM 归入最近似 tag）。

**记忆过期机制（双层）**：

FactMemory 新增 `expires_at` 字段，过期后 status 自动标为 inactive。

*第一层（LLM 提取）*：6 个时效性 tag 在含明确时间锚点时由 LLM 输出 expires_at，采用保守策略（宁缺毋滥，泛化表达不设置）。可识别的 tag：备考阶段、学习年级、备考目标、学科辅导、升学规划、考证需求。

*第二层（base TTL 兜底）*：未设 expires_at 时，按 tag 默认有效期在 build 时填入。

| tag | base TTL |
|---|---|
| 购课状态 | 30 天 |
| 作业解题 | 14 天 |
| 备考阶段、备考目标 | 365 天 |
| 学科辅导 | 180 天 |
| 学习年级、升学规划、考证需求 | 365 天 |
| ability/全部 | 180 天 |
| preference/课程类型偏好、学习方式偏好 | 365 天 |
| 家庭角色、trait/全部、其余 | 无 TTL |

---

### 3.4 UserProfile（三层画像）

#### L1 StaticProfile

```python
class StaticProfile(BaseModel):
    grade: Optional[str] = None                  # "初三" / "高一"
    region: Optional[str] = None                 # "四线城市"
    subject_preferences: list[str] = []          # ["数学", "语文"]
    register_channel: Optional[str] = None       # "高途"
```

#### L2 StatProfile

```python
class StatProfile(BaseModel):
    payment_level: Optional[str] = None              # "低" / "中" / "高"
    learning_stage: Optional[str] = None             # 直接取 lessoning_status 字段
    recent_activity_intensity: Optional[str] = None  # "高" / "中" / "低" / "沉默"
    course_engagement: Optional[str] = None          # "高" / "中" / "低"
    ai_usage_intensity: Optional[str] = None         # "高" / "中" / "低" / "无"
    subject_engagement: dict[str, float] = {}        # {"高中": 0.73, "数学": 0.07}
```

#### L3 InterestProfile

```python
class InterestProfile(BaseModel):
    interest_subjects: list[str] = []       # ["数学", "物理", "英语"]
    interest_directions: list[str] = []     # ["解题技巧", "考试备考"]
    focus_areas: list[str] = []             # ["高考", "升学规划"]
    purchase_intent: Optional[str] = None  # "中-有付费购课且关注课程规划"
    active_time_slots: list[str] = []       # ["晚间(18-22点)", "下午(12-18点)"]（规则统计）
    personality_traits: list[str] = []     # ["探究型", "目标导向", "家长角色"]
    growth_needs: list[str] = []            # ["时间管理", "学习习惯", "升学规划能力"]
```

#### DemoUserBundle

```python
class DemoUserBundle(BaseModel):
    user_key: str
    profile: UserProfile
    history_events: list[HistoryEvent]
    event_summaries: list[EventSummary]
    fact_memories: list[FactMemory]     # active + inactive 全量
    build_trace: list[TraceItem]
    token_summary: dict
    built_at: datetime
```

```python
class TraceItem(BaseModel):
    step: str           # "static_extract" / "event_summary" / "fact_extract_merge" / ...
    method: str         # "rule" / "llm"
    description: str
    duration_ms: int = 0
    token_cost: int = 0
    llm_calls: int = 0
    cached_calls: int = 0
```

---

## 四、HistoryEvent 构建规则

**函数**：`history_builder.py::build_history_events(user_key, ai_records, courses, orders, community) → list[HistoryEvent]`

分别调用各来源的 `_build_*_event()` 函数，过滤后合并，按 `event_time` 升序排列。

### 4.1 AI 搜索事件

```python
# 每条 ai_search 记录 → 1 条 HistoryEvent
# event_time = create_time / 1000（毫秒时间戳）
# 全部入库，无过滤
structured_data = {
    "session_id": str,
    "message_type": str,      # "1"=用户, "2"=AI
    "card_type": str,         # "10"=纯文本, "1"=AI回答, "3"=推荐问题
    "content": str,
    "session_time": int,
}
```

### 4.2 课程行为事件

```python
# 过滤条件：course_number 非空 + 不在 {"None", "0", "-1", "nan"}
# event_time 优先 event_time 字段，fallback dt（"20260210" → datetime）
structured_data = {
    "event_type": str,
    "course_number": str,
    "clazz_number": str,
    "page_id": str,
    "dt": str,
}
```

### 4.3 订单事件

```python
# 全部入库
# event_time = paid_time > create_time
structured_data = {
    "price": str,
    "order_status": int,       # 2=已付费
    "channel_name": str,
    "course_number": str,
    "clazz_number": str,
    "create_time": str,
    "paid_time": str,
    "refund_time": str,
    "refund_price": str,
}
```

### 4.4 社区互动事件

```python
# 过滤条件：actions 中至少一个为 1（click/like/comment/save/share）
# 纯曝光（全为 0）直接跳过
# event_time = dt（"20260210" → datetime，时间为当天 00:00:00）
structured_data = {
    "moment_id": str,
    "poster_number": str,
    "actions": list,           # [click, like, comment, save, share]，1/0
    "dt": str,
}
```

---

## 五、EventSummary 生成规则

**函数**：`event_summarizer.py::generate_event_summaries(history_events, llm_client, user_key, community_contents, course_dim, return_stats) → list[EventSummary]`

按 source_type 分组，分别调用对应聚合策略。

### 5.1 AI 搜索：按 session_id 聚合（LLM）

```python
# 1. 按 session_id 分组 HistoryEvent，按 event_time 排序
# 2. 对每个 session 构造对话文本：
#    - card_type=3（推荐问题）→ 跳过
#    - message_type=1 → "用户: {content}"
#    - message_type=2 → "AI: {content}"（card_type=1 时解析 JSON {"content": "..."}）
#    - content 截断到 500 字符
# 3. 跳过条件（不生成 EventSummary）：
#    - 对话为空或无"用户:"标记
#    - LLM 返回"无教育相关内容"
#    - LLM 内容风控（is_content_filter=True）→ 记录 content_filtered_sessions
#    - 其他 LLM 失败 → 记录 failed_sessions，继续
# 4. tags = _extract_tags_from_summary(content)
# 5. window_start = min(event_time)，window_end = max(event_time)
```

**System Prompt 要点**：
- 场景：K12/成人教育/职业技能/教育政策
- 与教育完全无关 → 输出固定文本 `无教育相关内容`
- 输出3-5条要点，描述用户意图和关注点
- 如有性格特质信号（持续追问/重传题目等）可自然提及

### 5.2 课程行为：按天聚合（LLM + 规则 fallback）

```python
# 有 course_dim + llm_client → LLM
# 步骤：
# 1. 按天分组（dt 字段）
# 2. 去重 course_number
# 3. 从 course_dim join：course_name, subject_name, grade_name, course_type_name
# 4. 最多 15 条课程（防止 prompt 过长）
# 5. 格式化："{course_name}（{grade_name}·{subject_name}·{course_type_name}）"
#
# 无 course_dim 或 LLM 失败 → 规则 fallback：
# content = f"当日浏览了 {len(courses)} 门课程，共 {len(events)} 次页面事件"
# tags = ["课程浏览"]
```

### 5.3 订单：按 ISO 周聚合（纯规则）

```python
# 按 isocalendar() 周分组，key = f"{year}-W{week:02d}"
# 统计：
#   paid_count  = len([o for o if order_status==2])
#   total_amount= sum(float(price) for paid orders)
#   refund_count= len([o for o if refund_time 非空且非"None"])
# 关联课程维表（join course_number）：
#   提取 course_name, grade_name, subject_name_lvl3, course_type_name
#   退款订单标记【退款】
#
# content = "本周 X 笔订单，已付费 Y 笔（Z 元），退款 N 笔。购课：课程1；课程2【退款】"
#
# tags:
#   - 固定 "订单"
#   - 从 course_dim 提取 app_subject_name_lv1（按逗号分割），加入 tags
```

### 5.4 社区互动：按天聚合（LLM + 规则 fallback）

```python
# 有动态内容宽表 + llm_client → LLM
# 步骤：
# 1. 按天分组 HistoryEvent（取 dt）
# 2. 从动态内容宽表匹配 moment_id，提取 title + content（前200字）
# 3. 最多 20 条动态（防止 prompt 过长）
# 4. LLM 返回"无教育相关内容" → 过滤，不生成 EventSummary
#
# 无内容或 LLM 失败 → 规则 fallback：
# content = f"当日动态互动: 点击 X 条，点赞 Y 条，评论 Z 条"
# tags = ["社区互动"]
```

### 5.5 服务通话：一通电话 = 一条 EventSummary（纯规则）

```python
# ASR 后端已预提取 22 个结构化字段，EventSummary 直接规则拼装
# 不走 LLM，不做时间聚合（一通电话 = 一条 EventSummary）
#
# _build_service_call_content(data) 解析逻辑：
# 1. 通话摘要：communication_summary_content → JSON {"总结": ..., "重点事项": [...]}
#    - 提取"总结"字段 + flag=True 的重点事项（最多3条）
# 2. 学习特征：learning_character_content → _clean_plain_text()
# 3. 薄弱点：weak_points → JSON [{"subject": ..., "weak": ...}] → "学科：薄弱点"
# 4. 退费相关：refund_intention_content → JSON {"退费意向": ..., "退费归因": ..., "退费预测": ...}
# 5. 续费阻力：renew_resistance → JSON list → 逐条拼接
# 6. 课程满意度：course_satisfaction_content → 尝试 "course_evaluation"/"匹配度" 等 key
# 7. 家长期望/学校成绩/排名/进度/学习主导/英语基础/上课方式/日程/周末安排等
#
# tags = ["服务通话"] + [call_type_content]（如"续班"/"课后督学"/"阶段反馈/日常"）
```

**桥接表打通方案**：

ASR 通话表（表0）无 `user_number` 字段，通过 IM 消息桥接表（表1）建立映射：

```
表1: service_dw.dwd_service_chat_private_messages_di
  → user_number（平台 user_key）↔ receiver/sender_username（7881... 格式 IM ID）
表0: u_strategy.dwd_service_call_asr_backend_info_hi
  → task_id 按 '_' 分割，第3段 = IM ID
Spark 脚本: extract_call_records_raw_spark_job.py
  Step 1: 桥接表过滤种子用户，CASE WHEN 双向取 7881% 格式 IM ID
  Step 2: 建 1:1 唯一映射（歧义键丢弃）
  Step 3: 用映射 JOIN 表0，拉取结构化字段（排除 audio_content 等大字段）
```

**覆盖率**：50 个种子用户中仅 9 个有服务通话数据（EM 素养业务线）。

### 5.6 Tags 自动提取（所有类型通用）

**函数**：`_extract_tags_from_summary(content: str) -> list[str]`

基于关键词 → 标签的映射字典，在摘要文本中匹配：

```python
keywords = {
    # K12 主科
    "数学": "数学", "语文": "语文", "英语": "英语", "物理": "物理",
    "化学": "化学", "生物": "生物", "历史": "历史", "地理": "地理",
    "政治": "政治", "道德与法治": "道德与法治", "科学": "科学",
    # K12 其他
    "编程": "编程", "Python": "Python", "信息技术": "信息技术",
    "音乐": "音乐", "美术": "美术", "体育": "体育",
    "作文": "作文", "阅读": "阅读", "口语": "口语",
    # K12 专项
    "艺考": "艺考", "竞赛": "竞赛", "奥数": "奥数",
    "选科志愿": "选科志愿", "小升初": "小升初", "幼升小": "幼升小",
    # 考试
    "中考": "中考", "高考": "高考", "考研": "考研",
    "考公": "考公", "考编": "考编", "公务员": "公务员", "省考": "省考",
    "事业单位": "事业单位",
    "教资": "教资", "教师资格": "教资", "教师招考": "教师招考",
    "会计": "会计", "CPA": "CPA", "财会": "财会考试",
    "法考": "法考", "司法考试": "法考",
    "医考": "医考", "医疗考试": "医疗考试",
    # 语言/出国
    "雅思": "雅思", "托福": "托福", "剑桥英语": "剑桥英语",
    "日语": "日语", "法语": "法语", "韩语": "韩语",
    "留学": "升学规划", "出国": "升学规划",
    # 家庭/规划
    "家庭教育": "家庭教育", "亲子": "家庭教育",
    "志愿": "志愿填报", "升学": "升学规划",
    # 通用（非学科，不参与 subject_engagement）
    "解题": "解题技巧", "备考": "备考", "课程": "课程推荐",
}
```

---

## 六、L1 静态画像提取规则

**函数**：`static_extractor.py::extract_static_profile(user_features, subject_preferences) → StaticProfile`

| 字段 | 提取源 | 逻辑 |
|---|---|---|
| grade | `app_grade`（优先） → `grade`（数值） | 优先中文值；数值走映射表 |
| region | `city_level` | 直接取值 |
| subject_preferences | `app_subject_name_lv1/lv2` + `his_subject_name_seq`（逗号分割） | 两源合并去重保序 |
| register_channel | `regist_product_name` | 直接取值 |

**Grade 映射表**：
```python
_GRADE_MAP = {
    1: "一年级", 2: "二年级", 3: "三年级", 4: "四年级",
    5: "五年级", 6: "六年级",
    7: "初一", 8: "初二", 9: "初三",
    10: "高一", 11: "高二", 12: "高三",
}
```

---

## 七、L2 统计画像提取规则

**函数**：`ability_extractor.py::extract_stat_profile(user_features, orders, course_behaviors, ai_search_records, course_dim, event_summaries) → StatProfile`

### 7.1 各字段分桶规则

| 字段 | 数据源 | 分桶 |
|---|---|---|
| payment_level | 已付费订单（order_status==2）总金额 | <500→"低", 500-5000→"中", ≥5000→"高" |
| learning_stage | `lessoning_status` | 直接取值 |
| recent_activity_intensity | `m_continuous_active_days` | ≥20→"高", ≥5→"中", ≥1→"低", 0→"沉默" |
| course_engagement | 课程行为记录总数 | ≥500→"高", ≥50→"中", ≥1→"低", 0→None |
| ai_usage_intensity | 去重 session_id 数 | ≥100→"高", ≥20→"中", ≥1→"低", 0→"无" |

### 7.2 subject_engagement 计算

**函数**：`ability_extractor.py::_calc_subject_engagement(orders, course_behaviors, course_dim, event_summaries) → dict[str, float]`

**来源权重**：
```python
_SUBJECT_WEIGHTS = {
    "order":             10,  # 付费订单（最强关注信号）
    "course_behavior":    3,  # 课程浏览（主动进入）
    "ai_search_session":  1,  # AI 搜索 tags
    "community_daily":    1,  # 社区互动 tags
    "course_daily":       1,  # 课程日摘要 tags
    "order_weekly":       0,  # 订单周摘要 tags（已在订单本身计数，不重复）
}
```

**计算步骤**：
```python
counter = Counter()

# 1. 课程行为 → join 课程维表 → 提取学科（命中白名单才计入）
for cb in course_behaviors:
    subj = _resolve_subject(course_dim.get(cb["course_number"]))
    if subj:
        counter[subj] += _SUBJECT_WEIGHTS["course_behavior"]

# 2. 已付费订单 → join 课程维表 → 提取学科
for o in orders:
    if order_status == 2:
        subj = _resolve_subject(course_dim.get(o["course_number"]))
        if subj:
            counter[subj] += _SUBJECT_WEIGHTS["order"]

# 3. EventSummary tags → 过滤白名单
for s in event_summaries:
    weight = _SUBJECT_WEIGHTS.get(s.source_type, 1)
    for tag in s.tags:
        if tag in _SUBJECT_KEYWORDS:
            counter[tag] += weight

# 4. 归一化 → Top 5
total = sum(counter.values())
top5 = counter.most_common(5)
return {subj: round(cnt / total, 2) for subj, cnt in top5}
```

**_resolve_subject 优先级**（过滤内部产品名）：
```python
def _resolve_subject(info: Optional[dict]) -> Optional[str]:
    # 按优先级尝试各字段
    for field in ("subject_name_lvl3", "app_subject_name_lv1", "subject_name_lvl2"):
        val = str(info.get(field) or "").strip()
        if field == "app_subject_name_lv1":
            # 可能是逗号分隔，逐段匹配
            for part in val.split(","):
                part = part.strip()
                if part and part in _SUBJECT_KEYWORDS:
                    return part
        elif val and val in _SUBJECT_KEYWORDS:
            return val
    return None
```

**学科白名单** `_SUBJECT_KEYWORDS`（仅列关键部分，完整见源码）：
```python
# K12 主科
{"数学", "语文", "英语", "物理", "化学", "生物", "历史", "地理", "政治", "道德与法治", "科学"}
# 学段
{"小学", "初中", "高中"}
# 专项
{"艺考", "竞赛", "奥数", "选科志愿", "中考", "高考"}
# 语言/出国
{"日语", "法语", "韩语", "德语", "雅思", "托福", "剑桥英语"}
# 职业/资格
{"考公", "考编", "公务员", "省考", "事业单位", "教资", "教师招考",
 "会计", "CPA", "财会考试", "法考", "医考", "医疗考试"}
# 家庭/规划
{"家庭教育", "升学规划", "志愿填报"}
```

---

## 八、FactMemory 抽取

**函数**：`fact_extractor.py::extract_fact_candidates(event_summaries, llm_client, user_key) → list[FactMemory]`

```python
# 1. 构造 user_prompt
user_prompt = "\n\n".join(
    f"[摘要{i}] ({s.source_type})\n{s.content}"
    for i, s in enumerate(event_summaries, 1)
)

# 2. LLM 调用（step_name="fact_extract"）
resp = llm_client.complete(system_prompt, user_prompt, step_name="fact_extract")

# 3. 解析输出（Tab 分隔）
for line in resp.content.splitlines():
    parts = line.split("\t")          # category, tag, statement
    if category in 5个合法值:
        results.append({...})

# 4. 时间戳 = 该批次 EventSummary 的最晚 window_end
batch_time = max(s.window_end for s in event_summaries)

# 5. 创建 FactMemory 对象
facts = [FactMemory(created_at=batch_time, updated_at=batch_time, ...) for c in candidates]
```

**LLM System Prompt 关键约束**：
- 5 个 category（preference/status/need/ability/trait）
- 有效内容：K12 学科、学习需求、考试目标、个人特质、成长需求等
- 无效内容：纯娱乐、无画像价值的偶发查询
- 输出格式严格：`category\ttag\tstatement`，每行一条，无表头
- 同一 tag 下多条信息合并为一条

**解析逻辑**：
```python
def parse_fact_candidates(raw_output: str) -> list[dict]:
    results = []
    for line in raw_output.strip().splitlines():
        line = line.strip().lstrip("- ").strip("`")
        if not line or line.startswith("category"):
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            category = parts[0].strip().lower()
            if category in ("preference", "status", "need", "ability", "trait"):
                results.append({
                    "category": category,
                    "tag": parts[1].strip(),
                    "statement": "\t".join(parts[2:]).strip(),  # statement 允许含 \t
                })
    return results
```

---

## 九、FactMemory Merge 协议

**函数**：`fact_merger.py::merge_fact_memories(candidates, existing, llm_client, user_key) → list[FactMemory]`

对每条 candidate 执行 `_merge_one()`，将结果 append 到 all_facts。

### 9.1 _merge_one 完整算法

```python
def _merge_one(candidate, all_facts, llm_client, user_key):
    # 1. 查找同组：同 category + tag + status=active
    same_group = [
        f for f in all_facts
        if f.status == "active"
        and f.category == candidate.category
        and f.tag == candidate.tag
    ]

    # 2. 规则前置1：完全相同文本 → SKIP（不调 LLM）
    for f in same_group:
        if f.statement.strip() == candidate.statement.strip():
            return MergeAction.SKIP, None

    # 3. 规则前置2：无同类记忆 → ADD（不调 LLM）
    if not same_group:
        return MergeAction.ADD, candidate.model_copy()

    # 4. LLM 判定（same_group 非空 + 内容不完全相同）
    user_prompt = fm_prompt.build_user_prompt(
        category=candidate.category,
        tag=candidate.tag,
        candidate_statement=candidate.statement,
        existing_statements=[f.statement for f in same_group],
    )
    resp = llm_client.complete(fm_prompt.SYSTEM_PROMPT, user_prompt, step_name="fact_merge")
    result = fm_prompt.parse_merge_result(resp.content)   # {"action": ..., "memo": ...}
    action = MergeAction(result["action"])

    # 5. 状态变更
    if action == MergeAction.SKIP:
        return action, None

    if action == MergeAction.ADD:
        return action, candidate.model_copy()

    if action in (MergeAction.MERGE, MergeAction.REPLACE):
        # 标记所有同组旧记忆为 inactive（LLM 基于全部同组内容生成新记忆）
        now = datetime.now()
        all_old_source_ids = []
        for old in same_group:
            old.status = "inactive"
            old.updated_at = now
            all_old_source_ids.extend(old.source_summary_ids)

        # 创建新记忆
        merged_statement = result.get("memo", "") or candidate.statement
        new_fact = FactMemory(
            user_key=user_key,
            category=candidate.category,
            tag=candidate.tag,
            statement=merged_statement,
            source_summary_ids=list(set(candidate.source_summary_ids + all_old_source_ids)),
            merged_from=same_group[0].id,       # 指向最早旧 fact（历史溯源用）
            created_at=candidate.created_at,    # 继承批次事件时间
            updated_at=candidate.created_at,
            expires_at=candidate.expires_at,    # 继承候选的 expires_at（已含 base TTL）
        )
        return action, new_fact
```

### 9.2 Merge LLM Prompt

**User Prompt 格式**：
```
## 记忆类别
{category}, {tag}

## 当前已有记忆
- 已有记忆1
- 已有记忆2

## 候选新记忆
{candidate_statement}

请判断如何处理这条候选记忆。
```

**System Prompt 关键约束**：
- ADD：新信息与已有完全无关
- SKIP：新信息已被已有完全覆盖
- MERGE：新信息是补充，合并为更完整记忆
- REPLACE：新信息与已有冲突（状态变化、偏好变化）
- 输出格式：`思考过程\n---\nACTION\tMEMO`
- trait 类型反映长期特征，优先 MERGE，REPLACE 应谨慎

**解析逻辑**：
```python
def parse_merge_result(raw_output: str) -> dict:
    lines = raw_output.strip().split("---")
    if len(lines) >= 2:
        action_part = lines[-1].strip()
        for line in action_part.splitlines():
            parts = line.strip().lstrip("- ").strip("`").split("\t", 1)
            action = parts[0].strip().upper()
            if action in ("ADD", "SKIP", "MERGE", "REPLACE"):
                memo = parts[1].strip() if len(parts) > 1 else ""
                return {"action": action, "memo": memo}
    # fallback：全文搜索 action 关键词
    upper = raw_output.upper()
    for action in ("REPLACE", "MERGE", "SKIP", "ADD"):
        if action in upper:
            # 尝试从匹配行中提取 memo
            ...
    return {"action": "SKIP", "memo": ""}
```

### 9.3 按周批次处理

```python
# build_user.py 中的调用方式
all_facts = []
weekly_batches = _split_summaries_by_week(event_summaries)

for week_label, batch in weekly_batches:
    if not batch:
        continue
    candidates = extract_fact_candidates(batch, llm_client, user_key=user_key)
    all_facts = merge_fact_memories(candidates, all_facts, llm_client, user_key=user_key)

# 分周逻辑
def _split_summaries_by_week(summaries) -> list[tuple[str, list]]:
    by_week = defaultdict(list)
    for s in summaries:
        year, week, _ = s.window_start.isocalendar()
        key = f"{year}-W{week:02d}"   # "2026-W03"
        by_week[key].append(s)
    return sorted(by_week.items())    # 按时间顺序处理
```

---

## 十、L3 兴趣画像生成

**函数**：`interest_extractor.py::extract_interest_profile(active_facts, event_summaries, llm_client, user_key, recent_weeks=4) → InterestProfile`

```python
# 1. 过滤 EventSummary：只保留最近 recent_weeks 周
latest = max(s.window_end for s in event_summaries)
cutoff = latest - timedelta(weeks=recent_weeks)
recent_summaries = [s for s in event_summaries if s.window_end >= cutoff]

# 2. 构造 user_prompt
facts_str = "\n".join(
    f"- [{f.category}:{f.tag}] {f.statement}"
    for f in active_facts
)
summaries_str = "\n".join(
    f"- ({s.source_type}) {s.content[:200]}"
    for s in recent_summaries
)

# 3. LLM 调用（step_name="interest_extract"）
resp = llm_client.complete(ie_prompt.SYSTEM_PROMPT, user_prompt, step_name="interest_extract")

# 4. 解析输出（严格 6 行 key: value 格式）
parsed = ie_prompt.parse_interest_profile(resp.content)
# 每行 "field_name: val1, val2, ..."，通过 partition(":") 解析

# 5. 补充 active_time_slots（规则统计，不走 LLM）
interest.active_time_slots = _calc_active_time_slots(history_events)
```

**活跃时段规则**（Top 3）：
```python
periods = {
    "早间(6-9点)":  range(6, 9),
    "上午(9-12点)": range(9, 12),
    "下午(12-18点)":range(12, 18),
    "晚间(18-22点)":range(18, 22),
    "深夜(22-6点)": list(range(22, 24)) + list(range(0, 6)),
}
# 统计各时段行为总数，取前3
```

**LLM System Prompt 关键约束**：
- 严格输出 6 行，不加任何 Markdown / 标题 / 多余空行
- 格式：`字段名: 值1, 值2`（purchase_intent 为短句，其余逗号分隔）
- 没有足够证据的字段输出"未知"
- personality_traits 来自 trait 类型的"学习性格""家庭角色""学习风格"等 fact
- growth_needs 来自 trait 类型的"成长需求""行为习惯"等 fact（非学科能力）

**解析逻辑**：
```python
def parse_interest_profile(raw_output: str) -> dict:
    result = {field: [] or None for field in 6个字段}
    for line in raw_output.strip().splitlines():
        key, _, value = line.partition(":")
        key = key.strip().lower().replace(" ", "_")
        value = value.strip()
        if key in ("interest_subjects", "interest_directions", "focus_areas",
                   "personality_traits", "growth_needs"):
            # 逗号/中文逗号分割，过滤"未知"
            result[key] = [t.strip() for t in value.replace("，",",").split(",")
                           if t.strip() and t.strip() != "未知"]
        elif key == "purchase_intent":
            result[key] = value if value and value != "未知" else None
    return result
```

---

## 十一、LLM 调用管理

### 11.1 缓存机制

**Key 生成**：
```python
def _cache_key(model: str, system_prompt: str, user_prompt: str) -> str:
    raw = f"{model}\n---SYS---\n{system_prompt}\n---USR---\n{user_prompt}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

**缓存文件格式**（`data/llm_cache/{key}.json`）：
```json
{
  "content": "LLM 返回内容",
  "input_tokens": 1000,
  "output_tokens": 200,
  "total_tokens": 1200,
  "model": "doubao/doubao-seed-1-6-lite-251015"
}
```

**Prompt 变更自动失效**：key 基于 model + system_prompt + user_prompt 的 hash，任何变更都会命中新 key。

### 11.2 API 调用与重试

**请求格式**（OpenAI 兼容）：
```python
payload = {
    "model": self.model,
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
    "temperature": 0.2,     # 降低随机性
    "max_tokens": 4096,
}
```

**重试策略**：
- max_retries=2（共3次尝试）
- 退避：`sleep_s = 2.0 * (attempt + 1)`（第1次2s，第2次4s）
- 可重试：网络异常（requests.RequestException）、5xx/408/429
- 不重试：内容风控（400 + 敏感词）、其他客户端错误

**错误分类**：
```python
# 内容风控判定：status_code==400 + 文本含以下关键词之一
("inappropriate content", "content filter", "moderation", "unsafe",
 "sensitive", "不适宜", "敏感", "违规")

# 可重试 HTTP 状态码
(408, 429, 500, 502, 503, 504)
```

### 11.3 Token 追踪

**TokenTracker.get_summary() 返回**：
```python
{
    "total_tokens": int,          # 含缓存（等效消耗规模，反映真实成本）
    "total_input_tokens": int,
    "total_output_tokens": int,
    "api_tokens": int,            # 仅非缓存（本次运行实际 API 费用）
    "api_input_tokens": int,
    "api_output_tokens": int,
    "actual_calls": int,
    "cached_calls": int,
    "by_step": {
        "event_summary": {"calls": int, "cached_calls": int, "tokens": int},
        "fact_extract": {...},
        "fact_merge": {...},
        "interest_extract": {...},
        "community_summary": {...},
        "course_summary": {...},
    }
}
```

**record 格式**（每次调用）：
```python
{
    "user_key": str,
    "step": str,
    "object_id": str,
    "model": str,
    "input_tokens": int,
    "output_tokens": int,
    "total_tokens": int,
    "latency_ms": int,
    "cached": bool,
}
```

---

## 十二、数据资产

### 12.1 原始数据（data/original_output/）

| 表 | 记录数 | 时间范围 | 用途 |
|---|---:|---|---|
| 用户特征宽表 | 24,446 × 88+ 字段 | dt=20260318 快照 | L1/L2 画像底板 |
| AI 搜索记录 | 400,431 条 | 2025-02 ~ 2026-03 | LLM 摘要主输入 |
| 课程行为 | 3,222,414 条 | 近 91 天 | 有效 11.6%（含 course_id） |
| 订单历史 | 1,248,432 笔 | 2017 ~ 2026 全量 | 消费画像 |
| 动态交互 | 5,621,869 条 | 近 91 天 | 有效互动约 1.7% |
| 动态内容宽表 | 59,919 条 | 近 91 天 | 有效互动动态内容 |
| 评论互动 | 177,844,530 条 | 近 91 天 | 不参与 pipeline |
| 课程维表 | 1,035,669 门 | dt=20260318 快照 | 课程名/学科/年级 |
| 科目偏好 | 53,992 条 | dt=20260318 快照 | L1 学科偏好 |

### 12.2 预处理数据集（data/seed_output/）

50 个种子用户过滤后的子集（~155 MB），课程维表全量保留用于 join。当前聚焦 5 个种子用户构建（`data/seed_user_keys.json`），其中 2 个有服务通话数据、3 个无。

### 12.5 服务通话记录（refer/*/服务通话记录原表/）

通过 `extract_call_records_raw_spark_job.py` 从集群抽取，存放在 `refer/{timestamp}/服务通话记录原表/`。
`ParquetAdapter` 自动发现最新目录（按时间戳字典序取最大值），不需要硬编码路径。

### 12.3 Demo 推送数据集（data/demo_seed/，约 37 MB，入库）

通过 `prepare_demo_data.py` 生成：
- 课程维表：过滤到种子用户实际使用的课程（1,035,669 → 135,455 行）
- 评论互动：跳过（pipeline 未使用）
- 其余：直接复制 seed_output

### 12.4 ParquetAdapter 数据目录优先级

```python
for candidate in ["data/seed_output", "data/demo_seed", "data/original_output"]:
    if Path(candidate).exists():
        data_dir = candidate
        break
```

---

## 十三、代码结构

```
src/
  core/
    schema/
      profile.py          L1/L2/L3 画像 Schema
      memory.py           HistoryEvent / EventSummary / FactMemory / MergeAction
      bundle.py           DemoUserBundle / TraceItem
    pipeline/
      static_extractor.py L1 规则抽取
      ability_extractor.py L2 规则统计（含 subject_engagement）
      history_builder.py   原始数据 → HistoryEvent（含过滤）
      event_summarizer.py  HistoryEvent → EventSummary（LLM + 规则）
      fact_extractor.py    EventSummary → 候选 FactMemory（LLM）
      fact_merger.py       候选 + 已有 → merge 决策（LLM + 规则前置）
      interest_extractor.py 最终态 → L3 兴趣画像（LLM）
    prompts/
      event_summary.py     AI 搜索 session 摘要 prompt
      community_summary.py 社区互动日摘要 prompt
      course_summary.py    课程浏览日摘要 prompt
      fact_extract.py      FactMemory 候选抽取 prompt + 解析
      fact_merge.py        FactMemory merge 判定 prompt + 解析
      interest_extract.py  L3 兴趣画像 prompt + 解析
    data_adapter/
      base.py              DataAdapter ABC
      parquet_adapter.py   V1 parquet 实现（带内存缓存）
    llm/
      client.py            LLMClient + TokenTracker + LLMResponse + LLMAPIError
  tools/
    build_data/
      extract_spark_job.py               主流水线 Spark 抽取（10张表）
      extract_userprofile_data.py        主流水线本地执行入口
      extract_moment_features_spark_job.py 动态内容宽表 Spark 抽取
      extract_moment_features_data.py    动态内容本地执行入口
      extract_call_records_raw_spark_job.py 服务通话 Spark 抽取（含桥接表）
      extract_call_records_raw_data.py   服务通话本地执行入口
      extract_call_records_spark_job.py  服务通话简化版（无桥接，已弃用）
      extract_call_records_data.py       简化版本地入口（已弃用）
  build/
    build_user.py          单用户完整 pipeline（9步顺序执行）
    build_all.py           批量构建（支持 --user-key / --limit）
  demo/
    app.py                 Streamlit 展示（纯读取 JSON，含数据来源展示）
  scripts/
    select_seed_users.py   种子用户筛选
    preprocess_seed_data.py 种子数据预处理
    prepare_demo_data.py   Demo 推送数据集生成
```

---

## 十四、关键阈值汇总

| 配置项 | 值 | 位置 |
|---|---|---|
| LLM temperature | 0.2 | client.py |
| LLM max_tokens | 4096 | client.py |
| LLM timeout | 120s | client.py |
| LLM max_retries | 2（共3次） | client.py |
| 重试退避 | 2.0s × (attempt+1) | client.py |
| 缓存 key 长度 | SHA256 前16位 | client.py |
| 课程浏览 LLM 最多条数 | 15 门 | event_summarizer.py |
| 社区互动 LLM 最多条数 | 20 条 | event_summarizer.py |
| FactMemory 同组比较上限 | ≤20 条 | memory.py 注释 |
| interest_extract 时间窗口 | 最近4周 | interest_extractor.py |
| subject_engagement Top N | 5 | ability_extractor.py |
| subject_engagement 精度 | 小数点后2位 | ability_extractor.py |
| active_time_slots Top N | 3 | build_user.py |
| payment_level 分桶 | 500 / 5000 | ability_extractor.py |
| activity_intensity 分桶 | 1 / 5 / 20 | ability_extractor.py |
| course_engagement 分桶 | 1 / 50 / 500 | ability_extractor.py |
| ai_usage_intensity 分桶 | 1 / 20 / 100 | ability_extractor.py |

---

## 十五、运行命令

```bash
# 1. 环境
pip install -r requirements.txt
cp .env.example .env   # 填写 MODEL_NAME / API_BASE_URL / API_KEY

# 2. 数据准备
python -m src.scripts.preprocess_seed_data          # 种子数据预处理
python -m src.scripts.prepare_demo_data             # 生成 demo 推送数据集

# 3. 构建
python -m src.build.build_all --user-key 3740128    # 单用户
python -m src.build.build_all --limit 5             # 前5个
python -m src.build.build_all                       # 全部种子用户（当前5个）

# 5.5 服务通话数据抽取（集群端）
python src/tools/build_data/extract_call_records_raw_data.py

# 4. 展示
streamlit run src/demo/app.py

# 5. Git
git -C /path/to/userprofile merge realize-claude   # 同步稳定分支
git push origin realize-claude                     # 推开发分支
git push origin userprofile-v1 --force             # 推稳定分支（squash历史）
git push baijia userprofile-v1:main                # 推内网 GitLab
```

---

## 十六、V1 边界与规划

> 详细规划见 `doc/ROADMAP.md`

### 已实现

- 三层画像完整链路（L1 规则 + L2 统计 + L3 LLM）
- 三层记忆完整链路（HistoryEvent → EventSummary → FactMemory）
- 社区互动内容感知摘要（动态内容宽表，100% 覆盖率）
- 课程行为内容感知摘要（课程维表，98.7% 覆盖率）
- 订单 EventSummary 关联课程名/学科/年级（course_dim join）
- FactMemory merge 协议（ADD/SKIP/MERGE/REPLACE + inactive 演化历史）
- FactMemory merge 正确 inactive 全部同组旧记录（而非仅第一条）
- FactMemory 时间戳来自事件窗口（而非 pipeline 运行时间）
- 个人特质抽取（5 个 category，InterestProfile 加 personality_traits/growth_needs）
- 学科关注强度关键词过滤（_resolve_subject + _SUBJECT_KEYWORDS 白名单）
- L3 EventSummary 输入截断到最近 4 周（减少 token 消耗）
- Trace 预计耗时（estimated_duration_ms，反映无缓存时的真实构建时长）
- Token 等效消耗追踪（区分等效 total 与实际 api token）
- LLM 调用缓存 + 重试 + 内容风控处理
- 构建/展示分离（DemoUserBundle JSON）
- Demo 数据集生成脚本（prepare_demo_data.py，~37 MB 可推送）
- **FactMemory 固定 tag 分类（V1，32 个）**：基于 5 个种子用户 995 条 FactMemory 设计，覆盖率 90%+
- **EventSummary 并行化**：AI 搜索 + 社区互动改为 ThreadPoolExecutor(max_workers=4)，4倍加速
- **服务通话记录接入**：ASR 表通过 IM 消息桥接表打通 user_key，规则拼装 EventSummary，22 个结构化字段
- **FactMemory merge 机制优化**：规则前置（完全相同 SKIP / 无同类 ADD）省 LLM 调用；MERGE/REPLACE 继承 expires_at

### 已知局限

| 问题 | 影响 | 计划解法 |
|---|---|---|
| 离线 JSON，无在线 API | 无法接入在线服务 | 服务化 |
| 课程行为仅保留 11.6% | 学科信号不足 | 数据层补充 course_number |
| 服务通话仅覆盖 EM 素养业务线 | 9/50 种子用户有数据 | 拓展其他业务线数据源 |
| `merged_from` 单值 | 仅指最早旧 fact，无法完整追溯 | 扩展为 list |

### 下游接入规划：AI 搜索服务

userprofile 处理的"AI 搜索记录"就是 AI 搜索服务产生的对话历史，两个系统共用同一份数据源。计划将 userprofile 构建产物反哺给 AI 搜索，提升搜索个性化能力。

**三种数据的接入方式**：

| 数据 | 接入方式 | 说明 |
|---|---|---|
| FactMemory | 直接注入 system context | 长期记忆，量少信息密，适合全量注入 |
| Profile（L1/L2/L3） | 直接注入 system context | 结构化标签，直接描述用户画像 |
| EventSummary | RAG 按需召回 | 量大，不全量注入；当用户问题涉及历史对话时，用当前 query 检索相关 EventSummary，召回对应 session 内容补充上下文 |

**前置条件**：userprofile 需提供在线查询 API（当前为离线 JSON）+ EventSummary 预先 embedding 入向量库。

> **为什么不需要 EventSummary 衰减治理**：线上架构下，老 EventSummary 已在上一次构建时被提炼进 FactMemory，不再参与 fact 抽取。FactMemory 的 REPLACE/MERGE 协议本身就是信息衰减机制（用户情况变化时新 fact 替代旧 fact）。老 EventSummary 唯一的角色是 RAG 召回，而相关性检索天然具有筛选效果，不需要额外的时间衰减策略。

### 预留接口（V1 未实现）

- FactMemory 按天批次触发（V1 为按周）
- FactMemory embedding 检索（active > 100 条时）
- 做题记录 → subject_ability / quiz_accuracy / knowledge_mastery
- 课程讲师逐字稿 → 工具型 LLM 特征提取
- API 服务化 / Kafka 事件总线

### 扩展新数据来源（三步接入）

```
1. data_adapter/base.py  → 加 get_xxx() 方法
2. history_builder.py    → 加结构化转换规则 + 过滤条件
3. event_summarizer.py   → 加聚合策略（规则 or LLM）+ 对应 prompt 文件
```
