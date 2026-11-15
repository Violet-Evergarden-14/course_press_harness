# 术语表

> 状态：设计稿 v0.1
> 依赖：[unit-schema.md](unit-schema.md) §6、[rendering.md](rendering.md) §8
> 本文件定义术语表结构、引用语法、一致性检查与多语言处理。

---

## 1. 作用

术语表解决三个问题：

1. **同一概念只有一种写法**。跨讲、跨素材、跨语言时不会出现「满二叉树」与「满二叉数」「完全满二叉树」并存。
2. **多语言输出有唯一映射**。中英对照不在正文中维护，而在术语表中集中维护。
3. **术语可追溯定义**。每个术语绑定一个知识点，读者首次遇到时可跳转。

术语表只管理**名称**，不管理解释。解释属于知识点。

---

## 2. 存储

```
.study/
├── glossary.yaml                    # 全局术语表，人工维护
└── knowledge/units/*.md             # 定义所在处
```

配置项 `glossary.path` 可指向课程外部文件，便于同一学科多门课共用。

---

## 3. 结构

```yaml
schema_version: 1

terms:
  - id: full-binary-tree
    zh: 满二叉树
    en: full binary tree
    definition_unit: full-binary-tree-def
    aliases: [完美二叉树]
    forbidden: [满二叉数, 完全满二叉树]
    first_use: lecture-06

  - id: wpl
    zh: 带权路径长度
    en: weighted path length
    short: WPL
    definition_unit: wpl-def
    aliases: []
    forbidden: [加权路径长度]
```

### 3.1 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | `[a-z0-9-]`，全局唯一，格式同知识点 id |
| `zh` | 是 | 中文标准写法 |
| `en` | 否 | 英文标准写法；缺失时 `en` 模式下回退 `zh` |
| `short` | 否 | 缩写，用于首次定义后的简称 |
| `definition_unit` | 是 | 首次给出定义的知识点 id，须存在 |
| `aliases` | 否 | 允许的等价写法，渲染时不改写，但检查时视为正确 |
| `forbidden` | 否 | 明确禁止的写法，出现即报错 |
| `first_use` | 否 | 预期首次出现的讲次，用于前向引用检查 |

### 3.2 约束

- `zh` 与其他术语的 `zh`、`aliases`、`short` 不得冲突
- `forbidden` 中的写法不得同时出现在任何术语的 `zh` / `aliases` 中
- `definition_unit` 必须存在，且该知识点须包含定义性小节（`concept` 类型的「定义」）
- 一个术语只能有一个 `definition_unit`，多处解释应改为 `see_also` 链接

---

## 4. 引用语法

正文中使用 `[[term:<id>]]`，渲染为当前语言的名称。

| 场景 | 写法 | 渲染（zh） |
|---|---|---|
| 通用引用 | `[[term:wpl]]` | 带权路径长度 |
| 带简称 | `[[term:wpl\|short]]` | WPL |
| 首次定义处 | 定义知识点内直接写全称，不加引用 | 带权路径长度（WPL） |
| 需要强调术语 | `[[term:full-binary-tree\|**]]` | **满二叉树** |

规则：

- 除定义处外，正文中的术语**必须**通过引用使用，不得直接书写名称
- 定义处由 `definition_unit` 唯一确定，程序据此豁免该知识点的引用要求
- 引用同一术语超过 3 次时，`md` 模板默认第 4 次起渲染为简称（可配置关闭）

---

## 5. 一致性检查

