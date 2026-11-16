# course_press_harness

把一门课的多种学习素材，整理成**一本可以脱离原始材料独立阅读的连续文档**。

喂给它幻灯片、讲义 PDF、Word、纯文本、课堂录音和学生手记，得到的是一本有章节编号、
有公式排版、有对照表和算例的书——而不是一堆被拼接起来的原文。

> English version: [README.en.md](README.en.md)

> 状态：**v0.1 alpha**。已在真实课程素材上跑通：一百余页的演示文稿、四十余页的双栏 PDF、
> 两百余页的连续章节课件。内容质量取决于写作层，而写作层是模型的工作，通过缺口文件接入。

![架构](docs/assets/architecture.svg)

<sub>图 1　数据流：素材经确定性层整理为中间表示，写作层通过缺口文件介入，最终装配为多种格式的成书。</sub>

---

## 它和"AI 笔记工具"有什么不同

多数笔记工具把素材摊成一大段文本，让模型总结一遍。这样会丢掉两样后面一定会用到的东西：
**每句话出自哪里**，以及**加进第七讲时哪些内容被改动了**。

这个项目保留一层结构化中间表示——带定位符的块（`slide 12`、`00:13:40`、`page 37`）——
上面所有能力都建立在这层之上。带来的结果是：

- 管道**确定、可离线运行**，不接模型也能产出一本书
- 新增讲次是**追加**，不重写已有章节（实测：加第四讲后前三讲正文逐字节一致）
- 每条陈述都能追溯到证据（内部保留，是否呈现给读者是一项配置）
- 质量门禁检查的是结构，不是"感觉"

## 安装

```bash
uv venv .venv && uv pip install -e .
# 公式用 KaTeX 排版，首次检出需要装一次：
(cd tools && npm install)
```

可选依赖：`pdf`（PDF 摄取）、`docx`（Word 摄取）、`audio`（本地转写）。
PDF 输出需要本机有 Edge 或 Chrome。

## 快速开始

```bash
mkdir my-course && cd my-course
study init --course "示例课程"
cp ~/slides/lecture-06.pptx inputs/
$EDITOR study.config.yaml     # 声明讲次
study auto                    # 构建 + 生成审查任务 + 报告状态
$EDITOR .study/out/book.pdf
```

最小配置：

```yaml
schema_version: 1
course:
  name: 示例课程
  lang: zh
  lectures:
    - number: 6                 # 用 number，不要用 no：YAML 1.1 会把裸键 no 解析成 false
      title: 示例主题
      sources: [inputs/lecture-06.pptx]
compose:
  style: ""                     # 写作要求由你写，工具不预设文风
render:
  profile: learn                # learn | review
  source_references: none       # none | footnote | inline
```

## 命令

| 命令 | 作用 |
|---|---|
| `study init` | 建立工作区 |
| `study ingest` | 解析 `inputs/`，产出带定位符的块 |
| `study probe` | 报告素材能支撑到什么程度 |
| `study auto` | 一轮跑完：构建 → 审查任务 → 状态报告；退出码 5 表示"等你填答案" |
| `study build` | ingest → 知识点 → 骨架 → 渲染 → 门禁 |
| `study compose` | 写作层：把草稿写成可读正文（走缺口文件） |
| `study align` | 录音与幻灯片对齐（A/B/C 三档） |
| `study render` | 输出 `md` / `txt` / `tex` / `pdf` / `docx` / `pptx`，或 `--format all` |
| `study outline` | 查看或手工调整骨架 |
| `study review` | 生成 / 应用 / 查看语义审查 |
| `study explain` | 为某个知识点生成补充讲解补丁 |
| `study doctor` | 环境与工作区自检 |
| `study clean` | 清理派生数据（不动个人数据） |

退出码：`0` 正常，`1` 门禁失败，`2` 参数错误，`3` 输入缺失，`4` 内部错误，`5` 需要人工决定。

## 架构

四层，边界清晰：

```
输入 ──► 中间表示 IR ──► 知识单元 ──► 骨架 ──► 文档
        (带定位的块)    (缓存单位)   (编号)    md/pdf/docx…
                            ▲
                       写作层（模型或人工，走缺口文件）
```

**两条硬边界：**

1. **命令行从不调用模型。** 需要语义的地方产出缺口文件
   （`.study/gaps/<id>.json` → `answers/<id>.json`），任何 agent 或人都能填，
   管道本身离线可跑、结果可缓存、可复核。
2. **确定性层与语义层分离。** 摄取、对齐、切分、门禁、渲染全是脚本；
   只有写作与审查调用模型，且产物一律落盘。

阶段：

| 阶段 | 性质 | 产出 |
|---|---|---|
| ① ingest | 确定性 | `.study/sources/*.jsonl` |
| ② probe | 确定性 | 能力清单 |
| ③ align | 半确定性 | 录音片段 ↔ 页码 |
| ④ units | 确定性 | 知识点草稿 |
| ⑤ outline | 确定性 | `outline.json` |
| ⑥ compose | 语义层 | 正文 |
| ⑦ render | 确定性 | 成品文档 |
| ⑧ gates | 确定性 | 通过 / 不通过 |
| ⑨ review | 语义层 | findings 队列 |

## 工作区

```
my-course/
├── study.config.yaml
├── inputs/                    原始素材，只读
└── .study/
    ├── sources/               中间表示（每块一行）
    ├── aligned/               对齐结果
    ├── knowledge/units/       知识点，跨讲共享，可手工编辑
    ├── outline.json           骨架，可手改，重跑不覆盖
    ├── gaps/                  缺口与答案
    ├── patches/               个人笔记与补丁，永不写入正文
    ├── review/                审查结果
    └── out/                   成品
```

`sources/`、`assets/`、`out/` 是派生物，可随时删除重建。
`patches/`、`review/`、`outline.json` 属于你的数据，任何命令都不会覆盖。

## 已验证的行为

- 一百余页的演示文稿能完整切分，结构与素材自带编号一致
- 标题写成普通文本框、页面还带页眉的素材也能识别出结构（否则整份只会析出一个知识点）
- 追加第四讲后，前三讲正文逐字节一致
- 手工调整大纲后重新构建，位置保持
- 相同输入重复构建，输出逐字节一致

细节与踩过的坑见 [docs/validation.md](docs/validation.md)。

## 设计文档

| 文件 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 总纲：原则、阶段、数据契约、决策记录 |
| [docs/unit-schema.md](docs/unit-schema.md) | 知识点的字段与修订协议 |
| [docs/outline-generation.md](docs/outline-generation.md) | 分节算法、编号、三方合并 |
| [docs/ingest.md](docs/ingest.md) | 各格式适配器、块 id 稳定性、图片降级 |
| [docs/alignment.md](docs/alignment.md) | 录音对齐三档 |
| [docs/review-protocol.md](docs/review-protocol.md) | findings 格式与审查流程 |
| [docs/rendering.md](docs/rendering.md) | 模板、视图、输出格式、确定性 |
| [docs/glossary.md](docs/glossary.md) | 术语表与一致性检查 |
| [docs/cli.md](docs/cli.md) | 命令全集与配置 |
| [docs/state.md](docs/state.md) | 缓存、失效、用户数据保护 |

## 测试

```bash
python -m pytest
```

测试全部使用**现场生成的占位素材**，不依赖任何外部文件。覆盖：摄取与切分、门禁、
公式排版、录音对齐、缺口流转、渲染确定性与增量追加。

## 许可

MIT
