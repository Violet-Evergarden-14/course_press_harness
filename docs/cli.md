# 命令行接口

> 状态：设计稿 v0.1
> 依赖：[architecture.md](architecture.md) §4、§16
> 本文件定义命令全集、参数约定、退出码与自动化行为。

---

## 1. 设计约束

| 约束 | 含义 |
|---|---|
| 单一入口 | 所有能力通过 `study <command>` 提供，无独立脚本入口 |
| 非交互可行 | 任何命令在 `--no-interactive` 下不得阻塞等待输入 |
| 机器可读 | `--json` 输出结构化结果，供代理与 CI 消费 |
| 幂等 | 重复执行同一命令不产生额外副作用 |
| 断点续跑 | `--from` / `--only` 支持从指定阶段开始 |
| 不隐藏失败 | 降级必须同时输出 warning 与对应缺口或 findings |

代理适配层（`SKILL.md`、`AGENTS.md`、`.cursor/rules`）只描述「何时调用哪条命令」，不得包含业务逻辑。

---

## 2. 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 门禁失败（L1 检查未通过） |
| 2 | 参数错误 |
| 3 | 输入缺失或不可读（阻塞级） |
| 4 | 内部错误（未预期异常） |
| 5 | 需要人工确认后才能继续（如三方合并冲突、待确认对齐项） |

CI 中只允许 `0`。`5` 用于交互式流程，在 `--no-interactive` 下升级为 `3`。

---

## 3. 命令全集

### 3.1 `study init`

在当前目录建立工作区。

```bash
study init --course "示例课程" --lang zh [--from-example templates/minimal]
```

产物：`study.config.yaml`、`inputs/`、`.study/`、`glossary.yaml`（空表）。已存在时不覆盖，除非 `--force`。

---

### 3.2 `study ingest`

摄取素材，产出 IR。

```bash
study ingest [--only pptx,pdf] [--file path] [--force]
```

| 参数 | 说明 |
|---|---|
| `--only` | 仅处理指定适配器 |
| `--file` | 仅处理指定文件（用于重试单个失败项） |
| `--force` | 忽略缓存，强制重跑 |

产物：`.study/sources/*.jsonl`、`.study/assets/`、`.study/errors.jsonl`。

存在 `blocker` 级错误时退出码 `3`；仅 `warning` 时退出码 `0`，warning 数计入摘要。

---

### 3.3 `study probe`

输出能力清单。

```bash
study probe [--json]
```

产物：`.study/probe.json`。不调用模型，不写入其他文件。

---

### 3.4 `study align`

执行音频对齐。

```bash
study align [--lecture 6] [--anchor 12:13:40] [--tier A|B|C]
            [--offset 00:00:30] [--split 00:45:00] [--confirm]
```

| 参数 | 说明 |
|---|---|
| `--anchor <slide>:<hh:mm:ss>` | 显式提供锚点，可重复 |
| `--tier` | 强制指定档位，覆盖 probe 判定 |
| `--offset` | 录音起点对应的偏移 |
| `--split` | 分段对齐，用于中途中断的录音 |
| `--confirm` | 交互式确认低置信度项；`--no-interactive` 下不执行 |

产物：`.study/aligned/sNN.align.json`、`sNN.confirm.yaml`。存在未确认项时退出码 `5`（交互模式下）。

---

### 3.4.1 `study auto`

一条命令跑完一轮，结束时给出「还缺什么」：

```bash
study auto [--profile learn|review] [--format md|txt|tex|pdf|docx|pptx|all]
           [--skip-review] [--l2-limit 20] [--l3-limit 10] [--sample 0.1]
```

流程：`build`（摄取→对齐→知识点→编排→渲染→门禁）→ 生成 L2/L3 审查任务 → 状态报告。

报告包含：各阶段状态、待填充缺口按类型计数、待处理问题按级别计数、已产出的文件及大小。

退出码：门禁失败 `1`；存在待填充缺口或待处理问题时 `5`——**这不是失败，是「轮到人了」**。脚本可以据此决定是否继续等待。

```bash
study auto --skip-review      # 只要文档，不要审查任务
```

### 3.5 `study build`

按顺序执行 ingest → probe → align → 知识化 → 编排，产出可渲染状态。

```bash
study build [--from STAGE] [--only STAGE] [--lecture 6]
            [--no-interactive] [--json]
```

`--from` 与 `--only` 的区别：前者从该阶段起跑完全部后续阶段，后者只跑该阶段。

知识化阶段的模型调用通过缺口文件进行：若存在未填充的缺口，构建暂停并列出缺口清单，退出码 `5`。填充后重新执行即继续。

---

### 3.6 `study gaps`

管理待填充的缺口。

```bash
study gaps list [--kind claim_entailment] [--json]
study gaps show <id>
study gaps fill <id> --file answer.json
study gaps validate <id>
```

`fill` 校验答案格式（按缺口声明的 `answer_format`），不合法即拒绝写入。

---

### 3.7 `study outline`

查看与调整骨架。

```bash
study outline show [--json]
study outline regenerate [--force]
study outline move <unit-id> --to <lecture>/<section>
study outline rename-section <lecture>/<section> --title "新标题"
```

`regenerate` 执行三方合并，冲突时退出码 `5` 并列出冲突项。