| 检查 | 层 | 严重度 | 说明 |
|---|---|---|---|
| 引用的术语 id 存在 | L1 | blocker | 否则无法渲染 |
| `definition_unit` 存在 | L1 | blocker | 否则术语无定义来源 |
| 术语名称冲突（`zh` / `aliases` / `short`） | L1 | blocker | 同一名称对应两个术语 |
| 正文出现 `forbidden` 写法 | L1 | blocker | 明确禁止的错误写法 |
| 正文直接书写术语名称而未用引用（定义处除外） | L1 | major | 常见于生成内容的不一致 |
| 术语在 `definition_unit` 之前的讲次被引用 | L1 | major | 需要前置说明或前向引用 |
| 同一术语在两个知识点中给出定义 | L3 | major | 定义分裂 |
| 术语翻译不一致（`en` 模式产物中同一术语两种译法） | L3 | major | 需要人工确认标准译名 |

「正文直接书写术语名称」的检查方式：对每个术语的 `zh`、`aliases`、`short` 做全文匹配，命中位置不在 `[[term:...]]` 内且不在 `definition_unit` 中即报。为避免误伤（如「树」这类常见字），名称长度小于 3 个字符的术语不参与该检查。

---

## 6. 新术语的准入

新增术语须同时满足：

1. 已在素材中出现（有 `evidence` 可指）
2. 存在一个知识点给出定义，且该定义可被审查（L2 判为 `supported` 或 `derived`）
3. 不与既有术语同义

不满足第 1 条时，说明是补充内容（`origin: synthesized`），须在术语表中标记：

```yaml
  - id: amortized-analysis
    zh: 均摊分析
    definition_unit: amortized-analysis-def
    origin: synthesized
```

`synthesized` 术语在 `review` 视图中默认不出现在术语表附录中，避免把补充内容当课程要求。

---

## 7. 多语言

### 7.1 名称

术语名称由术语表提供，渲染时按 `lang` 取值，不做其他处理。

### 7.2 正文

正文翻译不在渲染阶段进行，见 [rendering.md](rendering.md) §8。译文文件：

```
patches/i18n/<unit>.<lang>.md
```

结构要求：与原文小节一一对应，节标题保持对应，便于渲染器对齐并列。

```yaml
# patches/i18n/huffman-construct.en.yaml
source_hash: sha256:...          # 对应的原文内容哈希
sections:
  问题: "Given $n$ weights ..."
  步骤: "..."
```

`source_hash` 不匹配时视为译文过期，`bilingual` 模式下回退原文并标记，同时报 `major`。

### 7.3 语言选择

| 场景 | 取值 |
|---|---|
| 课程原文为中文，输出中文 | `zh` |
| 课程原文为中文，输出英文 | `en`，缺译处回退中文并标记 |
| 课程原文为英文，输出中文 | `zh`，缺译处回退英文并标记 |

回退是预期行为，不是错误。但 `untranslated_ratio` 超过 30% 时，`bilingual` 输出无实际意义，报 `major`。

---

## 8. 术语表附录

`semester` 与 `review` 作用域默认在书末生成术语表：

```
附录 C 术语表

| 中文 | 英文 | 首次出现 |
|---|---|---|
| 带权路径长度（WPL） | weighted path length | §6.3.1 |
```

排序按拼音（中文）或字母序（英文），不含随机。`origin: synthesized` 的术语默认排除，可通过配置包含。

---

## 9. 与符号的关系

| | 术语 | 符号 |
|---|---|---|
| 表示 | 概念名称 | 数学记号 |
| 存储 | `glossary.yaml` | 知识点的 `symbols` 字段 |
| 冲突检查 | 名称唯一 | 同一符号不得在本文件更早处有不同含义 |
| 渲染 | 按语言替换 | 原样输出 |

符号的定义随知识点走，因为符号通常只在局部有效。术语须全局统一，因此集中于术语表。符号冲突的检查见 [unit-schema.md](unit-schema.md) §11。

---

## 10. 人工维护成本

术语表是唯一需要持续人工维护的结构，因此须控制规模：

- 只登记**会被反复使用**的术语，一次性提及的概念不入表
- 目标规模：每讲新增不超过 8 条
- 术语表变动不触发全书重建，仅触发受影响知识点的渲染（按 `[[term:` 引用建立反向索引）
