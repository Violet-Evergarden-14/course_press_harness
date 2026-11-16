# 渲染

> 状态：设计稿 v0.1
> 依赖：[architecture.md](architecture.md) §12、[unit-schema.md](unit-schema.md)、[outline-generation.md](outline-generation.md)
> 本文件定义模板体系、视图差异、补丁叠加顺序与输出确定性要求。

---

## 1. 边界

渲染是**纯确定性装配**：读取知识点、骨架、模板与补丁，输出文档。该阶段：

- 不调用模型
- 不修改任何输入文件
- 相同输入与相同版本产生逐字节相同的产物
- 不生成时间戳、不写入构建机信息、不排序不确定的集合

任何需要语言生成的工作都不在本阶段。译文、按需补丁、改写均属上游产物。

---

## 2. 输入

```
render(units, outline, patches, glossary, template_set, options)

options = {
  scope:  unit | lecture | lectures | chapter | semester,
  profile: learn | review,
  lang:   zh | en | bilingual,
  source_references: none | footnote | inline,
  format: md | tex | html,
}
```

| 输入 | 说明 |
|---|---|
| `units` | `.study/knowledge/units/*.md` |
| `outline` | 骨架与编号来源 |
| `patches` | `patches/notes.yaml`、`patches/remedial/`、`patches/i18n/` |
| `glossary` | 术语表，见 [glossary.md](glossary.md) |
| `template_set` | `templates/<profile>/<format>/`，以哈希参与缓存 |

---

## 3. 模板体系

模板是数据，不是程序。更换模板不得修改代码。

### 3.1 目录

v0.1 实现的是 **manifest 模式**（布局即数据），文件级模板为后续阶段：

```
templates/
└── learn/
    └── md/
        └── manifest.yaml        # 标题格式、节顺序、折叠规则
```

```yaml
version: 1
document:
  title: "# {title}"
  toc_heading: "## 目录"
  toc_part: "- 第 {label} 讲 {title}"
  toc_section: "  - {label}.{index} {title}"
  toc_unit: "    - {number} {title}"
part:
  lecture: "# 第 {label} 讲 {title}"
section:
  heading: "## {label}.{index} {title}"
unit:
  heading: "### {number} {title}"
  section_label: "**{name}**"
  omit_heading_if_same: true
section_order:
  learn: [定义, 陈述, 说明, ...]
  review: [陈述, 定义, 要点, ...]
```

`{...}` 占位符由渲染器填充，未声明的键回退到内置默认值。manifest 的哈希参与缓存键（`template_hash`）并写入 `meta.json`，因此换模板必然重建。

文件级模板（每个层级一个 `.md` 骨架 + 块级模板）留待后续；届时 manifest 只负责声明层级文件的位置，不改动本节其余约束。

### 3.2 占位符语法

使用最小占位符，不引入通用模板引擎：

| 语法 | 含义 |
|---|---|
| `{{unit.title}}` | 字段取值，按类型转义 |
| `{{unit.body}}` | 已渲染的正文 |
| `{{number}}` | 当前对象编号，由渲染器注入 |
| `{{toc}}` | 目录 |
| `{{unit.section:陈述}}` | 取正文中某小节的渲染结果 |
| `{{#if emphasis}}...{{/if}}` | 条件块，仅支持字段存在性与真假 |
| `{{#each see_also}}...{{/each}}` | 遍历，元素内可用 `{{this.number}}` `{{this.title}}` |

不支持表达式、函数调用、嵌套模板引用。需要更复杂逻辑时，应在数据层准备字段，而不是增强模板。

### 3.3 分层职责

| 层 | 负责 | 不得包含 |
|---|---|---|
| `document` | 文档标题、目录、章节顺序、附录 | 知识点内容 |
| `part` | 讲次标题、编号、该讲小节列表 | 具体小节内容 |
| `section` | 节标题、编号、知识点顺序 | 知识点正文 |
| `unit` | 知识点标题、编号、小节结构、交叉引用 | 其他知识点的内容 |
| `blocks` | 公式、表格、图片、代码的排版 | 业务判断 |

