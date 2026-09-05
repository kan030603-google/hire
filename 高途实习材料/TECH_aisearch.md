# AI Search Pipeline 技术文档

> 教育场景智能搜索与意图识别系统 —— 算法核心完整说明
>
> 本文档覆盖所有技术细节，单独阅读即可理解整个系统的原理、实现和设计决策。

---

## 目录

1. [项目背景与问题定义](#1-项目背景与问题定义)
2. [整体架构](#2-整体架构)
3. [模块详解：预处理器（QueryProcessor）](#3-模块详解预处理器queryprocessor)
4. [模块详解：实体识别（EvidenceCollector）](#4-模块详解实体识别evidencecollector)
5. [模块详解：意图分类（三级降级架构）](#5-模块详解意图分类三级降级架构)
6. [模块详解：实体萃取与意图覆盖（ResponseBuilder）](#6-模块详解实体萃取与意图覆盖responsebuilder)
7. [模块详解：意图路由与向量检索（IntentBasedRetriever）](#7-模块详解意图路由与向量检索intentbasedretriever)
8. [模块详解：LLM 调用链](#8-模块详解llm-调用链)
9. [模块详解：会话管理（SessionManager）](#9-模块详解会话管理sessionmanager)
10. [数据结构全览](#10-数据结构全览)
11. [Prompt 设计详解](#11-prompt-设计详解)
12. [BERT 训练指南](#12-bert-训练指南)
13. [配置与运行](#13-配置与运行)
14. [扩展指南](#14-扩展指南)
15. [关键设计决策](#15-关键设计决策)

---

## 1. 项目背景与问题定义

### 1.1 业务场景

高途课堂 App 内的搜索框，用户会输入各种形式的查询：

| 用户输入 | 真实意图 | 难点 |
|---------|---------|------|
| 白马老师有课吗 | 找老师"褚佳麟" | 用了别名"白马老师" |
| baima laoshi | 找老师"褚佳麟" | 拼音输入 |
| 考研数学怎么学 | 知识解答 | 无实体，需要LLM |
| AI闪学在哪里 | 产品功能查询 | 功能名有模糊匹配 |
| 他有没有网课 | 找上一轮说的老师的课程 | 指代消解，依赖上下文 |

**核心问题**：如何准确识别用户意图并返回正确结果？

### 1.2 传统方法的局限

- **纯关键词匹配**：无法处理别名、拼音、繁体等变体
- **纯向量搜索**：召回率高但精确度不足，无法区分"找老师"和"找课程"
- **纯 LLM**：延迟高（7-9s），成本贵，不适合每次请求都调用

### 1.3 本系统的解法

**两阶段处理 + 本地优先**：
1. 先本地快速处理（预处理 + 实体识别，< 5ms）
2. 再按证据质量决定是否调用 LLM 或向量库

---

## 2. 整体架构

### 2.1 六步流水线

```
用户原始查询: "baima 老师有哪些课程"
        │
        ▼
┌─────────────────────────────────────────────────────┐
│  步骤1  QueryProcessor：NLP 预处理                   │
│  输入: "baima 老师有哪些课程"                         │
│  输出: "白马老师有哪些课程"  tokens: [白马老师, 课程] │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│  步骤2  EvidenceCollector：实体识别（证据收集）       │
│  三段匹配: 精确 → Token → 模糊                       │
│  输出: Evidence(value=褚佳麟, type=teacher, score=1) │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│  步骤3  意图分类（三级降级）                          │
│  BERT (~10ms) → 轻量LLM (~300ms) → 规则兜底         │
│  输出: Intent.COURSE_QUERY (课程查询类, code=101)    │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│  步骤3.5  ResponseBuilder：实体萃取 + 意图覆盖       │
│  萃取: 褚佳麟(teacher, role=filter)                  │
│  覆盖规则检查: 无需覆盖                               │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│  步骤4  IntentBasedRetriever：路由 + 向量检索        │
│  Filter: intent_word in ("课程查询类")               │
│          and main_teacher_name="褚佳麟"             │
│  混合检索: 向量(0.5) + BM25稀疏(0.5)                │
│  加权: 实体匹配 × 3.0, 上下文 × 1.5, 画像 × 1.2    │
└─────────────────────┬───────────────────────────────┘
                      │
              检索类意图直接返回
              知识解答类继续步骤5
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│  步骤5  LLM 回答生成（仅知识解答类/对话类）           │
│  Prompt = 意图 + 证据 + 上下文 + 历史               │
│  调用: MultimodalLLMClient → OpenAI兼容接口         │
│  输出: {reasoning, answer}                          │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│  步骤6  SessionManager：会话历史更新                 │
│  存储本轮: query + intent + answer + entities        │
│  更新上下文: current_teacher = 褚佳麟               │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
返回: {intent: 101, intent_name: "课程查询类",
       entities: [{褚佳麟, filter}], search_results: [...]}
```

### 2.2 目录结构

```
algorithm_demo/              ← 可独立移植的文件夹
├── pipeline_demo.py         ← 主入口，演示完整 pipeline
├── TECH_DOC.md              ← 本文档
├── core/                    ← 算法源代码
│   ├── preprocessor/        ← 步骤1: NLP 预处理
│   │   ├── processor.py     ← 统一入口 QueryProcessor
│   │   ├── tokenizer.py     ← jieba 分词 + TF-IDF 权重
│   │   ├── traditional_converter.py  ← 繁→简（OpenCC）
│   │   ├── pinyin_converter.py       ← 拼音→汉字
│   │   ├── whitespace_cleaner.py     ← 空白清理
│   │   ├── width_normalizer.py       ← 全角→半角
│   │   └── data/
│   │       ├── word.txt     ← jieba 自定义词典
│   │       └── stopwords.txt ← 停用词表
│   ├── prober/              ← 步骤2: 实体识别
│   │   ├── evidence.py      ← Evidence 数据结构
│   │   ├── evidence_collector.py ← 统一入口 EvidenceCollector
│   │   ├── keyword_prober.py     ← FlashText 关键词匹配
│   │   ├── semantic_prober.py    ← 本地 FAISS 语义检索（可选）
│   │   ├── tencent_vector_prober.py ← 腾讯云向量检索
│   │   └── data/
│   │       ├── entities.csv ← 实体词典（799 条）
│   │       └── teachers.json ← 教师详细信息
│   ├── intent/              ← 步骤3: 意图分类
│   │   ├── intent_types.py  ← Intent 枚举 + 编码映射
│   │   ├── bert_classifier.py ← BERT 本地分类器
│   │   └── bert_training/   ← BERT 训练链路（数据生成/训练/合并）
│   ├── llm_client/          ← 步骤5: LLM 调用
│   │   ├── intent_classifier.py       ← 高层封装
│   │   ├── multimodal_llm_client.py   ← HTTP 客户端
│   │   ├── prompt_template_v2.py      ← 完整意图+回答 Prompt
│   │   ├── prompt_template_answer.py  ← 纯回答生成 Prompt
│   │   └── prompt_template_intent_only.py ← 轻量意图 Prompt
│   ├── retriever/           ← 步骤4: 意图路由+检索
│   │   └── intent_based_retriever.py
│   ├── response_builder.py  ← 步骤3.5: 实体萃取+意图覆盖
│   └── session_manager.py   ← 步骤6: 会话管理
└── bert_training_ref/       ← BERT 训练参考（兼容旧路径）
    ├── README_BERT.md
    ├── train.py
    ├── dataset.py
    ├── generate_training_data.py
    └── data/                ← 训练数据集
```

### 2.3 这个 demo 与原大项目的关系

这个目录不是完整服务，而是把原项目中和算法主链直接相关的部分抽出来做了去基础设施化处理。

保留下来的核心：
- 查询预处理、实体词典匹配、意图识别、意图覆盖、检索路由、LLM 调用封装、会话管理、BERT 训练链路
- 腾讯云向量检索接口形态、OpenAI 兼容 LLM 接口形态

刻意移除或弱化的部分：
- Django 服务层、Apollo 配置中心、Eureka、SkyWalking、数据库治理、部署脚本
- 与业务系统强绑定的上下游编排

因此，这个 demo 的定位应理解为：
- **适合学习算法链路和模块边界**
- **不等于完整生产服务**
- **可以独立运行，但部分能力依赖外部模型/API/向量库时会自动降级**

---

## 3. 模块详解：预处理器（QueryProcessor）

### 3.1 职责与设计

**文件**：`core/preprocessor/processor.py`

**职责**：将用户原始输入标准化为干净的查询文本和分词序列，消除各种输入噪音。

**设计原则**：各功能独立文件，职责单一，`QueryProcessor` 作为统一入口按顺序调度。

### 3.2 处理流水线（5步）

```python
# 处理顺序固定，每步都会改变文本
query → 全角转半角 → 繁→简 → 拼音→汉字 → 空白清理 → 分词
```

#### 步骤 A：全角转半角（WidthNormalizer）

**文件**：`core/preprocessor/width_normalizer.py`

**问题**：中文输入法常产生全角字符，如 `Ａ`（U+FF21）≠ `A`（U+0041）

**算法**：Unicode 码位转换，全角字符范围 `U+FF01~U+FF5E` 统一减去 `0xFEE0`

```python
# 核心逻辑
for char in text:
    code = ord(char)
    if 0xFF01 <= code <= 0xFF5E:
        result += chr(code - 0xFEE0)  # 转半角
    elif code == 0x3000:              # 全角空格
        result += ' '
    else:
        result += char
```

**示例**：`"ＡＩ闪学"` → `"AI闪学"`

#### 步骤 B：繁体转简体（TraditionalConverter）

**文件**：`core/preprocessor/traditional_converter.py`

**依赖**：`opencc-python-reimplemented`（OpenCC 的 Python 绑定）

**配置**：`t2s`（Traditional to Simplified，台湾繁体转大陆简体）

```python
converter = opencc.OpenCC('t2s')
result = converter.convert(text)
```

**示例**：`"老師的課程"` → `"老师的课程"`

**为什么需要**：港台用户会用繁体输入，entity 词典存的是简体。

#### 步骤 C：拼音转汉字（PinyinConverter）

**文件**：`core/preprocessor/pinyin_converter.py`

**问题**：用户直接输入拼音，如 `baima laoshi` 应转为 `白马老师`

**算法**：
1. 启动时从 `entities.csv` 加载所有实体名，生成拼音映射表
2. 使用 `pypinyin` 库将每个实体名转成无调拼音
3. 为每个实体生成多种拼音变体：连写、空格分隔、首字母大写、全大写、下划线分隔
4. 预编译正则表达式，按拼音长度降序匹配，优先替换长串，避免 `白马老师` 被拆成 `白马` + `老师`

```python
# 初始化时：构建拼音映射
from pypinyin import lazy_pinyin
hanzi = "白马老师"
pinyin_list = lazy_pinyin(hanzi)  # ["bai", "ma", "lao", "shi"]

variants = [
    "".join(pinyin_list),          # baimalaoshi
    " ".join(pinyin_list),         # bai ma lao shi
    "".join(p.capitalize() for p in pinyin_list),  # BaiMaLaoShi
    "".join(pinyin_list).upper(),  # BAIMALAOSHI
]

# 查询时：按长度降序做 regex 替换
pattern = re.compile(r'(?<![a-zA-Z_])baimalaoshi(?![a-zA-Z_])', re.IGNORECASE)
text = pattern.sub("白马老师", text)
```

**工程边界**：
- 只有在 `entities.csv` 中存在的实体名才会被转换，避免误转普通词语
- `pypinyin` 是可选增强依赖；缺失时该步骤会自动禁用，但不会阻断整个 pipeline

#### 步骤 D：空白字符清理（WhitespaceCleaner）

**文件**：`core/preprocessor/whitespace_cleaner.py`

**处理**：
- 去除首尾空白
- 合并连续空格为单个空格
- 过滤特殊控制字符（`\t`, `\r`, `\n`等）

#### 步骤 E：中文分词（Tokenizer）

**文件**：`core/preprocessor/tokenizer.py`

**依赖**：`jieba` 分词库

**三种模式**：

```python
# 1. 普通分词（返回 List[str]）
tokens = tokenizer.tokenize("我想找白马老师")
# → ["想", "找", "白马老师"]

# 2. 多粒度分词（粗粒度 + 细粒度）
result = tokenizer.tokenize_multi_granularity("考研数学冲刺班")
# → {"coarse": ["考研数学", "冲刺班"], "fine": ["考研", "数学", "冲刺", "班"]}

# 3. 带 TF-IDF 权重分词
tokens_weight = tokenizer.tokenize_with_weight("白马老师考研数学")
# → [("白马老师", 0.85), ("考研数学", 0.72)]
```

**自定义词典** (`data/word.txt`)：注册专有名词，防止 jieba 切错：
```
白马老师    # 强制不拆分
AI闪学      # 强制不拆分
考研数学    # 强制不拆分
```

**TF-IDF 权重计算**：
- 词频（TF）= 词在查询中出现次数 / 总词数
- 逆文档频率（IDF）：从预训练词典读取，专有名词 IDF 更高
- 越罕见的词权重越高，避免"的"、"是"等高频词干扰

---

## 4. 模块详解：实体识别（EvidenceCollector）

### 4.1 核心概念：Evidence

**文件**：`core/prober/evidence.py`

```python
@dataclass(frozen=True)
class Evidence:
    source: str         # 来源: "keyword_exact", "keyword_token", "keyword_fuzzy", "tencent_vector"
    value: str          # 实体值: "褚佳麟"
    type: str           # 实体类型: "teacher", "course", "subject", "feature", "grade", "exam_type"
    score: float        # 匹配置信度: 0.0~1.0
    matched_text: str   # 在原始查询中命中的文本: "白马老师"
    entity_id: str      # 实体在词典中的唯一 ID: "T001"
    url: str            # 可选的跳转链接
```

**为什么用 frozen dataclass**：证据创建后不可修改（不变量），防止误修改，可哈希用于集合去重。

### 4.2 实体词典（entities.csv）

```csv
entity_id,entity_value,entity_type,aliases
T001,褚佳麟,teacher,白马老师;褚老师;白马;jialin
T002,王浩然,teacher,浩然老师;王老师
C001,考研数学强化班,course,数学强化;数学强化班
F001,AI试炼场,feature,AI试炼;试炼场;AI闪学
G001,高三,grade,高中三年级
S001,数学,subject,math;数学科目
```

字段说明：
- `entity_id`：全局唯一 ID，格式 `T`=teacher, `C`=course, `F`=feature, `G`=grade, `S`=subject
- `entity_value`：标准名称（存入向量库和返回结果时使用）
- `entity_type`：类型枚举，影响意图路由和 filter 构建
- `aliases`：分号分隔的别名列表，用于精确和 Token 匹配

### 4.3 KeywordProber：三段式匹配

**文件**：`core/prober/keyword_prober.py`

**核心技术**：FlashText 算法（Aho-Corasick 的改进版，O(n) 时间复杂度多模式匹配）

#### 第一段：精确匹配（score = 1.0）

使用 FlashText `KeywordProcessor`，将所有 `entity_value` 和 `aliases` 注册为关键词：

```python
keyword_processor = KeywordProcessor(case_sensitive=False)
# 注册格式: keyword_processor.add_keyword("白马老师", "褚佳麟")
# 匹配时返回: {"褚佳麟": ["白马老师"]}  # 找到了哪个关键词对应哪个实体值
```

**原理**：FlashText 在内部构建 Trie 树 + 失败指针，一次遍历文本即可找到所有关键词，时间复杂度 O(n)，远快于 n 个正则表达式 O(n×m)。

**示例**：
```
输入: "我想找白马老师的考研数学课"
FlashText 找到: "白马老师" → 褚佳麟(teacher), "考研数学" → 暂无
```

#### 第二段：Token 层匹配（score = 0.6 × token_weight）

对分词后的每个 token 单独做精确匹配，捕获分词后才能识别的实体。

**示例**：
```
原始: "褚佳麟的课程"
分词: ["褚佳麟", "的", "课程"]
Token 精确匹配: "褚佳麟" → 命中 entity T001
score = 0.6 × idf_weight("褚佳麟")
```

**为什么 score = 0.6**：Token 匹配比全文精确匹配置信度低，因为单个 token 可能有歧义。

#### 第三段：模糊匹配（RapidFuzz 回退）

只有当前两段都没有命中时，才用 `rapidfuzz` 做相似度回退，处理拼写错误和近似词：

```python
from rapidfuzz import fuzz
for candidate in all_entity_names:
    score = fuzz.partial_ratio(query, candidate)
    if score >= fuzzy_threshold:  # 默认 80
        yield Evidence(source="keyword_fuzzy", score=score / 100.0, ...)
```

**实现约束**：
- 模糊匹配只在“精确匹配和 Token 匹配都为空”时触发，不会和高置信度实体混用
- `rapidfuzz` 也是可选增强依赖；缺失时仅关闭模糊回退，不影响精确匹配主链

**为什么分三段**：
- 精确匹配：0 误报，速度快，优先返回
- Token 匹配：处理词序不同、长查询中的局部匹配
- 模糊匹配：兜底处理拼写错误，但置信度低，不参与实体萃取

### 4.4 EvidenceCollector 的编排与回退策略

`EvidenceCollector.collect()` 不是简单地把多个探测器串起来，而是做了明确的编排约束：

1. **本地关键词匹配永远先跑**
   - 它快、确定性强、对 alias 识别最可靠

2. **上下文实体不直接拼接进 query**
   - 这是一个很重要的设计决策
   - 如果把 `current_teacher=褚佳麟` 直接拼到当前 query，FlashText 会产生假阳性，导致“当前轮没提老师却像提了老师”
   - 正确做法是：在 `response_builder` 阶段把上下文实体作为 `origin="context"` 的结构化实体单独注入

3. **意图识别前的云检索默认关闭**
   - `enable_pre_intent_cloud_search=False`
   - 也就是说，默认情况下证据收集阶段不会先打云向量库

4. **本地语义检索只作为回退**
   - 满足以下任一条件才运行：
     - 云检索前置功能关闭
     - 腾讯云向量检索未配置
     - 云检索执行了但没有结果

5. **证据最终统一按 `(type, value)` 去重**
   - 保留分数最高的那条，避免同一实体被本地关键词和语义检索重复注入

**这一层的真实定位**：
- 本地关键词匹配是主证据来源
- 云向量检索是生产形态的语义召回
- 本地语义检索是 demo/离线场景下的补充回退，不应和生产云检索等价理解

### 4.5 TencentVectorProber：云端混合检索

**文件**：`core/prober/tencent_vector_prober.py`

在意图识别后，用于召回相关内容。使用腾讯云 VectorDB 的混合检索 API：

```python
result = client.hybrid_search(
    database_name="ai-search",
    collection_name="search-0224",
    ann=[AnnSearch(field_name="vector", data=[query_embedding], limit=200)],   # 稠密向量检索
    match=[KeywordSearch(field_name="sparse_vector", data=bm25_vector, limit=200)],  # BM25稀疏检索
    rerank=WeightedRerank(
        field_list=["vector", "sparse_vector"],
        weight=[0.5, 0.5]   # 各占50%权重，可通过Apollo调整
    ),
    limit=50
)
```

**稠密向量**：用 Embedding 模型将查询和文档映射到同一高维空间，捕获语义相似性。
**BM25稀疏向量**：基于词频-逆文档频率的传统检索，捕获关键词精确匹配。
**混合检索优势**：语义理解 + 关键词精确度，互补覆盖两种类型的查询。

### 4.6 SemanticProber：本地语义回退的实现现状

**文件**：`core/prober/semantic_prober.py`

这部分文档必须区分“设计目标”和“当前 demo 实现”，不能混写。

**设计目标**：
- 使用 `sentence-transformers` 生成句向量
- 用 FAISS 做本地近邻检索
- 在离线环境下提供一个不依赖腾讯云 VectorDB 的语义回退链路

**当前 demo 实现现状**：
- 代码仍保留了 `SentenceTransformer` 和 `faiss` 依赖接口
- 但当前 `load_documents()` / `probe()` 实际走的是 `_simple_encode()` 轻量向量化
- 因此它更接近“离线回退/实验性语义召回”，而不是生产级语义检索

这意味着两个工程结论：
- 你可以把它讲成“本地语义回退链路”
- 但不能把它讲成“与云端语义检索同等级的稳定主路径”

### 4.7 证据融合

```python
# 按 score 降序排列
evidence.sort(key=lambda x: x.score, reverse=True)

# 按 (type, value) 去重：保留分数最高的那条
seen = set()
for ev in evidence:
    key = (ev.type, ev.value)
    if key not in seen:
        seen.add(key)
        merged.append(ev)
```

**去重原因**：同一实体可能被精确匹配和向量检索都命中，只保留置信度高的。

---

## 5. 模块详解：意图分类（三级降级架构）

### 5.1 意图体系

**文件**：`core/intent/intent_types.py`

```python
class Intent(Enum):
    # 检索类（直接返回向量库结果）
    LOCAL_RETRIEVAL       = "检索匹配类"   # code=1  (通用)
    COURSE_QUERY          = "课程查询类"   # code=101
    TEACHER_QUERY         = "老师查询类"   # code=102
    PRODUCT_FEATURE_QUERY = "产品功能查询类"  # code=103

    # 生成类（需要 LLM 生成回答）
    KNOWLEDGE_QA          = "知识解答类"   # code=2
    CONVERSATIONAL        = "对话类"      # code=3

    # 其他
    OTHER                 = "其他类"      # code=4
    AMBIGUOUS             = "意图不明"    # code=-1
```

**设计分层**：
- code 1xx：检索类的细分，向量库按不同 `intent_word` 过滤
- code 2,3：需要 LLM 生成文字回答
- code 4,-1：兜底，不做检索也不生成

### 5.2 第一级：BERT 本地分类

**文件**：`core/intent/bert_classifier.py`

**模型**：`hfl/chinese-roberta-wwm-ext-small`（约 30M 参数）

**推理耗时**：CPU ~10-50ms（对比 LLM API 的 7-9s）

**核心流程**：

```python
def predict(self, query: str, prev_query: str = None, has_images: bool = False):
    # 1. 构建输入文本（支持多轮上下文和图片信号）
    if has_images:
        input_text = f"[图片] {query}"
    elif prev_query:
        input_text = f"[上文] {prev_query} [当前] {query}"
    else:
        input_text = query

    # 2. Tokenize
    inputs = self.tokenizer(input_text, max_length=128, padding="max_length",
                            truncation=True, return_tensors="pt")

    # 3. 推理（关闭梯度计算，节省内存和时间）
    with torch.no_grad():
        outputs = self.model(**inputs)
        probs = F.softmax(outputs.logits, dim=-1).squeeze(0)

    # 4. 返回最高概率类别及其置信度
    confidence, predicted_idx = torch.max(probs, dim=0)
    return LABEL_TO_INTENT[LABEL_LIST[predicted_idx]], confidence.item()
```

**置信度阈值**：
- 纯文本查询：置信度 ≥ 0.85 才采用 BERT 结果
- 携带图片：阈值提高到 0.90（图片场景不确定性更高）
- 置信度不足：降级到 LLM

**为什么用置信度阈值而不直接取最大值**：
BERT 训练时见过的样本有限，对未见过的输入会"硬分类"到最近的类别，置信度低说明模型本身不确定。不如把不确定的案例交给 LLM 处理。

### 5.3 第二级：轻量 LLM 意图识别

**模型**：`qwen/qwen-turbo`（默认）

**专用 Prompt**（`prompt_template_intent_only.py`）：

```
你是意图分类器。根据用户当前查询选择最匹配的意图。

# 意图
- 课程查询类：课程搜索、详情、价格、报名
- 老师查询类：找老师、老师简介、评价
...

# 输入
- 用户查询: "{raw_query}"
- 上一轮对话: {prev_context}
- 是否携带图片: {has_images}

# 规则
- 重点关注【当前查询】的意图，上一轮仅供参考
{"intent": "从上述6个意图中选一个"}
```

**参数设置**：
- `max_tokens=64`：仅输出 JSON 意图，不生成回答
- `temperature=0`：需要确定性输出，不要随机性

**为什么两种 LLM 用途分开**：
轻量意图识别（64 tokens 输出）比完整意图+回答生成（2048 tokens 输出）快 10 倍，成本低 10 倍。BERT 确定时根本不调 LLM；LLM 不确定时再调重模型。

### 5.4 第三级：规则兜底（演示用）

当 BERT 未安装、LLM 未配置时，基于实体类型简单推断：

```python
def _rule_based_intent(evidence_list):
    types = {ev.type for ev in evidence_list if ev.source in ("keyword_exact", "keyword_token")}
    if "teacher" in types and "course" not in types:
        return Intent.TEACHER_QUERY
    if "course" in types or "subject" in types:
        return Intent.COURSE_QUERY
    if "feature" in types:
        return Intent.PRODUCT_FEATURE_QUERY
    return Intent.OTHER
```

**注意**：这是演示时的兜底，生产环境不存在这一级，生产环境必须有 BERT 或 LLM。

---

## 6. 模块详解：实体萃取与意图覆盖（ResponseBuilder）

### 6.1 实体萃取（extract_entities_from_evidence）

**文件**：`core/response_builder.py`

**目的**：从证据列表中提取高置信度实体，赋予语义角色（target/filter），供下游检索使用。

**过滤规则**：
```python
# 只取精确匹配和 Token 匹配，排除模糊匹配和云端向量结果
if ev.source not in ("keyword_exact", "keyword_token"):
    continue
```

**为什么排除 fuzzy 和 cloud_vector**：
- 模糊匹配置信度低，可能误判
- 向量检索结果是文档级别，不是实体级别

**角色分配**：

```python
# 意图 → 主实体类型映射
INTENT_PRIMARY_ENTITY_TYPES = {
    Intent.TEACHER_QUERY:  ["teacher"],      # 老师查询→老师实体是 target
    Intent.COURSE_QUERY:   ["course", "subject"],  # 课程查询→课程/学科是 target
    Intent.PRODUCT_FEATURE_QUERY: ["feature"],
}

for ev in evidence_list:
    if ev.type in target_types:
        entity["role"] = "target"   # 查询的核心对象
    else:
        entity["role"] = "filter"   # 过滤/关联条件
```

**示例**：
```
查询: "白马老师有哪些数学课程"
意图: COURSE_QUERY（课程查询）
证据: 褚佳麟(teacher), 数学(subject)

→ 数学(subject) role=target（课程查询的主实体是 subject/course）
→ 褚佳麟(teacher) role=filter（作为课程的过滤条件：只找褚佳麟的课）
```

### 6.2 意图覆盖规则（apply_intent_override）

**目的**：用规则纠偏 BERT/LLM 的常见误判。

**规则 R1：老师查询但无平台老师**

```python
if intent == Intent.TEACHER_QUERY and "teacher" not in entity_types:
    # 含"是谁/简介/怎么样"等问具体人的句式 → 降级为知识解答
    if any(cue in query for cue in ("是谁", "简介", "怎么样")):
        return Intent.KNOWLEDGE_QA  # 问的人不在平台，让 LLM 回答
    # 含泛浏览词 → 保持老师查询（让向量库返回推荐老师）
    if any(cue in query for cue in ("老师", "推荐", "找", "有哪些")):
        return intent
    # 兜底降级
    return Intent.KNOWLEDGE_QA
```

**为什么需要这条规则**：
- 用户问"爱因斯坦是谁"→ BERT 可能分到老师查询类（因为句式相似）
- 但爱因斯坦不在实体库→ 向量检索无结果
- 正确做法：降级到知识解答，由 LLM 回答

### 6.3 上下文实体构建（build_context_entity_list）

**目的**：将多轮对话中上一轮识别的实体，注入当前轮次的检索。

```python
# 上一轮结果存在 SessionManager 中
# 格式: {"current_teacher": "褚佳麟", "current_subject": "数学"}

def build_context_entity_list(context_entities, keyword_prober):
    for key, value in context_entities.items():
        entity_type = key.replace("current_", "")  # "current_teacher" → "teacher"
        yield {
            "entity_type":  entity_type,
            "entity_value": value,
            "origin":       "context",  # 标记来源是上下文，非当前 query
            "role":         "filter",   # 上下文实体都作为过滤条件
            "confidence":   0.8,        # 继承实体置信度略低
        }
```

**origin 字段的重要性**：`origin="context"` vs `origin="current_query"` 影响后续加权系数，当前 query 识别的实体权重（×3.0）高于上下文继承的实体（×1.5）。

---

## 7. 模块详解：意图路由与向量检索（IntentBasedRetriever）

### 7.1 路由决策

**文件**：`core/retriever/intent_based_retriever.py`

```python
# 只有检索类意图才做向量检索
RETRIEVAL_INTENTS = {
    Intent.COURSE_QUERY,
    Intent.TEACHER_QUERY,
    Intent.PRODUCT_FEATURE_QUERY,
}

def retrieve(self, intent, ...):
    if intent not in RETRIEVAL_INTENTS:
        return []  # 知识解答类、对话类、其他类不做检索
```

**产品功能查询特殊处理**：固定返回 1 条（功能入口只有一个，多了反而干扰）：
```python
if intent == Intent.PRODUCT_FEATURE_QUERY:
    top_k = 1
```

### 7.2 动态 Filter 构建

这是系统的核心算法之一：根据意图 + 实体动态生成向量库过滤条件。

**Filter 语法**（腾讯云 VectorDB 自定义）：
```
intent_word in ("课程查询类") and status="1" and (subject="数学" or main_teacher_name="褚佳麟")
```

**构建逻辑**：

```python
def _build_filter(self, intent, current_entities, user_profile, context_entities):
    must_conditions = []

    # 必须条件1：意图过滤
    intent_words = INTENT_TO_INTENT_WORD[intent]  # ["课程查询类"]
    must_conditions.append(f'intent_word in ("{", ".join(intent_words)}")')

    # 必须条件2：只返回上架内容
    must_conditions.append('status="1"')

    # 可选条件（OR 逻辑）：实体过滤
    optional = []
    for entity in current_entities:
        field = ENTITY_FILTER_MAP[entity["entity_type"]]  # teacher→main_teacher_name
        optional.append(f'{field}="{entity["entity_value"]}"')

    # 用户画像（学科偏好、年级）
    if user_profile and "grade" not in entity_types:
        optional.append(f'grades include ("{user_profile["grade"]}")')

    # 组合
    if optional:
        must_conditions.append(f'({" or ".join(optional)})')

    return " and ".join(must_conditions)
```

**OR 逻辑的意义**：`main_teacher_name="褚佳麟" or subject="数学"` 意思是"只要和褚佳麟相关，或者和数学相关的课程都召回"，然后在加权排序阶段精细化。

### 7.3 加权排序（乘法模型）

向量检索返回基础分数（0~1），然后乘以实体匹配系数：

```python
WEIGHTS = {
    "current":  3.0,  # 当前 query 识别的实体
    "context":  1.5,  # 上下文继承的实体
    "profile":  1.2,  # 用户画像
}

def _calculate_entity_boost(self, result, current_entities, user_profile, context_entities):
    multiplier = 1.0

    for entity in current_entities:
        entity_value = entity["entity_value"]
        result_field = ENTITY_FIELD_MAP[entity["entity_type"]]  # "teacher" → "teacher_name"

        if entity_value in result.get(result_field, ""):
            if entity["origin"] == "context":
                multiplier *= 1.5   # 上下文实体
            else:
                multiplier *= 3.0   # 当前 query 实体

    # 用户画像
    if user_profile and user_profile.get("subject") in result.get("subject", ""):
        multiplier *= 1.2

    return multiplier

final_score = base_score * multiplier
```

**乘法 vs 加法**：
- 加法：`final = base + boost`，当 base 很低时，boost 可以把垃圾结果拉上来
- 乘法：`final = base × boost`，只有本身分数还可以的结果才能被提升，乘法更合理

**示例**：
```
查询: "白马老师的数学课"
识别实体: 褚佳麟(teacher, current), 数学(subject, current)

候选结果A: 褚佳麟的高数课  base=0.8, teacher匹配→×3.0, subject匹配→×3.0
  final = 0.8 × 3.0 × 3.0 = 7.2

候选结果B: 张三的数学课   base=0.75, subject匹配→×3.0
  final = 0.75 × 3.0 = 2.25

候选结果C: 褚佳麟的英语课  base=0.7, teacher匹配→×3.0
  final = 0.7 × 3.0 = 2.1

排名: A >> B ≈ C  ✓ 褚佳麟的数学课排最高
```

---

## 8. 模块详解：LLM 调用链

### 8.1 架构层次

```
IntentClassifier（高层逻辑）
    ├── classify_intent_only()  → 轻量LLM，仅意图
    ├── classify()             → 重LLM，意图+回答
    └── generate_answer()      → 重LLM，仅回答（BERT已确定意图）
              ↓
    MultimodalLLMClient（HTTP 封装）
        └── chat()
              ↓
    OpenAI 兼容 API（支持 Qwen/Doubao/OpenAI）
```

### 8.2 MultimodalLLMClient：底层 HTTP 客户端

**文件**：`core/llm_client/multimodal_llm_client.py`

**两种调用模式**：

```python
# 纯文本
messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": user_message}
]

# 多模态（文本 + 图片）
user_content = [
    {"type": "text", "text": user_message},
    {"type": "image_url", "image_url": {"url": "https://..."}}
]
messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": user_content}  # list 格式
]
```

**为什么用 OpenAI 兼容接口**：Qwen、Doubao、Claude 等主流模型都提供 OpenAI 格式的 API，只需修改 `base_url` 和 `api_key`，无需改代码。

### 8.3 三种调用模式的对比

| 方法 | 模型 | max_tokens | 输出 | 延迟 | 使用场景 |
|------|------|-----------|------|------|---------|
| `classify_intent_only` | 轻量（qwen-turbo） | 64 | `{"intent": "..."}` | ~200ms | BERT 不自信时快速补充 |
| `generate_answer` | 重型 | 1024 | `{"reasoning": "...", "answer": "..."}` | ~2-5s | BERT 已知意图，只需生成回答 |
| `classify` | 重型 | 2048 | `{"reasoning": "...", "intent": "...", "answer": "..."}` | ~3-7s | 一次调用同时做意图识别和回答 |

**三种调用模式的设计哲学**：
- 尽量用 BERT 识别意图（最快，免费）
- BERT 不自信才升级到轻量 LLM（较快，便宜）
- 意图确定了才用重型 LLM 生成回答（较慢，贵）
- 只有在 BERT 完全不可用时才用 `classify`（一次性同时做两件事）

### 8.4 提示词（Prompt）工程

#### Prompt 1：轻量意图识别（INTENT_ONLY_PROMPT）

**设计目标**：极简，减少输入 token，只传必要信息。

```
你是意图分类器。
用户查询: "{raw_query}"
上一轮对话: {prev_context}
是否携带图片: {has_images}
输出: {"intent": "课程查询类"}
```

**关键设计**：
- 不传证据（证据对意图识别有帮助，但 token 成本高；轻量模式省略）
- `max_tokens=64` 限制：迫使模型只输出 JSON，不生成其他内容
- `temperature=0`：意图分类需要确定性

#### Prompt 2：完整意图+回答（INTENT_CLASSIFICATION_PROMPT_V2）

**设计目标**：充分利用证据，同时做意图识别和回答生成。

```
# 角色
你是专业AI助手，分析用户意图。

# 可用意图
- 课程查询类：搜索课程、查询价格...（详细说明）
- 老师查询类：找老师、了解老师...
...

# 输入信息
- 原始查询: "{raw_query}"
- 用户信息: {user_profile}
- 上下文实体: {context_entities_str}
- 证据信息（含匹配原因）: {evidence_str}
- 会话历史: {session_history_str}

# 分析指南
1. 证据说明：每条证据包含匹配度、原因、来源...
2. 指代词处理：参考上下文实体...
3. 综合判断...

# 回答原则
1. 必须基于证据（不编造）
2. 证据不足时明确说明
...

# 输出格式
{"reasoning": "...", "intent": "...", "answer": "..."}
```

**为什么 reasoning 字段重要**：
- 可解释性：知道模型为什么做出这个判断
- 调试用：推理过程错了，可以针对性改 Prompt
- 对用户：部分场景可以展示"AI 思考过程"

**证据注入的格式**：
```
1. 【teacher】褚佳麟 (匹配度: 1.00)
   → 匹配原因: 用户查询中的「白马老师」与该实体相关
   → 来源: 精确关键词匹配
```

**为什么明确说明匹配原因**：
- LLM 看到"白马老师"→ 褚佳麟，理解了别名关系
- 不会以为用户真的在问"白马"这个词

#### Prompt 3：纯回答生成（ANSWER_GENERATION_PROMPT）

**设计目标**：意图已知，只需生成回答，Prompt 更短更快。

```
# 已识别意图: {intent_name}

# 用户查询: "{raw_query}"
# 证据: {evidence_str}
# 上下文实体: {context_entities_str}

# 回答原则
1. 只使用证据信息，不编造
2. 根据意图类型调整风格

输出: {"reasoning": "...", "answer": "..."}
```

**与 Prompt2 的差别**：
- 不包含意图说明（已知）
- 不包含会话历史（已经在前序步骤处理）
- max_tokens=1024（不需要2048）

---

## 9. 模块详解：会话管理（SessionManager）

### 9.1 职责

**文件**：`core/session_manager.py`

管理多轮对话状态，使系统能理解"他是谁"、"这门课"等指代词。

### 9.2 数据结构

```python
session_data = {
    "session_id":      "session_abc123",
    "user_id":         "user_456",
    "session_start":   "2025-03-27T10:00:00Z",
    "session_history": [
        {
            "turn":         1,
            "timestamp":    "2025-03-27T10:00:05Z",
            "query":        "白马老师教什么",
            "intent":       "老师查询类",
            "answer":       "褚佳麟老师主要教授考研数学...",
            "entities":     [{"type": "teacher", "value": "褚佳麟", "confidence": 1.0}],
            "evidence":     [...],
            "user_feedback": None,
            "cost_time_ms": 520
        },
        ...
    ],
    "context_entities": {
        "current_teacher": "褚佳麟"
    }
}
```

### 9.3 上下文实体更新

每轮对话后自动更新上下文实体，用于下一轮指代消解：

```python
def _update_context_entities(self, entities):
    for entity in entities:
        key = f"current_{entity['type']}"  # "current_teacher"
        self.context_entities[key] = entity["value"]  # "褚佳麟"
```

**覆盖机制**：新的同类型实体会覆盖旧的：
```
第1轮: 找褚佳麟 → context = {"current_teacher": "褚佳麟"}
第2轮: 找王浩然 → context = {"current_teacher": "王浩然"}  # 覆盖了褚佳麟
```

### 9.4 上下文管理改进（P0 + P2 + P3）

**文件**：`core/llm_client/context_manager.py`

#### 原始实现的问题

原始代码把全部历史对话拼成一个字符串字段塞进 Prompt，以单轮方式调用 LLM：

```python
# 原始实现（intent_classifier.py）
session_history_str = "\n".join(
    [f"- Q: {h.get('query')}, A: {h.get('answer')}" for h in session_history]
)
# → 拼成一段文字注入到 user message 的"本次会话历史"字段
```

问题有三个：
1. **格式错误**：LLM 看到的是一段普通文字，不是 `messages` 数组，无法识别 user/assistant 角色，指代消解效果差
2. **Token 计数粗糙**：`字符数 × 0.5` 估算误差大，长对话可能超出 context window
3. **历史无压缩**：全量注入，token 成本随对话轮次线性增长

#### 两个系统的数据关系

**关键前提**：userprofile 项目处理的"AI 搜索记录"就是本搜索服务产生的用户对话历史。两个系统共用同一份数据源。

```
本搜索服务（在线）                    userprofile（离线）
  用户对话 session
    ↓ 产生对话记录
  写入数据库 ──────────────────────→  AI 搜索记录表（raw）
                                            ↓
                                      EventSummary（每个 session LLM 压缩）
                                      "用户询问了褚佳麟的考研数学课..."
                                            ↓
                                      FactMemory（增量 merge）
                                      {need: 考研数学备考, preference: 强化班}
```

因此**历史 session 的压缩已经由 userprofile 完成**，本服务不需要重复做这件事。

#### 改进后的上下文分层

```
┌──────────────────────────────────────────────────────────┐
│  system prompt 补充段（system_addon）                     │
│                                                          │
│  [FactMemory]  来自 userprofile，全量注入                 │
│  - "用户备考考研数学，关注强化班和冲刺班"                  │
│  - "用户多次询问褚佳麟（白马老师）的课程"                  │
│  （几十条一句话，已高度压缩，token 成本低）                │
│                                                          │
│  [检索到的 EventSummary]  按 query 相关性召回，按需注入   │
│  - "上次 session：用户询问了白马老师的价格和班型"           │
│  （全量存向量库，只有与当前 query 相关的才被召回注入）      │
│                                                          │
│  [当前 session 早期溢出]  在线压缩（防御性兜底）           │
│  正常 session（5-10轮）不触发，token 超预算时才启用        │
├──────────────────────────────────────────────────────────┤
│  messages 数组（当前 session 近期轮次，原文）  ← P0       │
│  {role: user,      content: 第N-4轮问题}                  │
│  {role: assistant, content: 第N-4轮回答}                  │
│  {role: user,      content: 当前 Prompt（含证据注入）}    │
└──────────────────────────────────────────────────────────┘
```

**FactMemory vs EventSummary 注入方式的差别**：

| | FactMemory | EventSummary |
|---|---|---|
| 内容 | 提炼后的持久化用户事实 | 每个 session 的对话摘要 |
| 数量 | 几十条，稳定 | 随历史 session 积累增长 |
| 注入方式 | 全量注入 system prompt | 向量检索后按需注入 |
| 原因 | 已高度压缩，token 成本可控 | 全量注入 token 成本随历史增长，且多数历史与当前无关 |

#### 三个改进点

**P0：正确的 messages 格式**

```python
# 改进后：使用 chat_with_history，传入 messages 数组
full_messages = messages_history + [{"role": "user", "content": answer_prompt}]
llm_client.chat_with_history(system_prompt=system_prompt, messages=full_messages)
```

LLM 天然理解 user/assistant 轮次，"他"、"这个"等指代词的消解准确率显著提升。

**P2：tiktoken 精准 token 计数**

```python
@staticmethod
def count_tokens(text: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")  # GPT-4 / Qwen 兼容编码
        return len(enc.encode(text))
    except ImportError:
        return int(len(text) * 0.6)                 # fallback
```

`get_messages_for_llm(budget_tokens=2000)` 从最新轮往前累加，直到超出预算为止，返回 `(recent_messages, overflow_turns)`。

**P3：与 userprofile 联动**

```python
# session 开始时，从 userprofile 读取数据并注入
ctx_mgr.set_userprofile_context(
    fact_memories=[
        "用户备考考研数学，关注强化班和冲刺班",        # FactMemory.statement
    ],
    past_session_summaries=[
        "- 用户询问了褚佳麟（白马老师）的课程\n- 关注价格和报名方式",  # EventSummary.content
    ]
)

# build() 自动把上述数据整合进 system_addon
system_addon, messages_history = ctx_mgr.build(session, budget_tokens=2000)
```

对于当前 session 内超出预算的早期轮次，使用与 userprofile `event_summary.py` 相同的 prompt 逻辑在线压缩：输入 Q&A → 输出 2-4 条教育相关要点，非教育内容标记"无教育相关内容"跳过。两边行为对齐，历史数据处理逻辑一致。

#### 注意：V1 集成边界

userprofile V1 是离线构建（`不做在线服务`），输出为 per-user JSON 文件。当前集成方式：

```
userprofile 构建阶段 → data/demo_output/{user_key}.json
本服务运行时 → 读取该 JSON → ctx_mgr.set_userprofile_context(...)
```

未来 userprofile 提供在线 API 后，可直接在 session 开始时实时查询，无需离线文件中转。

### 9.5 token 精准计数与预算切分

```python
# P2: 精准计数
tokens = SessionManager.count_tokens(text)  # tiktoken 或 fallback

# 从最新轮往前取，直到超出预算
recent_messages, overflow_turns = session.get_messages_for_llm(
    budget_tokens=2000,
    keep_recent=5,
)
# recent_messages → [{role: user, content:...}, {role: assistant, content:...}, ...]
# overflow_turns  → 早于预算的轮次，传给 ctx_mgr 压缩
```

### 9.6 历史长度控制

**双重限制**，防止上下文过长导致 token 超限：

```python
def _trim_history(self):
    # 限制1：最多保留 max_turns 轮
    if len(self.session_history) > self.max_turns:
        self.session_history = self.session_history[-self.max_turns:]

    # 限制2：token 精准计数
    while self._estimate_tokens() > self.max_tokens and len(self.session_history) > 1:
        self.session_history.pop(0)
```

### 9.7 三种历史格式

```python
# full：完整信息，用于存储和分析
session.get_history(format="full")

# compact：精简版，用于传给 LLM（减少 token）
session.get_history(format="compact")
# → [{"turn": 1, "query": "...", "intent": "...", "entities": [...]}]

# minimal：最小版，只有 Q&A
session.get_history(format="minimal")
# → [{"query": "...", "answer": "..."}]
```

---

## 10. 数据结构全览

### 10.1 请求（SearchRequest）

```python
{
    "query":            "我想找白马老师",           # 必填：用户原始查询
    "user_id":          "user_123",                # 可选：用户 ID
    "session_id":       "session_abc",             # 可选：会话 ID
    "images":           ["https://xxx.jpg"],        # 可选：图片 URL 列表（多模态）
    "user_profile":     {                          # 可选：用户画像
        "grade":              "高三",
        "subject_preference": ["数学", "物理"]
    },
    "session_history":  [...],                     # 可选：历史对话（多轮）
    "context_entities": {                          # 可选：上下文实体（指代消解）
        "current_teacher": "褚佳麟"
    }
}
```

### 10.2 响应（SearchResponse）

```python
{
    "raw_query":       "我想找白马老师",
    "query_processed": "我想找白马老师",            # 预处理后
    "intent":          102,                       # 意图数值编码
    "intent_name":     "老师查询类",               # 意图中文名
    "entities": [
        {
            "entity_id":    "T001",
            "entity_type":  "teacher",
            "entity_value": "褚佳麟",
            "confidence":   1.0,
            "matched_text": "白马老师",            # 原始查询中的命中文本
            "source":       "keyword_exact",      # 匹配来源
            "origin":       "current_query",       # current_query 或 context
            "role":         "target"              # target 或 filter
        }
    ],
    "search_results": [                           # 向量检索结果（检索类意图）
        {"id": "T001", "name": "褚佳麟", "intent_word": "老师查询类", "score": 0.95}
    ],
    "answer":          null,                      # LLM 回答（知识解答类才有）
    "cost_time":       0.158                      # 总耗时（秒）
}
```

### 10.3 内部数据流

```
Evidence（证据）
    ↓  extract_entities_from_evidence()
EntityDict（结构化实体）= {entity_id, entity_type, entity_value, confidence, role, origin}
    ↓  IntentBasedRetriever.retrieve()
SearchResult = {id, name, intent_word, score}
    ↓  (同时)
LLMResponse = {intent, answer, reasoning}  （知识解答类才有）
    ↓  合并
FinalResponse
```

---

## 11. Prompt 设计详解

### 11.1 设计原则

**1. 角色定义**：明确告诉 LLM 它是什么（意图分类器 vs 知识助手），影响输出风格和关注点。

**2. 结构化输入**：用标签（`# 角色`, `# 输入`, `# 规则`）分隔，LLM 更容易找到关键信息。

**3. 输出格式约束**：强制 JSON 输出，`temperature=0`，避免自由发挥。

**4. 证据优先原则**：明确告知"只使用证据回答，不编造"，解决 LLM 幻觉问题。

**5. 兜底指令**：当证据不足时告知如何回答（"抱歉暂时没有相关信息"），而不是让 LLM 自由发挥。

### 11.2 证据格式设计

```
1. 【teacher】褚佳麟 (匹配度: 1.00)
   → 匹配原因: 用户查询中的「白马老师」与该实体相关
   → 来源: 精确关键词匹配
```

**这种格式的意图**：
- `【类型】值` 帮助 LLM 理解实体的语义类型
- 匹配度表达置信度
- 匹配原因是关键：告诉 LLM "白马老师" = "褚佳麟" 的映射关系
- 来源帮助 LLM 判断证据可信度（精确匹配 > 模糊匹配）

### 11.3 Prompt 版本演进

| 版本 | 特点 | 适用场景 |
|------|------|---------|
| INTENT_ONLY | 极简，64 tokens 输出 | BERT 不自信时快速兜底 |
| V2（完整版） | 证据+历史+上下文，2048 tokens | 首次请求，无 BERT |
| ANSWER_GENERATION | 意图已知，1024 tokens | BERT 确定意图后生成回答 |

**版本选择逻辑**：
```
有 BERT 且自信 → generate_answer（Prompt3）
有 BERT 但不自信 → classify_intent_only（Prompt1）→ generate_answer（Prompt3）
无 BERT → classify（Prompt2）
```

---

## 12. BERT 训练指南

### 12.1 训练流程概览

```
数据准备 → 实体标注 → 数据增强 → 模型微调 → 评估 → 部署
```

详细步骤见 `bert_training_ref/README_BERT.md`。

### 12.2 训练数据构成

**三类数据来源，优先级从高到低**：

| 来源 | 文件 | 数量 | 价值 |
|------|------|-----:|------|
| 真实用户标注数据 | `intent_train_real.csv` | ~14k | 最高，反映真实分布 |
| 真实数据打标版 | `intent_train_real_tagged.csv` | ~14k | 高，加了实体标注的真实数据 |
| 模板×实体合成 | `intent_train.csv` | ~54k | 覆盖实体组合，但分布失衡 |
| 均衡合并版 | `intent_train_merged.csv` | ~19k | 上两者的均衡混合 |

**训练集格式（含实体标注）**：

```csv
text,label
〈老师〉白马老师是谁,老师查询类
〈老师〉白马老师有什么课,课程查询类
〈考试〉考研〈学科〉数学有哪些班,课程查询类
AI闪学在哪里,产品功能查询类
你好啊,对话类
这道题怎么解,知识解答类
```

注意：`AI闪学` 如果不在 entities.csv 中，则不会被标注（这是正常的，见 12.3 实体标注设计）。

**数据集规模参考**：
- 每类最少 200 条
- 真实用户数据 > 人工构造数据
- 带实体标注的样本占比越高，hard case 表现越好

### 12.3 核心设计：实体标注（Entity-Augmented Training）

#### 解决的本质问题

**语义歧义与指代消解**：

```
"土豆厉害吗"  →  BERT 不知道"土豆"是老师名 → 可能误判为其他类
"〈老师〉土豆厉害吗"  →  〈老师〉 信号直接告知意图 → 老师查询类 ✓
```

**新实体零改动泛化**：

```
传统做法：新老师"小熊"上线 → 收集小熊相关数据 → 重新训练 BERT
实体标注：新老师"小熊"加入 entities.csv → KeywordProber 自动识别
         → 推理时自动打标 〈老师〉小熊 → BERT 立刻正确分类，无需重训
```

BERT 学到的是 **"〈老师〉X + 句式 → 标签"**，而非记忆具体名字。实体知识外置到 entities.csv，与模型权重解耦。

#### 训练/推理一致性

两端使用相同的 `ENTITY_TYPE_ZH` 映射和相同的最长匹配算法：

```
训练端：generate_training_data.py → auto_tag(text, name_to_type)
推理端：bert_classifier.py → _tag_with_evidence(query, evidence_list)

相同映射：{"teacher":"老师", "course":"课程", "feature":"功能", ...}
相同逻辑：贪心左到右最长匹配，避免长串/短串别名互相干扰
```

#### 混合训练策略

每条合成样本同时生成 tagged 和 untagged 两种版本：

```python
# generate_training_data.py 中的 add() 函数
def add(text, label, tag=True):
    if tag:
        tagged = auto_tag(text, name_to_type)
        samples.append((tagged, label))     # 主版本（约70%）
        if tagged != text and random.random() < 0.3:
            samples.append((text, label))   # untagged 兜底（约30%）
```

**为什么要保留 untagged 样本**：推理时可能出现两种情况：
- 实体在 entities.csv 中 → 被 KeywordProber 识别 → 打标 → 走 tagged 路径
- 实体不在 entities.csv 中 → 未被识别 → 无标注 → 走 untagged 路径（依靠句式特征）

混合训练使 BERT 在两种情况下都能正常工作。

### 12.4 工业界常用数据构造技巧

#### 已实现

**1. 实体标注（Entity-Augmented Classification）**
见 12.3，是本项目最核心的技巧。

**2. 模板×实体笛卡尔积展开**
人工写句式模板，从 entities.csv 自动拉取实体填充，规模化生成覆盖所有实体名称的样本。

**3. 同义词替换增强（Synonym Substitution）**
```python
SYNONYM_MAP = {"怎么样": ["好不好", "如何"], "多少钱": ["什么价格", "费用多少"], ...}
# 随机替换句中的词，产生相同语义但不同表达的新样本
```

**4. 多轮对话上下文拼接**
将单轮样本随机加上文，训练 BERT 理解上下文依赖：
```
"他的课多少钱" → "[上文] 〈老师〉白马老师是谁 [当前] 他的课多少钱"
```

#### 未实现（如需可扩展）

**5. LLM 改写多样化（Paraphrase Augmentation）**
对已有样本让 LLM 生成多样化改写，覆盖模板无法穷举的长尾表达：
```
原始: "白马老师是谁"
LLM 改写: "这个叫白马的老师什么来头", "白马老师牛不牛", "帮我了解一下白马老师"
```
对长尾类别（对话类、其他类）效果最显著。

**6. LLM 专项 Hard Case 构造**
让 LLM 专门生成歧义样本，重点覆盖"老师名 + 课程动词"这类容易混淆的场景：
```
prompt: "生成50条提到老师名字但实际在询问课程的句子（课程查询类）"
```

**7. 回译增强（Back-Translation）**
中文 → 英文 → 中文，利用翻译的语言多样性生成不同句式的同义样本。
成本高但对低资源类别（对话类、其他类）有效。

**8. 伪标签（Pseudo-Labeling）**
用训练好的模型对无标注真实查询做预测，置信度高的样本加入训练集：
```python
if confidence > 0.95:
    new_samples.append((query, predicted_label))
```
可持续扩充训练集，但需要人工抽检质量。

**9. LLM 对话类/其他类专项补充**
模板数据在对话类（70条）和其他类（26条）极度稀缺，最适合直接让 LLM 生成：
- 对话类：用户在教育 APP 说"谢谢"、"好的"、"知道了"的各种变体
- 其他类：误触、无关话题、乱码输入等真实噪声场景

**10. Mixup（嵌入层插值）**
在 BERT 嵌入空间对两个样本做线性插值，生成虚拟训练样本：
```python
mixed_embed = λ * embed_A + (1-λ) * embed_B
mixed_label = λ * label_A + (1-λ) * label_B  # soft label
```
对类别边界模糊的样本（老师查询 vs 课程查询）有一定帮助。

### 12.5 预训练模型选择

#### 模型对比

| 模型 | 参数量 | 磁盘大小 | CPU 推理延迟 | 准确率 | 推荐场景 |
|------|-------:|--------:|------------:|------:|---------|
| `tiansz/bert-base-chinese` | 102M | ~400MB | ~80-120ms | 高 | 默认，下载快 |
| `hfl/chinese-roberta-wwm-ext-small` | 30M | ~110MB | **~10-30ms** | 较高 | **推荐线上部署** |
| `hfl/chinese-roberta-wwm-ext` | 102M | ~400MB | ~80-120ms | 高 | 精度要求高 |
| `hfl/chinese-macbert-base` | 102M | ~400MB | ~80-120ms | **最高** | 离线评测/实验 |
| `hfl/chinese-macbert-large` | 324M | ~1.3GB | ~300ms+ | 极高 | 仅 GPU 可用 |

**延迟说明**：以上均为单条推理、CPU、batch_size=1 的估算值。实际延迟受 CPU 核数、内存带宽影响较大。线上服务通常 batch_size=1 或小 batch，small 模型在这个场景下优势更明显。

#### 推荐选择：hfl/chinese-roberta-wwm-ext-small

- 参数量仅为 base 的 30%，推理速度 4-10 倍提升
- 在教育场景短文本分类（< 50 字）上准确率与 base 差距通常 < 1%
- 磁盘 110MB，容器镜像友好
- CPU 推理 ~10-30ms，满足 50ms 延迟预算下的意图识别前置

#### 模型下载

```bash
# 方式1：ModelScope（国内推荐，默认）
MODEL_NAME=hfl/chinese-roberta-wwm-ext-small python -m bert_training_ref.train

# 方式2：本地路径（已有模型时）
LOCAL_MODEL_PATH=/path/to/local/model python -m bert_training_ref.train

# 方式3：HuggingFace（需要科学上网）
# 先手动 git clone，再用 LOCAL_MODEL_PATH 指向
```

#### 关于量化（Quantization）

量化可进一步压缩模型大小和推理速度，适合资源受限场景：

| 量化方式 | 工具 | 大小变化 | 速度变化 | 精度损失 |
|---------|------|-------:|-------:|-------:|
| INT8 动态量化 | `torch.quantization` | ~110MB→~28MB | CPU 加速约 2x | < 1% |
| ONNX Runtime | `optimum` | 同上 | CPU 加速约 2-4x | < 1% |
| 不量化 | — | 110MB | 基准 | 0% |

对于本项目的 small 模型，INT8 动态量化代码如下（训练完成后执行一次）：

```python
import torch
model = torch.load("output/best_model/pytorch_model.bin")
quantized = torch.quantization.quantize_dynamic(
    model, {torch.nn.Linear}, dtype=torch.qint8
)
torch.save(quantized.state_dict(), "output/best_model/pytorch_model_int8.bin")
# 结果：~110MB → ~28MB，CPU 推理约从 20ms 降到 10ms
```

实际部署中，对于意图分类这种短文本任务，INT8 量化通常不影响分类准确率（分类边界对量化噪声不敏感）。

---

### 12.6 训练超参数全表

所有参数均通过环境变量配置，不改代码：

| 环境变量 | 默认值 | 说明 | 调参建议 |
|---------|--------|------|---------|
| `MODEL_NAME` | `tiansz/bert-base-chinese` | ModelScope 预训练模型 ID | 推荐换 `hfl/chinese-roberta-wwm-ext-small` |
| `LOCAL_MODEL_PATH` | 空 | 本地模型路径，优先级高于 MODEL_NAME | 已下载时设置避免重复下载 |
| `DATA_MODE` | `constructed` | 训练数据模式：`constructed`=合成数据，`merged`=合成+真实均衡版 | 有真实数据时用 `merged` |
| `EPOCHS` | `10` | 训练轮数 | small 模型 8-12 轮；base 模型 5-8 轮；EarlyStopping 会自动截停 |
| `BATCH_SIZE` | `32` | 每 GPU/CPU 的 batch 大小 | 内存允许时尽量大；CPU 训练用 16-32 |
| `LR` | `2e-5` | 学习率 | BERT 微调标准值；small 模型可试 3e-5；大数据集可降到 1e-5 |
| `MAX_LENGTH` | `128` | tokenizer 最大序列长度 | 教育查询通常 < 50 字，128 完全足够；改 64 可加速约 40% |
| `FOCAL_GAMMA` | **`2.0`** | Focal Loss 的 γ 参数 | 默认开启；0=关闭；2.0 是标准推荐值 |
| `LABEL_SMOOTHING` | **`0.1`** | Label Smoothing 参数 | 默认开启；0=关闭；0.1 是标准推荐值 |

#### 推荐训练命令

```bash
# 默认训练（类别权重 + Focal Loss γ=2 + Label Smoothing 0.1，三种增强全开）
python -m bert_training_ref.train

# 关闭增强（仅类别权重，用于对比实验）
FOCAL_GAMMA=0.0 LABEL_SMOOTHING=0.0 python -m bert_training_ref.train

# 生产配置（推荐模型 + 均衡数据）
MODEL_NAME=hfl/chinese-roberta-wwm-ext-small \
DATA_MODE=merged \
EPOCHS=12 \
LR=3e-5 \
python -m bert_training_ref.train
```

---

### 12.7 训练方法详解（WeightedTrainer）

`train.py` 的 `WeightedTrainer` 继承自 HuggingFace `Trainer`，重写了 `compute_loss`，支持三种损失函数增强，可组合使用。

#### 1. 类别权重（Class Weights）

**解决问题**：标签频率不均衡（课程查询类 33k 条 vs 其他类 26 条）。

```python
# 权重 = 样本数倒数，归一化到 num_labels
label_counts = np.bincount(train_dataset.labels)
weights = 1.0 / label_counts
weights = weights / weights.sum() * num_labels
class_weights = torch.FloatTensor(weights)
```

效果：稀有类别（对话类、其他类）在梯度更新时权重放大，避免模型只会预测多数类。

#### 2. Focal Loss（`FOCAL_GAMMA > 0` 时启用）

**解决问题**：easy sample 占据太多梯度，hard case 学习不充分。

数学原理：

```
FL(p_t) = -(1 - p_t)^γ × log(p_t)

p_t：模型对正确类别的预测概率
γ=0：退化为标准 CE
γ=2（推荐）：
  p_t=0.9（easy sample）→ 权重 = 0.01，梯度压缩 100 倍
  p_t=0.5（hard sample）→ 权重 = 0.25，梯度几乎不变
```

实现关键：必须用 `reduction='none'` 拿到 per-sample loss，再乘 focal 权重，最后 `.mean()`。

```python
ce_loss = loss_fct(logits, labels)        # per-sample loss (batch_size,)
pt = torch.exp(-ce_loss)                  # 正确预测概率
focal_weight = (1 - pt) ** self.gamma
loss = (focal_weight * ce_loss).mean()
```

与类别权重的关系：类别权重解决**频率不均衡**（多数类压少数类），Focal Loss 解决**难度不均衡**（easy sample 淹没 hard case）。两者作用维度不同，可叠加。

#### 3. Label Smoothing（`LABEL_SMOOTHING > 0` 时启用）

**解决问题**：对歧义样本（老师名+课程动词）强制学成 hard 标签会过拟合。

```python
# label_smoothing=0.1：targets 从 hard [0,1,0,0,0,0] 变为 soft [0.02, 0.9, 0.02, ...]
loss_fct = CrossEntropyLoss(label_smoothing=self.label_smoothing)
```

效果：模型不会对置信度低的样本过于自信，置信度阈值（0.85）更准确反映真实不确定性，降低错误触发 LLM 兜底的概率。

#### 组合效果

三者叠加时的梯度流向：

```
一个 easy sample（老师名简单查询，CE=0.05）：
  × 类别权重（如老师查询类占多数，权重=0.8）
  × Focal 权重（(1-0.95)^2 = 0.0025）
  最终梯度 ≈ 原来的 0.2%，几乎不参与训练

一个 hard sample（老师名+课程动词，CE=0.7）：
  × 类别权重（课程查询类）
  × Focal 权重（(1-0.5)^2 = 0.25）
  最终梯度 ≈ 正常量，充分参与训练
```

---

### 12.8 评估指标

训练脚本用 **weighted F1** 选最优 checkpoint（`metric_for_best_model="f1"`）。

EarlyStopping 条件：连续 3 个 epoch F1 不提升则停止。

评估时各指标含义：

| 指标 | 含义 | 关注点 |
|------|------|--------|
| `accuracy` | 整体分类准确率 | 受多数类影响大，参考意义有限 |
| `f1` (weighted) | 各类 F1 按样本数加权平均 | **主要指标**，平衡精度和召回 |
| `precision` | 预测为某类的样本中真正是该类的比例 | 误判率指标 |
| `recall` | 某类样本中被正确识别的比例 | 漏判率指标 |

验证集应包含足够的每类样本（每类 ≥ 50 条），否则 F1 估算不稳定。

---

### 12.9 模型部署路径

```
bert_training_ref/output/best_model/
    ├── config.json              ← 模型结构配置（含 label2id/id2label）
    ├── pytorch_model.bin        ← 模型权重
    ├── tokenizer_config.json    ← tokenizer 配置
    ├── vocab.txt                ← 词表
    └── label_config.json        ← 标签列表（本项目额外保存）
```

将此目录放到：
```
algorithm_demo/core/intent/bert_training/output/best_model/
```

`pipeline_demo.py` 启动时自动检测，存在则启用 BERT，不存在则跳过（走 LLM 兜底）。

验证 BERT 已启用：

```bash
python -m pipeline_demo
# 初始化输出中应出现：
# ✓  BertIntentClassifier（本地 BERT，~10-50ms）
```

---

## 13. 配置与运行

### 13.1 依赖安装

```bash
# 最小可运行集合（本地关键词 + 基础预处理 + demo 主链）
pip install jieba opencc-python-reimplemented flashtext

# 预处理增强（可选）
pip install pypinyin         # 启用拼音转汉字
pip install rapidfuzz        # 启用模糊匹配兜底

# LLM 功能（可选）
pip install openai

# 云向量检索（可选）
pip install tcvectordb

# BERT 意图分类（可选）
pip install torch transformers

# 本地语义检索回退（可选）
pip install sentence-transformers faiss-cpu
```

**不要误解这里的“可选”**：
- 可选的意思是“缺失时功能降级，但主链不断”
- 不是“这部分技术路线不重要”

### 13.2 环境变量

| 变量 | 说明 | 示例值 |
|------|------|-------|
| `LLM_API_KEY` | LLM API Key | `sk-xxx` |
| `LLM_BASE_URL` | LLM API 地址（OpenAI 兼容） | `https://api.openai.com/v1` |
| `LLM_MODEL` | 轻量模型（意图识别） | `qwen/qwen-turbo` |
| `LLM_HEAVY_MODEL` | 重模型（回答生成） | `qwen/qwen3-vl-plus` |
| `VECTOR_DB_URL` | 腾讯云 VectorDB 地址 | `http://10.x.x.x:80` |
| `VECTOR_DB_USERNAME` | 数据库用户名 | `root` |
| `VECTOR_DB_KEY` | 数据库密钥 | `your-key` |
| `VECTOR_DB_DATABASE` | 数据库名 | `ai-search` |
| `VECTOR_DB_COLLECTION` | 集合名 | `search-0224` |
| `VERBOSE` | 开启详细日志 | `1` |

兼容键名：
- `API_KEY` → `LLM_API_KEY`
- `API_BASE_URL` → `LLM_BASE_URL`
- `MODEL_NAME` → `LLM_MODEL`

### 13.3 运行方式

```bash
cd /path/to/algorithm_demo     # 进入文件夹（不需要是项目根目录）

# 演示模式（5个预设用例）
python -m pipeline_demo

# 显示完整 Prompt 文本
python -m pipeline_demo --show-prompts

# 多轮对话演示
python -m pipeline_demo --multiturn

# 交互模式
python -m pipeline_demo --interactive
# 支持命令: exit | clear | context | history | prompts

# 单次查询
python -m pipeline_demo --query "考研数学有哪些课程"

# 启用 LLM
LLM_API_KEY=sk-xxx LLM_BASE_URL=https://xxx/v1 python -m pipeline_demo

# 详细日志
VERBOSE=1 python -m pipeline_demo
```

**说明**：
- 直接运行 `python pipeline_demo.py` 也可以
- 但独立仓库形态下更推荐 `python -m pipeline_demo`，与 README 和训练脚本保持一致

### 13.4 可移植性验证

```bash
# 将整个文件夹复制到任意位置
cp -r algorithm_demo /tmp/my_demo

# 直接运行，无需原项目
python /tmp/my_demo/pipeline_demo.py
```

### 13.5 功能降级矩阵

| 缺失项 | 影响 | 是否阻断主链 |
|------|------|-------------|
| `pypinyin` | 关闭拼音转汉字 | 否 |
| `rapidfuzz` | 关闭模糊匹配回退 | 否 |
| `openai` 或 LLM 环境变量 | 关闭 LLM 意图识别与回答生成 | 否 |
| 本地 BERT 模型目录缺失 | 跳过 BERT，本地意图分类不可用 | 否 |
| `sentence-transformers` / `faiss-cpu` | 关闭本地语义回退 | 否 |
| `tcvectordb` 或 `VECTOR_DB_*` | 关闭云向量检索 | 否 |

这张表的意义不是“少装点依赖也没事”，而是明确系统的故障边界：
- 核心演示链路可以在缺少外部服务时仍然讲清楚
- 但能力会退化，退化到什么程度必须明确说明，不能模糊表述成“都支持”

---

## 14. 扩展指南

### 14.1 添加新意图类型

1. **更新意图枚举**（`core/intent/intent_types.py`）：
```python
class Intent(Enum):
    LIVE_QUERY = "直播查询类"  # 新增

INTENT_CODE_MAP[Intent.LIVE_QUERY] = 104  # 新增编码
```

2. **更新路由配置**（`core/retriever/intent_based_retriever.py`）：
```python
RETRIEVAL_INTENTS.add(Intent.LIVE_QUERY)
INTENT_TO_INTENT_WORD[Intent.LIVE_QUERY] = ["直播查询类"]
```

3. **更新 Prompt** (`core/llm_client/prompt_template_v2.py`)：
```
- 直播查询类：查询直播课程、直播时间、直播链接等；
```

4. **添加训练数据**（`bert_training_ref/data/intent_train.csv`）：
```
这节课几点直播,直播查询类
明天有直播吗,直播查询类
```

5. **重新训练 BERT**。

### 14.2 添加新实体类型

1. **更新实体词典**（`core/prober/data/entities.csv`）：
```csv
L001,数学直播冲刺,live_course,直播冲刺;数学直播
```

2. **更新 filter 映射**（`core/retriever/intent_based_retriever.py`）：
```python
ENTITY_FILTER_MAP["live_course"] = "live_course_name"
```

3. **更新实体萃取优先级**（`core/response_builder.py`）：
```python
INTENT_PRIMARY_ENTITY_TYPES[Intent.LIVE_QUERY] = ["live_course"]
```

### 14.3 接入新 LLM

只要支持 OpenAI 兼容接口，修改环境变量即可：

```bash
# 切换到 Doubao
export LLM_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
export LLM_MODEL=doubao/Doubao-pro-32k

# 切换到本地 Ollama
export LLM_BASE_URL=http://localhost:11434/v1
export LLM_MODEL=qwen2.5:7b
export LLM_API_KEY=dummy  # Ollama 不需要真实 key，但字段不能为空
```

---

## 15. 关键设计决策

### 15.1 为什么用两阶段意图识别而不是直接 LLM

**问题**：直接 LLM 识别意图很准，为什么要 BERT？

**答**：
- 延迟：LLM ~7s，BERT ~20ms，差 350 倍
- 成本：每次调 LLM 都要钱，BERT 本地推理免费
- 可用性：LLM 服务可能超时或不可用，BERT 本地兜底

**反驳意见处理**：BERT 训练数据有限，准确率不如 LLM？
→ 用置信度阈值：只有置信度高的才用 BERT，低置信度的交给 LLM，兼顾速度和准确率。

### 15.2 为什么 EvidenceCollector 在意图识别前运行

**直觉**：先知道意图再收集证据不是更合理吗？

**现实**：
- 意图识别本身需要证据来辅助（"白马老师"的存在说明这是老师查询）
- 如果先识别意图，BERT 没有"白马老师=褚佳麟"的背景知识，可能误判
- 流程：先收集证据 → 证据帮助意图识别 → 意图指导后续检索

### 15.3 为什么 Filter 用 OR 逻辑而不是 AND

**AND 的问题**：
```
teacher="褚佳麟" AND subject="数学"
→ 只召回褚佳麟的数学课
→ 如果褚佳麟只教政治，结果为空
```

**OR 的优势**：
```
teacher="褚佳麟" OR subject="数学"
→ 召回褚佳麟的所有课 + 所有数学课
→ 再通过加权排序把最相关的推到前面
```

OR 保证召回率，加权排序保证精确度。

### 15.4 为什么 `apply_intent_override` 只用当前 query 实体

```python
# 注意：只传 current_query_entities，不传 context 实体
final_intent = apply_intent_override(final_intent, current_query_entities, clean_query)
```

**原因**：意图覆盖规则基于"当前用户问的是什么"，上下文实体来自上一轮，不应影响当前意图判断。

**例子**：
- 上一轮：问褚佳麟（context_teacher=褚佳麟）
- 当前轮：问"爱因斯坦是谁"（无实体匹配）
- 如果把 context 实体（褚佳麟）传入覆盖规则，会错误保持"老师查询"意图
- 正确：当前 query 无实体 → 触发 R1 规则 → 降级知识解答

### 15.5 Session 历史 token 控制的重要性

OpenAI API 有 context window 限制（通常 4k~128k tokens）。如果无限追加历史：
- 第 20 轮对话：历史可能超过 10k tokens
- API 报错，或截断导致模型"失忆"

**本系统方案**：
- 轮次限制（默认 10 轮）
- Token 数估算限制（默认 2000 tokens）
- 当超限时删除最早的轮次（保留最近的上下文）

---

## 附录：术语表

| 术语 | 说明 |
|------|------|
| Evidence | 证据：从查询中提取的实体命中记录，包含来源、值、类型、置信度 |
| Intent | 意图：用户查询的语义目的分类（老师查询、课程查询等） |
| Entity | 实体：可识别的命名对象（老师名、课程名、功能名等） |
| Session | 会话：用户与系统的一次连续对话，包含多个轮次 |
| Context Entity | 上下文实体：从历史对话中提取并保留的实体，用于指代消解 |
| Filter | 过滤条件：向量检索时的前置筛选表达式 |
| BERT | 双向编码器表示（Bidirectional Encoder Representations from Transformers） |
| BM25 | 最优匹配25：基于词频的经典文本检索算法 |
| FlashText | 基于 Aho-Corasick 的高效多模式字符串匹配库 |
| OpenCC | 开放中文转换：繁简转换库 |
| Hybrid Search | 混合检索：稠密向量检索 + 稀疏 BM25 检索的结合 |
| Weighted Rerank | 加权重排：将多路检索结果按权重融合排序 |
| TF-IDF | 词频-逆文档频率：衡量词语重要性的经典统计方法 |
| target/filter | 实体角色：target=用户查询的核心对象，filter=过滤/关联条件 |
| origin | 实体来源：current_query=本轮识别，context=上下文继承 |

---

*文档版本：2026-03 | 作者：AI Search Pipeline Team*
