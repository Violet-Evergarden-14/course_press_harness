# 知识点结构

> 状态：设计稿 v0.1
> 依赖：[architecture.md](architecture.md) §10
> 本文件定义知识点的字段、正文结构、标识规则与修订协议。所有涉及知识点的程序与提示词均以本文件为准。

---

## 1. 作用

知识点（unit）是文档的基本单元，同时是缓存的单位与审查的对象。三项性质决定了全部字段设计：

1. **身份稳定**：跨讲次、跨版本沿用同一 `id`，否则增量更新与交叉引用都会失效。
2. **证据可查**：每条陈述都能定位到素材中的具体位置（是否呈现给示例主体由渲染配置决定）。
3. **可独立判定**：正文能拆成一条条可验证的断言，供审查逐条判断真伪。

一个知识点只表达一个概念、一条性质、一个算法或一道例题。判定标准：若标题需要用「与」连接两个名词，则应拆为两个知识点。

---

## 2. 存储位置

```
.study/knowledge/
├── units/<id>.md      # 每个知识点一个文件，文件名即 id
└── graph.json         # 依赖边与候选合并清单，由 units 生成，不手工编辑
```

`graph.json` 是派生数据。它被删除后可完全重建，因此不得作为唯一数据源。

---

## 3. 标识

### 3.1 id 规则

```
id = <主题前缀>-<语义 slug>
```

- 字符集：`[a-z0-9-]`，以字母开头，不含下划线
- 主题前缀标识所属知识域（如 `binary-tree`、`huffman`），不使用讲次号——讲次号会因重排而失真
- 全局唯一，**一经发布永不修改**

正例：`binary-tree-layer-max`、`huffman-construct`、`kmp-prefix-func`
反例：`l06-p012`（绑定位置）、`示例结构性质2`（含中文与空格）、`tree-1`（无语义）

### 3.2 重命名

`id` 修改通过别名完成，不直接改名：

```yaml
aliases: [binary-tree-level-bound]   # 旧 id → 新 id 的映射
```

渲染与引用解析时，`aliases` 中的旧 id 会被重定向到当前 id；`dangling_refs` 门禁将未声明的旧 id 视为错误。别名至少保留一个大版本。

---

## 4. 字段定义

```yaml
---
schema_version: 1
id: binary-tree-layer-max
title: 示例结构每层元素数的上界
type: property
evidence:
  - {src: lecture-06.pptx, loc: {slide: 12}, block: s06-012}
  - {src: rec-06.m4a,     loc: {t: [812.5, 845.2]}, block: rec06-0088}
  - {src: my-notes.yaml,  loc: {slide: 12}, kind: emphasis}
depends_on: [binary-tree-def]
see_also: [full-binary-tree-leaves]
symbols:
  - {name: i, meaning: 元素所在层号，根为第 1 层}
  - {name: h, meaning: 示例结构高度}
origin: source
emphasis: true
revision: 3
suspects: [full-binary-tree-leaves]
aliases: []
reviewed_by: [l2, l3]
---
```

### 4.1 必填字段

| 字段 | 类型 | 约束 |
|---|---|---|
| `schema_version` | int | 当前为 1 |
| `id` | string | 符合 §3.1 |
| `title` | string | 中文陈述式，不以问句结尾，不含标点结尾 |
| `type` | enum | 见 §5 |
| `evidence` | array | 至少 1 项；每项须含 `src`、`loc`，`block` 为 IR 块 id（可省略） |
| `origin` | enum | `source` / `derived` / `synthesized`，判定见 §4.3 |
| `revision` | int | 从 1 开始，每次内容变更加 1 |

### 4.2 选填字段

| 字段 | 用途 |
|---|---|
| `depends_on` | 理解本知识点前必须先掌握的知识点；参与前向引用与缺环检测 |
| `see_also` | 相关但非必需的知识点 |
| `symbols` | 本知识点引入的符号及其含义；全书的符号定义来源 |
| `emphasis` | 标记为必考或重点；来源可以是手记或考纲 |
| `suspects` | 新增素材后可能受影响的既有知识点；由编排阶段自动生成 |
| `aliases` | 旧 id，见 §3.2 |
| `reviewed_by` | 已通过的审查层（`l2`、`l3`），附时间戳 |
| `numeric` | 例题与数值断言的复算信息，见 §5.2 |
| `tolerance` | 数值比对的相对误差容忍度，默认 `1e-9` |

### 4.3 origin 的判定

`origin` 描述正文的依据强度，取值：

| 值 | 判定条件 |
|---|---|
| `source` | 正文中所有断言均可由 `evidence` 直接推出 |
| `derived` | 至少一条断言不能直接推出，但可由本书中更早的知识点合理推得 |
| `synthesized` | 至少一条断言既不满足上述两项，属补充内容 |

**取最弱值**：知识点内只要有一条断言属补充内容，整个知识点即标记为 `synthesized`。不允许按段落分别标记——混合标记会使示例主体无法判断哪一段可靠。