---

## 4. 视图差异

`learn` 与 `review` 使用同一批知识点，差异完全由数据表达：

```yaml
# templates/<profile>/md/manifest.yaml
section_order: [陈述, 依据, 用途, 例子, 易错]      # learn
# section_order: [陈述, 易错, 用途, 依据, 例子]    # review（数据前置）

include_units:
  review:
    emphasis_only: false        # true 时仅输出 emphasis 与 pitfall
collapse: [依据, 例子]           # review 下折叠为一行摘要
appendices: [公式速查, 术语表]     # review 默认附加
```

### 4.1 附录的生成

附录在正文之后统一生成，不由知识点各自输出——同一张公式表在五讲里各出现一次就是噪音。

| 附录 | 条件 | 内容 |
|---|---|---|
| 重点清单 | 仅 `review` | `emphasis: true` 的知识点，以及手记中 `kind: emphasis` 的条目 |
| 公式速查 | 两种视图 | 全书所有知识点的「算式」小节，按文档顺序分组 |

附录标题与条目格式同样由 manifest 提供（`appendix.focus_heading` 等），不写死在代码里。

约束：

- 视图不得改变知识点的 `id`、`origin`、`evidence`
- 视图不得删除知识点内容，只能调整顺序或折叠；折叠时仍须输出完整内容的可展开形式（Markdown 用 `<details>`）

---

## 5. 编号注入

编号不在数据中，由渲染器依 outline 顺序计算并注入占位符：

| 对象 | 注入值 |
|---|---|
| 讲次 | `n`（附录为 `A`、`B`） |
| 节 | `n.m` |
| 知识点 | `n.m.k` |
| 图 / 表 | `n.m.k-1` 起，按出现顺序 |
| 公式 | `(n.m.k)` |
| 定理类 | `n.m.k` |

交叉引用在渲染时解析：`[[unit-id]]` → `§n.m`，`[[unit-id|文字]]` → `文字`（带链接或页码）。渲染器须保证同一 `unit-id` 在全文的编号一致，出现不一致即失败。

---

## 6. 跨作用域渲染

| `scope` | 行为 |
|---|---|
| `unit` | 单个知识点，编号退化为 `1`，交叉引用改为「见《标题》」 |
| `lecture` | 一讲，含前言与目录 |
| `lectures` | 指定若干讲，按 outline 顺序，编号延续 |
| `chapter` | 一个 part |
| `semester` | 全书，含前言、全部讲次、复习课章、附录 |

非 `semester` 作用域下的交叉引用若指向范围外知识点，须降级为「见《标题》」并保留该知识点标题，不得输出悬空的 `§` 编号。

---

## 7. 补丁叠加

叠加顺序固定，后写者覆盖前者：

```
1. 知识点正文
2. patches/notes.yaml 中 kind: correction 的条目（覆盖指定段落）
3. patches/notes.yaml 中 kind: emphasis 的条目（追加到重点清单）
4. patches/remedial/<slug>.md（插入到目标知识点之前）
5. patches/i18n/<unit>.<lang>.md（仅 bilingual/en 模式，替换正文）
```

### 7.1 correction 的定位

```yaml
- at: {unit: binary-tree-def, section: 定义}
  kind: correction
  text: 空树的高度定义为 -1，而非 0。
```

- 定位到小节时，整节替换
- 未能定位到小节（原文不存在该节）时，追加到正文末尾并记 `warning`
- 被覆盖的原文保留在知识点文件中，渲染时跳过。删除手记后原文恢复

### 7.2 隔离要求

补丁永不写入 `units/`。个人手记与按需补丁不得出现在公开产物中，因此：

- `--exclude-patches` 用于生成可分享版本
- 发布检查中，若产物含补丁内容且未显式允许，报 `blocker`

---

## 8. 多语言

| 模式 | 行为 |
|---|---|
| `zh` / `en` | 模板字符串取对应语言；正文使用知识点原文；术语取 glossary 对应字段 |
| `bilingual` | 正文按段并列；缺少译本时回退原文并标记 `[未译]` |

