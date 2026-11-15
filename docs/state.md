# 状态与缓存

> 状态：设计稿 v0.1
> 依赖：[architecture.md](architecture.md) §5
> 本文件定义 `state.json` 结构、缓存键、失效规则与用户数据的保护边界。

---

## 1. 目标

| 要求 | 判定 |
|---|---|
| 增量 | 改一份素材只重跑受影响阶段 |
| 可续跑 | 任意阶段中断后可继续 |
| 可重建 | 派生数据删除后能完全恢复 |
| 用户数据不丢 | 手记、审阅结论、手工骨架位置永不被清理覆盖 |

---

## 2. 数据分类

这是本文件最重要的一节：`.study/` 中的文件分为两类，处理方式完全不同。

| 类别 | 内容 | 可删除 | 可重建 | 需备份 |
|---|---|---|---|---|
| **派生数据** | `sources/`、`assets/`、`aligned/`、`gaps/answers/`、`knowledge/graph.json`、`out/` | 是 | 是 | 否 |
| **内容产物** | `knowledge/units/` | 需确认 | 部分（重跑会重新生成，但人工修改会丢失） | 是 |
| **用户数据** | `patches/`、`review/findings.*`、`outline.json` 的人工修改、`inputs/` | 否 | 否 | 是 |

约束：

- `study clean` 默认只清理派生数据
- 清理内容产物须 `--include-units` 并二次确认
- 用户数据永不自动清理；`clean --include-user-data` 需显式指定并二次确认

`inputs/` 永不被写入。任何命令不得修改原始素材。

---

## 3. state.json 结构

```json
{
  "schema_version": 1,
  "pipeline_version": "0.1.0",
  "workspace_id": "sha256:...",
  "stages": {
    "ingest": {
      "status": "done",
      "started_at": "2026-01-01T00:00:00Z",
      "finished_at": "2026-01-01T00:00:05Z",
      "key": "sha256:...",
      "outputs": [".study/sources/pptx-lecture-06.jsonl"],
      "warnings": 3,
      "errors": 0
    },
    "probe":   {"status": "done",    "key": "sha256:..."},
    "align":   {"status": "skipped", "reason": "tier C"},
    "units":   {"status": "partial", "key": "sha256:...", "pending_gaps": 12},
    "outline": {"status": "done",    "key": "sha256:..."},
    "render":  {"status": "stale",   "reason": "units 已更新"},
    "gates":   {"status": "failed",  "gate": "undefined_symbols"}
  },
  "inputs": {
    "lecture-06.pptx": {"sha256": "...", "size": 18442112, "mtime": "..."},
    "rec-06.m4a":      {"sha256": "...", "size": 88231200, "mtime": "..."}
  },
  "config_hash": "sha256:...",
  "template_hash": "sha256:...",
  "modifications": [
    {"at": "2026-01-01T10:00:00Z", "source": "review", "finding_id": "f-...-0042", "unit": "binary-tree-layer-max", "revision_from": 3, "revision_to": 4},
    {"at": "2026-01-01T11:00:00Z", "source": "human", "unit": "huffman-construct", "revision_from": 1, "revision_to": 2}
  ]
}
```

### 3.1 状态枚举

| status | 含义 |
|---|---|
| `pending` | 未开始 |
| `running` | 进行中（用于检测异常中断） |
| `done` | 完成且缓存有效 |
| `partial` | 完成但存在未填充缺口 |
| `stale` | 上游变化导致失效 |
| `skipped` | 按配置跳过（如 C 档对齐） |
| `failed` | 失败 |

`running` 残留说明上次异常终止。启动时检测到 `running` 且无存活进程，降级为 `stale` 并提示重跑该阶段。

---

## 4. 阶段依赖

```
ingest ──► probe ──► align ──┐
   │                          ├──► units ──► outline ──► render ──► gates
   └──────────────────────────┘
```

| 阶段 | 依赖输入 |
|---|---|
| ingest | `inputs/` 文件哈希、config 的 `ingest` 段、pipeline 版本 |
| probe | sources 的产物哈希 |
| align | sources、probe、config 的 `alignment` 段 |
| units | sources、align、gaps/answers、既有 `units/` 的内容哈希 |
| outline | units、既有 `outline.json`、config 的 `course` 段 |
| render | units、outline、patches、glossary、template_hash、`render` 段 |
| gates | render 产物 |

---

## 5. 缓存键

```
stage_key = sha256(
    pipeline_version
  + 相关输入的哈希（按 §4）
  + 该阶段相关的 config 子段
  + 该阶段相关模板/提示词版本
)
```

规则：