`origin` 与 L2 审查的 `verdict` 存在对应关系：`supported` → 允许 `source`，`derived` → 至少 `derived`，`unsupported` → 必须降为 `synthesized` 或删除该断言。两者不一致时视为缺陷，由 `origin_consistency` 检查捕获。

---

## 5. 类型与正文结构

### 5.1 类型枚举

| type | 用于 | 必备小节 |
|---|---|---|
| `concept` | 概念、定义 | 定义、说明、例子 |
| `property` | 性质、定理、公式 | 陈述、依据、用途 |
| `algorithm` | 算法与过程 | 问题、步骤、复杂度、例子 |
| `example` | 完整例题 | 题目、解法、验证、容易出错处 |
| `pitfall` | 易错点、常见误解 | 错误说法、正确说法、原因 |
| `procedure` | 操作流程、证明套路 | 适用条件、步骤、示例 |
| `summary` | 一章或一讲的归纳 | 要点、对照、与前后章的衔接 |

### 5.2 各类型的正文要求

**property**

```markdown
## 陈述

示例结构第 $i$ 层至多有 $2^{i-1}$ 个元素（$i$ 从 1 开始计，根为第 1 层）。

## 依据

第 1 层只有根元素，共 $1 = 2^{0}$ 个。设第 $i$ 层有 $n_i$ 个元素，
每个元素至多贡献两个子元素，故 $n_{i+1} \le 2n_i$。递推得 $n_i \le 2^{i-1}$。

## 用途

估算最坏情况下的元素总数 $S(h) = \sum_{i=1}^{h} 2^{i-1} = 2^{h} - 1$，
这是判定「$N$ 个元素的示例结构至少有多高」的直接依据。
```

**example** 附 `numeric` 字段，供 `numeric_check` 复算：

```yaml
numeric:
  expr: sum(2**(i-1), (i, 1, h))
  substitutions: {h: 5}
  expect: 31
  checked: sympy
```

`expect` 与复算结果不一致时门禁失败。禁止填写「略」「见上」。

### 5.3 小节标题

正文使用 `##` 二级标题划分小节，标题文字必须来自 §5.1 的必备小节集合。程序据此判定结构完整性，并可将小节顺序映射到不同渲染视图（`review` 视图将「陈述」提到最前，「依据」折叠）。

小节标题之后的正文不得为空。空节视为结构缺陷。

---

## 6. 符号与术语

- 本知识点引入的符号必须登记在 `symbols`。
- 正文首次使用某符号时，须在 $i$ 所在知识点或更早处有定义（`undefined_symbols` 门禁）。
- 术语通过 `glossary.yaml` 统一，条目格式：

```yaml
- id: full-binary-tree
  zh: 示例结构 B
  en: full binary tree
  definition_unit: full-binary-tree-def
```

正文引用术语时使用 `[[term:full-binary-tree]]`，渲染为本地化后的名称。禁止同一概念在书中出现两种译名——由 `undefined_terms` 与 L3 表述审查共同捕获。

---

## 7. 交叉引用

| 写法 | 渲染结果 | 用途 |
|---|---|---|
| `[[unit-id]]` | `§3.2` 或 `§3.2 示例结构每层元素数的上界`（按模板配置） | 指向书内知识点 |
| `[[unit-id\|自定义文字]]` | 以自定义文字为显示，保留链接 | 行文中提及 |
| `[[term:term-id]]` | 术语名称 | 术语引用 |
| `[[asset:asset-id]]` | 图片引用 | 素材原样导出的位图 |
| `[[fig:figure-id]]` | 图编号 | 重画或新增的图，格式见 [rendering.md](rendering.md) §9.1 |

规则：

- 只能引用书内对象（`[[...]]`）。引用书外素材一律禁止，属于来源指称，见 architecture.md §6.3。
- 前向引用（指向后续讲次）允许，但渲染时须自动附加「详见」样式的前置标记，且不得搬运内容。
- 自引用与循环引用由 `dangling_refs` 检查捕获。

---

## 8. 图片与资产

- 正文通过 `[[asset:<id>]]` 引用资产，不直接写路径。
- 资产由 IR 阶段导出至 `.study/assets/`，并以内容哈希命名，因此改名不影响引用。
- L2 多模态补充得到的语义（如「边集合」）必须写入该资产在 IR 中的 `semantics` 字段，并在正文中以其为内容基础，而非在正文里直接叙述图片外观。

---

## 9. 禁止事项

| 禁止 | 理由 | 检查 |
|---|---|---|
| 正文出现来源指称（课件、PPT、第 X 页、老师、录音等） | 文档须独立可读；具体策略见 architecture.md §6.3 | `source_reference_policy` |
| 正文出现绝对路径或工作区相对路径 | 产物须可移植 | `path_leak` |
| 引用书外素材 | 同上 | `dangling_refs` |
| 在知识点内嵌入手记原文 | 个人数据与课程内容须隔离 | `patches_stale` |
| 同一知识点内含两个以上「与」级并列概念 | 破坏缓存粒度与增量更新 | 审查（`major`） |
| 以「略」「见上」「同上」代替实质内容 | 文档须自足于本书 | 审查（`major`） |

---

## 10. 修订协议

### 10.1 何时递增 revision

