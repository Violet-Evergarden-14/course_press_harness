# 审查协议

> 状态：设计稿 v0.1
> 依赖：[architecture.md](architecture.md) §13、§14、§16；[unit-schema.md](unit-schema.md)
> 本文件定义审查任务的输入输出、findings 格式、抽样与成本预算、应用流程。

---

## 1. 职责划分

| 层 | 执行者 | 依据 | 能否阻断 | 输出 |
|---|---|---|---|---|
| L1 结构检查 | 程序 | 确定性规则 | 能 | 退出码与错误列表 |
| L2 证据支撑审查 | 模型 | 该知识点的 `evidence` | 否 | findings |
| L3 独立对抗审查 | 另一个模型或干净上下文 | 成品正文 + 证据 | 否 | findings |
| 人工复核 | 人 | findings 与自己的判断 | 能 | 应用或拒绝 |

**核心约束：任何审查都不修改知识点文件。** 审查只读，产出 findings；修改须经 `study review apply` 并记录来源。

不设制的检查：不存在「自审」层。同一模型携带写作上下文检查自身产物不视为审查，其结果不得写入 `reviewed_by`。

---

## 2. findings 格式

`.study/review/findings.jsonl`，每行一条：

```json
{
  "id": "f-20261ef3-0042",
  "level": "blocker",
  "category": "unsupported_claim",
  "unit": "binary-tree-layer-max",
  "claim_idx": 3,
  "message": "断言无证据支撑，且与 u-full-binary-tree-leaves 的陈述矛盾",
  "evidence": [{"src": "lecture-06.pptx", "loc": {"slide": 12}}],
  "suggestion": {"action": "delete_claim", "target": "claim_idx:3"},
  "detected_by": "l2",
  "detector": "claude-code/opus",
  "prompt_version": 3,
  "created_at": "2026-01-01T00:00:00Z",
  "status": "open"
}
```

### 2.1 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 是 | `f-<doc_hash>-<序号>`，同一文档内稳定 |
| `level` | 是 | `blocker` / `major` / `minor` / `info` |
| `category` | 是 | 见 §2.2 |
| `unit` | 是 | 知识点 id；跨知识点的问题取主要涉事方 |
| `claim_idx` | 否 | 断言的序号，从 0 开始，按正文出现顺序 |
| `message` | 是 | 一句话说明问题，须可读懂而无需翻原文 |
| `evidence` | 否 | 支撑该问题判断的证据位置 |
| `suggestion` | 否 | 机器可执行的操作，见 §2.3 |
| `detected_by` | 是 | `l1` / `l2` / `l3` / `human` |
| `detector` | 否 | 模型标识或人工标识 |
| `prompt_version` | 否 | 用于缓存与回归比对 |
| `status` | 是 | `open` / `accepted` / `rejected` / `wontfix` / `stale` |

### 2.2 category

| category | 层 | 含义 |
|---|---|---|
| `figure_content` | L3 | 图中的结构、文字或方向与正文冲突 |
| `unsupported_claim` | L2 | 断言无法由证据支撑 |
| `origin_mismatch` | L2 | `origin` 与 verdict 不一致 |
| `contradiction` | L2/L3 | 与其他知识点矛盾 |
| `missing_step` | L3 | 推导缺少关键步骤 |
| `numeric_mismatch` | L1 | 复算结果不一致 |
| `ambiguous_wording` | L3 | 表述会导致误解 |
| `term_inconsistent` | L3 | 同一概念两种叫法 |
| `over_merged` | L3 | 一个知识点混入两个概念 |
| `placeholder_text` | L3 | 以「略」「见上」代替内容 |
| `structure` | L1 | 结构检查失败 |

### 2.3 suggestion

`suggestion` 必须是枚举动作，不接受自由文本指令：

| action | 参数 | 含义 |
|---|---|---|
| `delete_claim` | `target` | 删除指定断言 |
| `mark_synthesized` | `target` | 将知识点 origin 降为 synthesized |
| `split_unit` | `into` | 拆分为若干知识点（需人工补全 id） |
| `merge_unit` | `with` | 与另一知识点合并 |
| `rewrite_claim` | `target`, `text` | 改写断言（须人工确认措辞） |
| `reorder_section` | `unit`, `section` | 调整位置 |
| `none` | — | 仅提示，无自动动作 |

---

## 3. 严重度

| level | 判据 | 处理 |
|---|---|---|
| `blocker` | 会导致学习者形成错误理解，或使文档不可渲染 | 阻塞发布，必须处理 |
| `major` | 内容有缺陷但读者仍能学到正确内容 | 应在发布前处理 |
| `minor` | 表述或结构可改进 | 可延后 |
| `info` | 提示与建议 | 仅记录 |

`blocker` 的判定从严：只有「陈述为假」或「结构无法解析」才可归入。表述不佳一律为 `major` 或以下，避免审查把大量内容卡在队列里。

---

## 4. L2：证据支撑审查

### 4.1 任务生成

每个知识点生成一个缺口文件（architecture.md §16）：

