---
schema_version: 1
id: sec-2-1
title: 线性表：n 个数据元素的有限序列
type: concept
evidence:
- src: 第02章+线性表.pptx
  loc:
    slide: 2
    shape: 6147
  block: s02-002-2
- src: 第02章+线性表.pptx
  loc:
    slide: 2
    shape: 6148
  block: s02-002-3
- src: 第02章+线性表.pptx
  loc:
    slide: 2
    shape: 6149
  block: s02-002-4
- src: 第02章+线性表.pptx
  loc:
    slide: 2
    shape: 6150
  block: s02-002-5
- src: 第02章+线性表.pptx
  loc:
    slide: 2
    shape: 6151
  block: s02-002-6
- src: 第02章+线性表.pptx
  loc:
    slide: 2
    shape: 35875
  block: s02-002-7
- src: 第02章+线性表.pptx
  loc:
    slide: 3
    shape: 7170
  block: s02-003-1
- src: 第02章+线性表.pptx
  loc:
    slide: 3
    shape: 7171
  block: s02-003-2
- src: 第02章+线性表.pptx
  loc:
    slide: 3
    shape: 7172
  block: s02-003-3
- src: 第02章+线性表.pptx
  loc:
    slide: 3
    shape: 7173
  block: s02-003-4
- src: 第02章+线性表.pptx
  loc:
    slide: 3
    shape: 7174
  block: s02-003-5
- src: 第02章+线性表.pptx
  loc:
    slide: 3
    shape: 7177
  block: s02-003-7
- src: 第02章+线性表.pptx
  loc:
    slide: 4
    shape: 8194
  block: s02-004-1
- src: 第02章+线性表.pptx
  loc:
    slide: 4
    shape: 8210
  block: s02-004-2
- src: 第02章+线性表.pptx
  loc:
    slide: 4
    shape: 8211
  block: s02-004-3
- src: 第02章+线性表.pptx
  loc:
    slide: 4
    shape: 8207
  block: s02-004-4
- src: 第02章+线性表.pptx
  loc:
    slide: 4
    shape: 8208
  block: s02-004-5
- src: 第02章+线性表.pptx
  loc:
    slide: 4
    shape: 8209
  block: s02-004-6
- src: 第02章+线性表.pptx
  loc:
    slide: 4
    shape: 8197
  block: s02-004-7
- src: 第02章+线性表.pptx
  loc:
    slide: 4
    shape: 8198
  block: s02-004-8
- src: 第02章+线性表.pptx
  loc:
    slide: 4
    shape: 8205
  block: s02-004-10
- src: 第02章+线性表.pptx
  loc:
    slide: 4
    shape: 8206
  block: s02-004-11
- src: 第02章+线性表.pptx
  loc:
    slide: 4
    shape: 8202
  block: s02-004-12
- src: 第02章+线性表.pptx
  loc:
    slide: 4
    shape: 8203
  block: s02-004-13
- src: 第02章+线性表.pptx
  loc:
    slide: 4
    shape: 8204
  block: s02-004-14
- src: 第02章+线性表.pptx
  loc:
    slide: 5
    shape: 9218
  block: s02-005-1
- src: 第02章+线性表.pptx
  loc:
    slide: 5
    shape: 9220
  block: s02-005-3
- src: 第02章+线性表.pptx
  loc:
    slide: 5
    shape: 9221
  block: s02-005-4
- src: 第02章+线性表.pptx
  loc:
    slide: 5
    shape: 9222
  block: s02-005-5
- src: 第02章+线性表.pptx
  loc:
    slide: 5
    shape: 9223
  block: s02-005-6
- src: 第02章+线性表.pptx
  loc:
    slide: 5
    shape: 9224
  block: s02-005-7
- src: 第02章+线性表.pptx
  loc:
    slide: 5
    shape: 9225
  block: s02-005-8
- src: 第02章+线性表.pptx
  loc:
    slide: 5
    shape: 9226
  block: s02-005-9