内容发生任何变化时 `revision += 1`：正文、evidence、依赖、符号、类型。仅格式（空白、换行）变化不递增。

`revision` 参与缓存键，因此递增意味着下游渲染必须重跑该知识点。

### 10.2 suspects 的生成

新增一讲后，对既有知识点做两两比较，命中任一条即加入 `suspects`：

- `title` 与新增知识点的高相似度（字符 n-gram 重合超过阈值）
- 共享 `depends_on` 且正文主题词重合
- 同一 `src` 中 `loc.slide` 相邻

`suspects` 只是提示，不自动修改内容。人工确认后由 `review` 流程处理：合并、拆分或标记为无关。

### 10.3 合并与拆分

**合并**：保留一个 `id`，另一个降为 `aliases`，正文合并后重跑审查，`revision` 加 1。

**拆分**：为新知识点分配新 `id`，原 `id` 保留给语义上更主要的那个，`aliases` 不用于拆分。引用原 `id` 的位置需人工确认指向。

两种操作均须在提交信息中说明理由，并保留操作前的 `revision` 记录。

### 10.4 与 patches 的关系

手记与按需补丁永不写回知识点文件。渲染时按以下优先级叠加：

```
知识点正文  <  patches/notes.yaml（correction 类）  <  patches/remedial/（按需补丁，插入而非覆盖）
```

`correction` 覆盖时，被覆盖段落保留在原文件中，渲染器跳过并替换；因此删除手记后原文自动恢复。

---

## 10.5 草稿状态

确定性抽取无法生成解释性内容，只能搬运原文。此类产物标记 `draft: true`。

| 影响 | 说明 |
|---|---|
| `id` | 无法从中文标题生成语义 slug，退化为 `auto-<hash>`；`auto_unit_id` 检查报 `major` |
| 必备小节 | 缺失时报 `draft_sections`（`major`），而非 `blocker` |
| 内容 | 只含原文条目、公式、图片引用，`origin: source`，不含解释 |
| 发布 | 含 `draft: true` 的知识点不得视为成稿；渲染不因草稿而失败，但门禁指标会持续显示 |

草稿的目的是让无模型的管道能跑通并暴露结构问题。移除 `draft: true` 表示已人工或经模型补全。

**已知限制**：草稿 id 由标题哈希生成，若两份素材出现同名标题，后者获得 `-2` 后缀。改标题会更换 id，因此草稿 id 不是稳定身份——稳定身份从人工命名开始。

---

## 11. 校验规则汇总

| 规则 | 层 | 严重度 |
|---|---|---|
| 必填字段齐全、`schema_version` 兼容 | L1 | blocker |
| `id` 符合 §3.1 且全局唯一 | L1 | blocker |
| `evidence` 非空且 `loc` 形态合法 | L1 | blocker |
| `type` 的必备小节齐全且非空 | L1 | blocker |
| `numeric.expect` 与复算一致 | L1 | blocker |
| 符号在更早处有定义 | L1 | blocker |
| 交叉引用可解析且无环 | L1 | blocker |
| `origin` 与 L2 `verdict` 一致 | L1 | major |
| 知识点长度超过 1500 字（建议拆分） | L1 | minor |
| 一知识点含并列概念 | L3 | major |
| 存在无实质内容的占位表述 | L3 | major |

`schema_version` 不兼容时，程序须拒绝加载并提示迁移，不得猜测字段含义。

---

## 12. 完整示例

```markdown
---
schema_version: 1
id: huffman-construct
title: 哈夫曼树的构造方法
type: algorithm
evidence:
  - {src: lecture-06.pptx, loc: {slide: 31}, block: s06-031}
  - {src: rec-06.m4a, loc: {t: [2410.0, 2663.5]}, block: rec06-0412}
depends_on: [binary-tree-def, heap-def]
see_also: [huffman-code-length]
symbols:
  - {name: W, meaning: 权值集合，按非递减序排列}
origin: source
revision: 1
---

## 问题

给定 $n$ 个权值 $W = \{w_1, \dots, w_n\}$，构造一棵带权路径长度（WPL）最小的示例结构，
其中每个权值对应一个叶元素。

## 步骤

1. 将 $n$ 个权值各自作为单元素树，组成示例集合 $F$。
2. 从 $F$ 中取出根权值最小的两棵树，作为左右子结构合并为新树，新根权值为两者之和。
3. 将新树放回 $F$。
4. 重复步骤 2、3，直至 $F$ 中只剩一棵树。

## 复杂度

使用小根堆维护 $F$，每次取最小、插入各为 $O(\log n)$，
共执行 $n-1$ 轮，总时间 $O(n \log n)$。

## 例子

权值 $\{1, 2, 3, 4\}$：

合并 $1,2 \to 3$；示例集合 $\{3,3,4\}$；
合并 $3,3 \to 6$；示例集合 $\{4,6\}$；
合并 $4,6 \to 10$。WPL $= 1\cdot3 + 2\cdot3 + 3\cdot2 + 4\cdot1 = 19$。
```

注意示例中未出现任何来源指称，且「依据」全部可由证据推断，因此 `origin` 为 `source`。