```json
{
  "id": "g-l2-binary-tree-layer-max",
  "kind": "claim_entailment",
  "unit": "binary-tree-layer-max",
  "input": {
    "claims": ["..."],
    "evidence": [{"src": "...", "loc": {...}, "text": "..."}],
    "book_context": ["..."]
  },
  "instruction_version": 3
}
```

- `claims`：由程序按段落实体切分，不做语义改写
- `evidence`：该知识点全部证据的**原文**，不含任何生成内容
- `book_context`：本书更早知识点的标题与结论摘要，用于判断 `derived`

### 4.2 输出要求

```json
{
  "id": "g-l2-binary-tree-layer-max",
  "verdicts": [
    {"claim_idx": 0, "verdict": "supported", "quote": "第 i 层至多有 2^(i-1) 个结点"},
    {"claim_idx": 1, "verdict": "derived", "reason": "由「每个结点至多两个子结点」递推可得"},
    {"claim_idx": 2, "verdict": "unsupported", "reason": "证据中无对应陈述"}
  ],
  "by": "claude-code/opus",
  "confidence": "high"
}
```

硬性要求：

- 只输出 verdict，**不得输出改写后的正文**
- `quote` 必须是原断言的逐字片段，用于校验切分是否对齐
- `unsupported` 必须给出 `reason`，否则视为无效输出并重试一次

### 4.3 实现映射（v0.1）

| 文档概念 | 命令 / 模块 |
|---|---|
| 任务生成 | `study review prepare [--unit ID] [--limit N]` → `.study/gaps/l2-<unit>.json` |
| 答案填充 | `study gaps fill l2-<unit> --file answer.json` |
| 校验与回流 | `study review apply` → findings + 重算 `origin` |
| 指标 | `study review stats` → `unsupported_ratio` / `derived_ratio` |

两条约束在实现中必须成立：

1. `apply` 只写元数据（`origin`、`reviewed_by`）与 findings，**不改正文**——已由测试断言。
2. `origin` 从答案推导而非固化。知识点是草稿时会被 `build` 重新生成，若把结论写死在文件里就会被抹掉；因此每次 `build` 都按已保存的答案重算 `origin`，且只改元数据时**不递增 `revision`**（比较正文指纹，而非整份文件哈希）。

### 4.4 校验与回流

1. 程序校验 `quote` 与原断言一致，不一致则报 `info` 并丢弃该条
2. `unsupported` 比例写入统计；知识点的 `origin` 按 [unit-schema.md](unit-schema.md) §4.3 重新计算
3. `origin` 与 verdict 不一致时产出 `origin_mismatch` findings（`major`），不自动改

---

## 5. L3：独立对抗审查

### 5.1 隔离要求

| 要求 | 实现 |
|---|---|
| 不与作者共享上下文 | 新会话，输入仅含成品正文与证据 |
| 不提供写作过程 | 不传追问记录、不传草稿 |
| 不同模型优先 | 配置项 `review.l3.model`，默认与生成模型不同 |
| 不附审查者身份 | 不告诉审查者「这是 AI 生成的」，避免其偏向性判断 |

### 5.1.1 图的审查

对 `provenance` 为 `redrawn` 或 `synthesized` 的图，审查任务单独一条：

```
以下是正文中的一段讲解，以及它为辅助理解而绘制的图（mermaid/svg 源码）。
只回答：
1. 图中的元素、连线方向、标签文字是否与正文一致？
2. 图中是否出现正文未定义的术语或符号？
3. 若要照此图作答，会不会得出与正文相反的结论？
```

审查结论写入 `figure_content` findings。审查通过后由人工将图元数据的 `reviewed` 置为 `true`——**审查器不自行修改元数据**，与知识点同一条约束。

### 5.2 任务模板

固定三问，禁止扩展：

```
以下是一份课程讲义的一个知识点，以及它依据的原始素材片段。
请找出会导致学习者形成错误理解的地方，只回答以下三项：
1. 它与下列其他知识点是否矛盾？（列出对立的陈述）
2. 它的推导或步骤是否缺少关键环节？（指出缺在哪一步之后）
3. 它的例子能否实际算出与结论一致的结果？（若不能，给出正确结果）

不要风格建议，不要补充内容，不要重写。
```

### 5.3 输出映射

| 回答 | category | level |
|---|---|---|
| 发现矛盾 | `contradiction` | blocker（若两陈述同为真值断言）或 major |
| 缺少步骤 | `missing_step` | major |
| 例子算错 | `numeric_mismatch` | blocker |
| 无可指摘 | 无 finding，仅在 `reviewed_by` 记录 `l3` | — |

---

## 6. 抽样与成本预算

### 6.1 必审对象

| 条件 | 层 |
|---|---|
| `type: example` 或含 `numeric` | L1 复算 + L3 |
| 含 `formula` 块 | L1 往返 + L3 |
| `origin: synthesized` | L2 + L3 |
| 被手记标记为 `question` | L2 + L3 |
| `emphasis: true` | L2 |
| 证据仅来自单一来源 | L2 |
| 引用 `provenance: synthesized` 的图 | L3 图内容审查 |