- 键按阶段独立计算，一个阶段变化不必然使下游全部失效
- `units` 阶段额外包含 `prompt_version`（抽取提示词），改动提示词即使素材不变也须重跑
- `render` 阶段包含 `template_hash`，模板改动只影响渲染
- 哈希对象为文件内容，不使用 `mtime` 判定（`mtime` 仅用于报告）

### 5.1 部分失效

| 变化 | 重跑范围 |
|---|---|
| 新增一讲素材 | ingest（该文件）→ probe → align（该讲）→ units（受影响）→ outline → render |
| 修改已有素材 | 同上，且原 `units` 中引用该素材的知识点标记 `revision` 待复核 |
| 修改 hand notes | units（叠加层）→ render |
| 修改 glossary | render |
| 修改模板 | render |
| 修改 `render.scope` | render |
| 修改 prompt | units → outline → render |

「受影响」的判定不靠模型：知识点 `evidence` 中引用的 `src` 或 `loc` 命中变化文件的即视为受影响。

---

## 6. 缺口与暂停

知识化阶段需要模型完成的工作以缺口文件形式存在：

```
.study/gaps/g*.json
.study/gaps/answers/g*.json
```

- 存在未填充缺口时，`units` 状态为 `partial`，`build` 在渲染前暂停并列出缺口
- 填充答案后重跑，仅处理有答案的缺口，已缓存的缺口不再请求
- 缺口答案按 `sha256(缺口输入 + prompt_version + 模型标识)` 缓存

缺口机制使 `state.json` 能准确表达「流程停在哪里，等什么」。

---

## 7. 并发

- 工作区级文件锁 `.study/lock`，记录 pid 与启动时间
- 检测到锁存在且进程存活时，拒绝并发执行，退出码 `3`，提示等待或 `--take-lock`
- 进程异常终止后，下次启动检测 pid 不存在则自动接管并记 `warning`
- 只读命令（`show`、`list`、`stats`、`doctor`）不取锁

---

## 8. 用户数据的保护

### 8.1 内容产物的冲突检测

`units/` 是派生产物，但通常会被人工修改。为避免重跑覆盖人工修改：

1. 记录每个知识点的 `content_hash` 与 `revision`
2. 重跑生成新内容时，若磁盘上的 `content_hash` 与上次生成的记录不一致，说明被人工修改过
3. 此时不直接覆盖，而是写出 `<id>.generated.md` 并在 `review/findings.md` 中列为 `major`，要求人工合并

此机制是「内容产物」区别于纯派生数据的关键。

### 8.2 备份

- `study migrate` 与 `clean --include-units` 前自动备份至 `.study/backup/<timestamp>/`
- 备份保留最近 5 次，超出按时间清理
- `patches/` 与 `review/` 不进入备份（它们本身就是源数据，应纳入版本控制）

### 8.3 版本控制边界

| 路径 | 是否纳入版本控制 |
|---|---|
| `inputs/` | 视课程内容授权而定，公开仓库中不得包含 |
| `study.config.yaml` | 是 |
| `glossary.yaml` | 是 |
| `.study/knowledge/units/` | 是（人工修改的成果） |
| `.study/outline.json` | 是（含手工位置） |
| `.study/patches/` | 建议是（个人笔记，若仓库公开则不纳入） |
| `.study/review/` | 建议是 |
| `.study/sources/`、`assets/`、`aligned/`、`out/`、`gaps/` | 否（派生或体积大） |
| `.study/state.json` | 否（含绝对路径与时间戳） |

仓库提供 `.gitignore` 模板，并由门禁 `workspace_clean` 检查是否有违规纳入。

---

## 9. 一致性自检

`study doctor` 执行：

| 检查 | 说明 |
|---|---|
| `running` 残留 | 无存活进程的 `running` 阶段 |
| 键不一致 | `state.json` 记录的键与实际文件哈希不符 |
| 孤儿产物 | `units/` 存在但 `state.json` 无记录，或反之 |
| 缺口悬挂 | 答案存在但对应缺口已不存在 |
| 补丁悬挂 | 补丁指向的 `unit` 已删除 |
| 版本不兼容 | `schema_version` 与程序不符 |
| 锁残留 | `.study/lock` 存在但进程不存在 |

自检不修改任何文件，只报告并给出修复命令。

---

## 10. 禁止事项

| 禁止 | 理由 |
|---|---|
| 用 `mtime` 判定是否需要重跑 | 复制、检出、同步都会改变时间戳 |
| 在 `state.json` 中记录绝对路径 | 工作区搬移后全部失效 |
| 清理时删除 `patches/` 或 `review/` | 属用户数据，无法重建 |
| 静默覆盖人工修改过的知识点 | 丢失人工工作 |
| 用空文件表示「跳过」 | 无法与「失败」区分 |
| 多个进程同时写同一工作区 | 产物交叉污染 |