- src: 第02章+线性表.pptx
  loc:
    slide: 5
    shape: 9227
  block: s02-005-10
- src: 第02章+线性表.pptx
  loc:
    slide: 5
    shape: 9228
  block: s02-005-11
origin: derived
revision: 2
reviewed_by:
- compose
---

## 概念

线性表是 $n$ 个数据元素的有限序列，写成 $(a_1,\ a_2,\ \dots,\ a_n)$。
其中 $a_1$ 是头元素，$a_n$ 是尾元素，$a_i$ 是第 $i$ 个元素。
$a_{i-1}$ 是 $a_i$ 的直接前驱，$a_i$ 是 $a_{i-1}$ 的直接后继：当 $2 \le i \le n$ 时，$a_i$ 有且只有一个直接前驱；当 $1 \le i \le n-1$ 时，$a_i$ 有且只有一个直接后继。由此，头元素没有直接前驱，尾元素没有直接后继，其余元素恰好各有一个前驱和一个后继。
线性表中的数据元素可以由若干个数据项组成：一个元素是一条记录，记录由若干数据项构成。
作为抽象数据类型，线性表由一个数据对象、一个数据关系和一组基本操作共同刻画：数据对象 $D=\{a_i \mid a_i \in \mathrm{ElemSet},\ i=1,2,\dots,n\}$；数据关系 $R_1=\{<a_{i-1},a_i> \mid a_{i-1},a_i \in D,\ i=1,2,\dots,n\}$。操作部分见“怎么用”。

## 为什么需要

线性表上的操作名目繁多，但真正需要单独定义的只是少数几个简单操作。
合并、拆分、复制等复杂操作，都可以由这些简单基本操作组合实现，因此不用为每个新需求再定义一个基本操作。

## 怎么用

构造与销毁：`InitList(&L)` 构造一个空的线性表 $L$；`DestroyList(&L)` 销毁线性表 $L$。
清空：`ClearList(&L)`，前提是线性表 $L$ 已存在，结果是将 $L$ 重置为空表。
判空与长度：`ListEmpty(L)` 判断是否为空；`ListLength(L)` 取长度。
取元素：`GetElem(L, i, &e)` 把 $L$ 中的第 $i$ 个元素返回到 $e$。
查找：`LocateElem(L, e, compare())` 借助比较函数 `compare()` 定位元素 $e$ 的位置。
找前驱和后继：`PriorElem(L, cur_e, &pre_e)` 求 `cur_e` 的前驱元素；`NextElem(L, cur_e, &next_e)` 求 `cur_e` 的后继元素。
插入与删除：`ListInsert(&L, i, e)` 在位置 $i$ 处插入元素；`ListDelete(&L, i, &e)` 删除位置 $i$ 处的元素。
遍历：`ListTraverse(L, visit())` 依次对每个元素执行 `visit()`。

## 例子

英文字母表 $(A, B, C, \dots, Z)$ 是一个线性表，每个元素就是一个字母。
登记表是元素由多个数据项组成的线性表。一条记录有姓名、学号、性别、年龄、班级这五个数据项，整张登记表就是这些记录构成的有限序列：
| 姓名 | 学号 | 性别 | 年龄 | 班级 |
| --- | --- | --- | --- | --- |
| 王晓林 | 790631 | 男 | 25 | 计90 |
| 陈红 | 790632 | 女 | 24 | 计90 |

## 易错

前驱、后继的“唯一”要条件，不要漏掉下标范围：只说 $a_i$ 有直接前驱之前，先看清楚 $2 \le i \le n$；只说有直接后继之前，先看清楚 $1 \le i \le n-1$。
$\mathrm{InitList}$、$\mathrm{ClearList}$、$\mathrm{DestroyList}$ 是三件不同的事，分别对应“构造空表”“把已有表重置为空表”“销毁表”；其中 $\mathrm{ClearList}$ 有前置条件，即线性表已存在。
带 `&` 的参数（如 `&L`、`&e`）是待带回结果的参数，与只读参数（如 `L`、`i`）在用法上不同。