### 6.2 抽样

其余知识点按 `10%` 随机抽样，采样种子固定为 `sha256(workspace 名 + pipeline 版本)`，保证同一工作区重复运行抽到同一批。

### 6.3 每讲预算

| 项 | 上限 |
|---|---|
| L2 任务数 | 每讲 40 个 |
| L3 任务数 | 每讲 15 个 |
| 单任务输入 | 20000 字符（超出则按段落拆分） |

超出预算时按 `level 预期严重度 × emphasis × 证据薄弱度` 排序，取前 N。排序函数须确定，不含随机。

### 6.4 缓存

缓存键：`sha256(unit 内容 + 输入证据哈希 + prompt_version + 模型标识)`。命中直接复用，不重复调用。修改 prompt 时须递增 `prompt_version`。

---

## 7. 去重与合并

同一问题可能被 L2、L3 各报一次。去重键：

```
dedupe_key = unit + category + claim_idx + normalized_message
```

`normalized_message` 为去除标点、空白与数字后的字符集小写形式。命中去重键时保留 `level` 较高者，`detected_by` 合并为数组。

跨层冲突处理：L2 判 `supported` 而 L3 报 `contradiction` 时，不自动取舍，产出 `info` 记录双方依据，交人工判断。

---

## 8. 人工复核与应用

### 8.1 复核队列

`review/findings.md` 由 findings 生成，按 `level` 与涉及知识点在文档中的位置排序：

```
## blocker (2)

- [f-...-0042] binary-tree-layer-max（§6.2.3）
  断言 3 无证据支撑，且与 §6.2.5 的陈述矛盾
  建议：delete_claim → claim_idx:3
  证据：lecture-06.pptx slide 12

## major (7)
...
```

该文件是工作区中唯一允许出现定位符的地方，不属于交付文档。

### 8.2 命令

| 命令 | 作用 |
|---|---|
| `study review list [--level] [--unit]` | 列出 findings |
| `study review accept <id>` / `reject <id>` | 标记状态 |
| `study review apply` | 应用所有 `accepted` 且带可执行 `suggestion` 的 findings |
| `study review stats` | 输出指标，见 §9 |

`apply` 的行为约束：

1. 仅处理 `status: accepted` 且 `suggestion.action != none`
2. `split_unit` / `rewrite_claim` 需要人工提供参数，`apply` 拒绝无参数执行
3. 每次修改：`revision += 1`，`state.json` 记录 `{source: "review", finding_id}`
4. 修改后使该知识点的全部 findings 置为 `stale`（除本次应用的）
5. 修改后重跑 L1 门禁，失败则回滚并报错

### 8.3 不得自动处理的情形

| 情形 | 处理 |
|---|---|
| `contradiction` | 人工判断哪一方正确，`apply` 不处理 |
| `over_merged` | 拆分涉及 id 命名，人工执行 |
| `term_inconsistent` | 涉及 glossary 全局一致性，人工决定标准译名 |
| 同一知识点 5 条以上 findings | 提示整体重写，而非逐条修补 |

---

## 9. 指标

`study review stats` 输出：

| 指标 | 定义 | 用途 |
|---|---|---|
| `unsupported_ratio` | `unsupported` 断言 / 总断言 | 反映生成内容是否有依据 |
| `derived_ratio` | `derived` 断言 / 总断言 | 反映补充内容是否超出素材范围 |
| `findings_per_unit` | findings 数 / 知识点数，按 level 分列 | 反映质量趋势 |
| `fix_rate` | `accepted` 且已应用 / 总数 | 反映审查是否有效 |
| `unconfirmed_align_ratio` | 未确认对齐项 / 待确认项 | 反映对齐可信度 |
| `blocker_age` | blocker 从创建到解决的天数 | 反映流程是否卡住 |

`unsupported_ratio` 是核心指标：显著升高说明生成阶段在脱离证据，应回查提示词或 `book_context` 的构造。

---

## 10. 与 CI 的关系

| 检查 | 触发 | 是否阻断 |
|---|---|---|
| L1 门禁 | 每次提交、每次构建 | 是 |
| L2 / L3 审查 | 手动或定时任务 | 否 |
| `blocker` findings 存在 | 发布前检查 | 是 |
| `unsupported_ratio > 0.2` | 发布前检查 | 是 |

CI 中禁止调用模型：CI 只跑 L1。语义审查在构建产物之外进行，结果随构建产物一同归档。

---

## 11. 禁止事项

| 禁止 | 理由 |
|---|---|
| 审查结果自动回写知识点 | 不可复现；无法区分陈述来源 |
| 用生成同一内容的模型自审并计入 `reviewed_by` | 自证无意义 |
| 让审查器输出改写正文 | 会把审查变成第二次生成，责任无法归属 |
| 把 findings 中的定位符渲染进交付文档 | 违反来源指称策略 |
| 静默丢弃 `unsupported` | 指标失真，风险被隐藏 |
| 在 CI 中运行语义审查 | 结果不确定且成本不可控 |