渲染器**不翻译**正文。译文来源：

- `patches/i18n/<unit>.<lang>.md`，由模型生成后人工确认
- 缺失时按上表回退，绝不现场翻译

此约束保证渲染的确定性与可复现性。译文的生成属于内容生产，走审查流程。

---

## 9. 资产与图形

- `[[asset:<id>]]` 渲染为图片引用（素材原始截图），编号与图注由渲染器注入
- `[[fig:<id>]]` 渲染为图，按 §9.1 的格式分派
- 路径统一为相对产物目录，禁止绝对路径与工作区路径（`path_leak` 门禁）
- 图或资产缺失时报 `blocker`，不静默跳过
- 表格超过阈值列数（默认 8）时，`md` 转为附件式排版，避免窄屏溢出

### 9.1 三种来源的渲染方式

图的元数据（`.study/figures/<id>.json`）记录 `provenance` 与 `format`，渲染完全由数据决定：

| provenance | format | 渲染 |
|---|---|---|
| `extracted` | png / jpg | 复制到 `out/book.assets/`，输出 `![](...)` |
| `redrawn` | mermaid | 原样输出 ` ```mermaid ` 代码块，由宿主平台渲染 |
| `redrawn` | dot | 输出 ` ```dot ` 代码块；无渲染器时降级为预渲染 svg |
| `redrawn` | svg | 复制到 `out/book.assets/`，输出 `![](...)` |
| `redrawn` | tikz | 输出 ` ```latex ` 代码块（`tex` 输出格式下直接嵌入） |
| `synthesized` | 同上 | 同上，另在门禁中提示待复核 |

规则：

- **文本矢量图不得被复制为二进制资产**。mermaid、dot、tikz 直接进产物，保证 `git diff` 可读。
- 位图只允许 `extracted` 使用；`redrawn` / `synthesized` 使用位图时，`figure_provenance` 报 `major`（模型画位图等于放弃可复核性）。
- 图题（`caption`）由图元数据提供，不由正文重复书写。
- 图编号由渲染器依出现顺序注入，与知识点编号绑定（`n.m.k-1`）。

### 9.2 图形缺口

正文尚未有图、但缺图会妨碍理解时，写 `[[fig:<id>]]` 并生成 `figure_request` 缺口：

```json
{
  "id": "fig-tree-shape",
  "kind": "figure_request",
  "unit": "binary-tree-def",
  "provenance": "synthesized",
  "description": "画出示例结构的五种基本形态",
  "answer_format": {"format": "mermaid", "content": "str", "caption": "str"},
  "rules": ["优先文本矢量格式", "图中文字使用术语表写法", "不得在图中标注出处"]
}
```

答案写入 `.study/figures/<id>.mmd` 及同名 `.json` 元数据，`study render` 自动接入。未填缺口时 `[[fig:...]]` 渲染为空，`figure_refs` 报 `blocker`——缺图不能静默消失。

---

## 10. 输出产物

```
.study/out/
├── book.md                 # semester 作用域
├── book.assets/            # 图片，内容哈希命名
├── lecture-06.md           # lecture 作用域
└── meta.json               # 构建信息，见 §10.2
```

### 10.1 确定性要求

- 产物正文**不得**包含生成时间、主机名、绝对路径、随机顺序
- 集合遍历顺序一律按 outline 顺序；无顺序依据时按字典序
- 连续渲染两次，正文逐字节一致（`meta.json` 除外）

### 10.1.1 输出格式

一本书，多种写法。格式分两类，区别要在文档里说清楚，否则用户会以为 pptx 和书等价：

| 格式 | 类别 | 说明 |
|---|---|---|
| `md` | 书形态 | 默认；连续文档，可被 Obsidian / Git 直接消费 |
| `tex` / `pdf` | 书形态 | `ctexbook` 排版，有目录、页码、页眉，适合打印 |
| `txt` | 派生 | 去标记的纯文本 |
| `docx` | 派生 | 标题层级 + 表格 + 图片，方便交给需要 Word 的场合 |
| `pptx` | 派生，**有损** | 每个知识点一页；被截断的正文完整写入**备注页**，不静默丢弃 |

```bash
study render --format pdf          # 单种
study render --format all          # 全部
```

`--format all` 依次产出 md / txt / tex / pdf / docx / pptx。缺引擎或缺依赖时**只跳过该格式**并说明原因，其余照常产出。

### 10.2 meta.json

构建信息与正文分离，避免污染版本差异：

```json
{
  "pipeline_version": "0.1.0",
  "profile": "learn",
  "scope": "semester",
  "lang": "zh",
  "source_references": "none",
  "template_hash": "sha256:...",
  "unit_count": 128,
  "generated_at": "2026-01-01T00:00:00Z"
}
```

---

## 11. 渲染后校验

渲染完成后立即执行，任一失败即视为构建失败：

| 检查 | 说明 |
|---|---|
| `source_reference_policy` | 按 §6.3 策略检查产物正文 |
| `path_leak` | 无绝对路径、无 `.study/` 路径 |
| `undefined_symbols` | 符号在首次使用前有定义 |
| `undefined_terms` | 术语已登记或就地定义 |
| `dangling_refs` | 所有交叉引用可解析 |
| `numbering_consistency` | 同一对象编号唯一且一致 |
| `asset_exists` | 所有引用的资产存在 |
| `patch_leak` | 未排除补丁时产物不含个人内容 |
| `render_idempotent` | 连续两次渲染一致（CI 中执行） |

---

## 12. 失败处理

| 情况 | 处理 |
|---|---|
| 模板缺少声明字段 | 立即失败，指出模板文件与占位符 |
| 模板含未声明占位符 | 立即失败 |
| 知识点缺必备小节 | 由 L1 门禁拦截，渲染不兜底 |
| 补丁定位失败 | `warning`，追加至末尾 |
| 译文缺失（bilingual） | 回退原文并标记 |
| 编号冲突 | 立即失败 |

渲染器不得为让产物生成成功而静默修正数据。任何「修复」都必须表现为上游数据或模板的修改。

### 12.1 排版可 DIY（LaTeX）

LaTeX 的版式完全由 `templates/<profile>/tex/manifest.yaml` 决定：

```yaml
latex:
  preamble: |            # 整份导言区，可整段换成你自己的样式
    \documentclass[11pt]{ctexbook}
    ...
  chapter: "\\chapter{%s}"
  section: "\\section{%s}"
  subsection: "\\subsection{%s}"
  titlepage: ""          # 留空使用内置封面