---

### 3.8 `study render`

生成文档。

```bash
study render [--scope semester|lecture|chapter|unit]
             [--lecture 6] [--profile learn|review]
             [--lang zh|en|bilingual]
             [--source-references none|footnote|inline]
             [--exclude-patches] [--audit-locators] [--out DIR]
```

`--audit-locators` 生成的带定位版本只写 `.study/review/`，不进入 `out/`。

---

### 3.9 `study review`

审查与复核。

```bash
study review list [--level blocker|major|minor] [--unit ID] [--json]
study review show <finding-id>
study review accept <finding-id> [--note TEXT]
study review reject <finding-id> [--note TEXT]
study review apply [--dry-run]
study review stats [--json]
```

`apply` 只处理 `accepted` 且带可执行 `suggestion` 的项；每次修改记录来源，修改后自动重跑 L1，失败则回滚。

---

### 3.10 `study explain`

生成或管理按需补丁。

```bash
study explain <unit-id> [--lang zh]        # 生成补充讲解补丁请求（缺口文件）
study explain --prereq <术语或概念>         # 生成前置知识补丁请求
study explain --list                        # 列出已有补丁
study explain --remove <slug>               # 删除补丁
```

生成的请求写入 `gaps/`，填充后由 `study render` 自动叠加。

---

### 3.11 `study doctor`

环境与工作区自检。

```bash
study doctor [--json]
```

检查项：Python 版本、可选依赖（OCR、转写引擎、公式转换）、`ffmpeg`、工作区结构、术语表合法性、`inputs/` 与 `.study/` 是否被纳入版本控制。

缺失可选依赖时给出降级说明，不报错。

---

### 3.12 `study clean`

清理产物。

```bash
study clean [--stage sources|aligned|units|out|all]
            [--keep-history] [--dry-run]
```

`--dry-run` 列出将删除的路径。**不得**清理 `patches/`、`review/` 与人工修改过的 `outline.json`，除非显式指定 `--include-user-data` 并二次确认。

---

### 3.13 `study migrate`

数据格式迁移。

```bash
study migrate [--check] [--to SCHEMA_VERSION]
```

`schema_version` 不兼容时，程序拒绝加载并提示运行本命令。迁移前自动备份至 `.study/backup/<timestamp>/`。

---

## 4. 全局参数

| 参数 | 说明 |
|---|---|
| `--config PATH` | 指定配置文件 |
| `--workspace PATH` | 指定工作区，默认当前目录向上查找 `study.config.yaml` |
| `--no-interactive` | 禁止任何交互；需要确认的操作返回退出码 `5` |
| `--json` | 机器可读输出；日志写入 stderr |
| `--quiet` / `--verbose` | 日志级别 |
| `--no-color` | 关闭颜色 |
| `--pipeline-version` | 打印管道版本，用于缓存键核对 |

---

## 5. 配置文件

```yaml
schema_version: 1

course:
  name: 示例课程
  lang: zh
  lectures:
    # 用 number，不要用 no：YAML 1.1 会把裸键 `no` 解析为布尔 false
    - {number: 6, title: 示例专题, sources: [inputs/lecture-06.pptx, inputs/rec-06.m4a]}

render:
  profile: learn
  scope: semester
  source_references: none
  template: templates/learn/md

alignment:
  tier: auto            # auto | A | B | C
  confirm_threshold: 0.6

review:
  l3:
    model: null         # 与生成模型不同的标识；为空时跳过 L3
  sample_ratio: 0.1
  budget:
    l2_per_lecture: 40
    l3_per_lecture: 15

glossary:
  path: .study/glossary.yaml

ingest:
  ocr: auto             # auto | tesseract | none
  docx_comments: false
```

配置变化参与缓存键。`render` 段的改动只触发渲染，不触发摄取。

---

## 6. 代理适配

`SKILL.md` 只包含：

1. 何时调用哪条命令（如「用户提供课件后先 `study init` 再 `study ingest`」）
2. 遇到退出码 `5` 时如何向用户提问、如何把答案写回缺口文件
3. 出现 `blocker` 时如何汇报，不得自行修改数据

不得包含：抽取规则、对齐算法、模板语法、审查判据。这些属于命令行的职责，放进提示词会导致不同代理产生不同结果。

---

## 7. 自动化示例

```bash
# CI：只跑确定性检查
study doctor --json
study ingest --no-interactive
study build --from 编排 --no-interactive
study render --exclude-patches --no-interactive
echo $?   # 必须为 0

# 代理：填充缺口后继续
study gaps list --json | jq -r '.[].id' | while read id; do
  answer=$(agent_fill "$id")            # 由代理生成
  echo "$answer" | study gaps fill "$id" --file -
done
study build --no-interactive
```

---

## 8. 错误输出格式

`--json` 时错误写入 stdout：

```json
{
  "code": 1,
  "stage": "render",
  "gate": "source_reference_policy",
  "message": "正文出现来源指称",
  "locations": [{"unit": "huffman-construct", "line": 42, "text": "如课件所示"}],
  "hint": "移除指称并直接陈述内容；或设置 render.source_references"
}
```

`hint` 必须给出可执行的下一步，不接受「请检查配置」这类空提示。