```

- 换 `preamble` 即可换字号、行距、页边距、字体、页眉页脚与章节样式，**不需要改渲染器**。
- 想要「某份 PDF 的观感」时，把那份 PDF 的排版特征（字号、行距、页边距、章节层级）写进 preamble 即可。
- markdown 与 LaTeX 使用**各自的 manifest**（`templates/<profile>/md` 与 `templates/<profile>/tex`），网页版式与打印版式可分别演进。
- 缺引擎时只跳过 pdf，`.tex` 仍会写出，不虚构产物。

---

## 13. 模板版本化

- `template_hash` 参与缓存键与 `meta.json`
- 模板变化导致整本书重建，这是预期行为
- 模板须自带 `version` 字段；同一 profile 下，`version` 变化时提示用户检查产物结构差异
- 模板由数据驱动，因此可作为 fixture 参与测试：给定固定 units，断言产物逐字节一致

---

## 14. 测试要求

| 用例 | 断言 |
|---|---|
| 单知识点、单讲、整学期 三种作用域 | 编号正确、交叉引用可解析 |
| `learn` 与 `review` 同批输入 | 内容集合一致，顺序不同 |
| 含 correction 手记 | 对应段落被替换，原文保留在 units 中 |
| `--exclude-patches` | 产物不含个人内容 |
| `source_references` 三档 | 分别通过对应策略检查 |
| 连续渲染两次 | 正文逐字节一致 |
| 模板缺字段 | 输出明确错误，不产生部分产物 |
