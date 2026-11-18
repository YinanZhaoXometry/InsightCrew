# InsightCrew

> 多智能体数据分析助手，核心是**独立审计**：
> 一个审计者用另一条实现路径重算每个关键数字，用外部口径字典检查定义，
> 不一致就驳回，由规划者重新规划、执行者重跑。

COMP5584 Group Project · 演示骨架（可直接运行、可换真模型、可做消融实验）

---

## 30 秒跑起来

**Windows：直接双击** `start.bat`（交互提问）或 `demo.bat`（固定剧本 + 生成报告）。
启动器会自动挑选可用解释器并打印版本，不需要先配 PATH。

命令行方式：

```bash
cd insightcrew
python ask.py                     # 交互式：输入问题，跑完自动打开回放页面
```

Python 3.8+ 即可，无需任何第三方依赖（仅标准库）。已在 3.8.3 / 3.11.5 / 3.13.14 实测通过。

换个问题、看有无审计的差别：

```bash
python ask.py "2026年8月华南区的营收是多少？"
python ask.py "2026年第三季度华东区的营收是多少？" --compare   # 无审计 vs 有审计
```

非交互入口（脚本/录屏用）：

```bash
python run_demo.py                # 跑一次完整分析（默认问题）
python -m ui.render_report        # 重新生成可视化回放 report.html
```

**完整操作指引见 `../08_操作手册.md`。**

**不需要 API key**：没有 `IC_API_KEY` 时自动进入离线模式，编排/沙箱/审计全部真实运行，
只有"写代码"这一步由规则生成，因此不需要联网也能完整操作一遍。

接真模型（任何 OpenAI 兼容接口都能用：DeepSeek / Qwen / 智谱 / OpenAI / 本地 vLLM）：

```bash
cp .env.example .env          # 然后在 .env 里填 IC_API_KEY / IC_BASE_URL / IC_MODEL
python check_api.py           # 体检：读到了哪把 key、通不通、多快
python check_api.py --run     # 体检 + 真跑一道题
```

`.env` 由 `config.py` 自己解析（不依赖 python-dotenv），所以**双击 start.bat 也能生效**。
系统环境变量优先级高于 `.env`。

没有 key 但想验证"在线链路是通的"：

```bash
python -m eval.mock_online_test    # 本地假模型，走完整 HTTP → 沙箱 → 审计 → 报告
```

### 能问什么（四个指标，都带双路径审计）

| 指标 | 例子 | 实测结果 |
|---|---|---|
| 营收 | `2026年第三季度华东区的营收是多少？` | 1,036,538.28 |
| 订单量 | `2026年第三季度华北区的订单量是多少？` | 220 笔 |
| 客单价 | `2026年第三季度华南区的客单价是多少？` | 4,546.68 |
| 退款率 | `2026年9月华北区的退款率是多少？` | 8.38 % |

地区只认数据里真实存在的三个（华东 / 华北 / 华南）或「全公司」；时间限 2026-06-01 ~ 2026-10-14，可写（季度 / QX / 月份 / 八月 / 上季度 / 近 N 个月 / 全年）。
问「西南」「线上渠道」「毛利率」都会被明确拒绝并说明原因——**能答才答，答不了不给假数字**。

### 数据从哪来：自己生成的，不是下载的

`data/orders.csv`（1200 单）与 `data/refunds.csv`（166 条退款）都由 `data/make_data.py` 用 Python 标准库 `random` 在 `random.seed(42)` 下生成：

```bash
cd data && python make_data.py    # 重新生成，与仓库内文件逐字节一致
```

| 文件 | 行数 | MD5 |
|---|---|---|
| `orders.csv` | 1200 | `cdd2178f8da36068281184a8b44c95b4` |
| `refunds.csv` | 166 | `2b5a4f48506c8d948cffdc39d2fcd848` |

**不是真实业务数据，也没有引用任何公开数据集。** 不用真实数据的理由有三个：

1. **没有 ground truth**。真实数据里没人知道"正确的营收"是多少，"审计有没有用"就只能靠自说自话。合成数据里正确答案由口径手册唯一确定。
2. **错误不可控**。真实数据中取消单、用券、退款三件事混在一起，某次结果错了，无法判断系统漏的是哪一层。合成数据把比例钉住（取消 18.2%、用券 55.9%、退款 13.8%），可以逐个注入、逐个验证。
3. **不可复现**。`seed(42)` 保证换台电脑、换个时间跑，数字一模一样——答辩现场和报告里的每个数值都能对上。

换真实数据只需替换这两个 CSV 和 `data/metric_dict.json`，计算与审计代码不用改。字段契约：

| 表 | 必需字段 |
|---|---|
| `orders.csv` | `order_id, order_date(YYYY-MM-DD), region, channel, amount, coupon, status` |
| `refunds.csv` | `refund_id, order_id, refund_date, refund_amount, reason` |

### 实测数据（数字都来自实际运行，可复现）

| 场景 | 指标 | 结果 |
|---|---|---|
| 离线剧本（机制验证） | 12 题准确率 | 12/12 = 100% |
| 真模型 `deepseek-chat`（能力验证） | 12 题准确率 | **12/12 = 100%**，平均 1.08 轮，55,260 tokens |
| 错误注入（审计灵敏度） | 40 例已知错误 | 拦截 40/40，误报 0/10；其中 30 例只有 L3 拦得住 |
| 三组消融（离线） | full / no_critic / single | 100% / 16.7% / 16.7% |

```bash
python -m eval.online_eval       # 真模型 12 题，逐题打印期望 vs 实得
python -m eval.consistency_test  # 口径字典与 SQL 实现是否漂移
```

---

## 一次运行里发生了什么

```
用户问题：「2026 年第三季度华东区的营收是多少？」
  │
  ├─ Profiler   读 schema，产出画像卡（8 字段 / 3 条风险）
  ├─ Planner    拆 2 步，写下 filters：exclude_cancelled=true, coupon_deduction=false, subtract_refund=false
  ├─ Analyst    沙箱执行 → 1,189,734.57
  ├─ Critic     L2 复算（SQL 路径）→ 1,189,734.57 ✅ 一致
  │             L3 口径校验 → 1/3 项满足 ❌
  │             ⇒ REVISE：「未扣除优惠券、未扣减退款」
  │
  ├─ Planner    重规划 v2，filters 两项改为 true（3 步）
  ├─ Analyst    沙箱执行 → 1,036,538.28
  ├─ Critic     L2 → 一致 ✅   L3 → 3/3 ✅   ⇒ PASS
  └─ Reporter   出报告，置信度「高」
```

**这一轮最值得记住的细节**：第一轮的 L2 复算是"一致"的——
两条路径算出同一个错误答案。这说明**只做复算抓不到口径错**，
必须有 L3（外部口径知识）。这是整个项目的核心论点，也是演示的第二幕。

---

## 目录结构

| 路径 | 作用 | 对应评分项 |
|---|---|---|
| `core/orchestrator.py` | 驱动循环、三重终止条件、降级输出 | 设计 30% |
| `core/blackboard.py` | 共享工作区，artifact 版本化 | 设计 30% |
| `core/messages.py` | 消息总线，6 种 intent 枚举 + trace 落盘 | 设计 30% |
| `core/sandbox.py` | AST 静态检查 + 子进程隔离 + 超时 | 实现 25% |
| `agents/profiler.py` | 数据画像 | 设计 30% |
| `agents/planner.py` | 拆步骤 + **结构化 filters** | 设计 30% |
| `agents/analyst.py` | 写代码 + 执行 + 同层自修复 | 实现 25% |
| `agents/critic.py` | L2 复算 + L3 口径审计（确定性，不用 LLM） | 分析 15% |
| `agents/reporter.py` | 报告 + 置信度 + 局限 | 展示 15% |
| `tools/data_tools.py` | schema 探查 + SQL 独立复算入口 | 实现 25% |
| `tools/metrics.py` | 四个指标的 SQL 实现（L2 的第二条路径） | 分析 15% |
| `tools/metric_dict.py` | 指标口径字典（L3 的知识来源） | 分析 15% |
| `eval/` | 离线评估 + 三组消融 + 错误注入 + 在线评测 + 一致性检查 | 分析 15% |
| `check_api.py` | 接真模型前的体检（连通性 / 计费 / 报错翻译） | 实现 25% |
| `core/question_parser.py` | 问题解析 + 范围检查（答不了就说答不了） | 实现 25% |
| `ui/render_report.py` | 单文件 HTML 回放 | 展示 15% |

---

## 五个关键设计决策（答辩就讲这五条）

### 1. 审计不用 LLM，用确定性代码

如果审计者的判据也是"模型觉得对不对"，它只会附和——同一个模型、同一份数据、
同一个上下文，凭什么得出相反结论？

所以 `agents/critic.py` 里没有任何模型调用：
- 数值比对 → 容差比较（`TOL = 0.01`，金额容忍 1 分钱）
- 口径合规 → 集合比较（filters 的布尔开关 vs 字典的 required_filters）

LLM 负责表达，不负责判定。这一条能直接回答"审计是不是走过场"。

### 2. 口径必须是结构化字段，不能是散文

`filters` 里的六个键（region / start / end / exclude_cancelled / coupon_deduction /
subtract_refund）都是布尔或字符串，不是自然语言。

理由：如果计划写"按财务口径计算"，审计者无法判断合规与否，只能再问一次模型。
把口径变成布尔开关后，合规性变成一次集合差集计算——确定性、可测试、可复现。

### 3. 错误回到不同的层

| 错误类型 | 谁修 | 为什么 |
|---|---|---|
| 语法错 / 字段名错 | Analyst 自己（最多 1 次） | 同层能改，不必上升 |
| 口径错（L3 不合规） | Planner 重规划 | Analyst 改不出来，它不知道口径要求 |
| **数字对不上（只有 L2 不一致）** | **Analyst 带偏差重算，计划不动** | 计划是对的，错在实现；退给 Planner 只会原地打转 |
| 数据缺字段 | 系统拒绝作答 | 需要换数据，不是换代码 |

第三条是接真模型之后补上的（`orchestrator._next_hint`）。最初所有驳回都退回 Planner，
结果遇到"退款率分母用错"这类纯实现错误时，规划者改八次 filters 也修不好一个分母，
最后只能降级。把错误路由到"能修它的那一层"，系统才真正收敛。

"不同错误回不同层"是分层协作区别于流水线的分水岭。

### 4. 沙箱不继承环境变量

`core/sandbox.py` 用最小环境启动子进程：

```python
env = {"PATH": ..., "PYTHONIOENCODING": "utf-8"}
```

生成的代码拿不到 `IC_API_KEY`。配合 AST 静态检查（禁 `os`/`subprocess`/`socket`/`eval`）
和 30 秒超时，构成三层防护。

### 5. 有降级路径

三重终止条件（轮数 / 时间 / 同一问题反复驳回）任一触发，都会输出
"部分结论 + 未解决项 + 低置信度"，而不是抛异常。演示现场网络抖动也能出画面。

---

## 做消融实验（分析分 15% 的硬通货）

```bash
python -m eval.run_eval --mode full        # 完整系统
python -m eval.run_eval --mode no_critic   # 去掉审计
python -m eval.run_eval --mode single      # 单 agent 基线
```

三组对照才能回答最致命的质疑："你的提升是不是只因为多调了几次模型？"
输出里有准确率、分类型准确率、平均轮数、耗时、降级次数。

评估集的参考答案由 `tools/data_tools.l2_value()`（SQL）生成，
**使用前必须人工抽检 2~3 题**——评估集的答案来自可信来源，消融才成立。

---

## 已知局限（写进报告，也用来预判提问）

1. **评估集只有 12 题**，是分层构造（简单/口径/层级/数据质量/不可答），
   用于覆盖失败模式类型，不足以支撑统计显著性结论。
2. **L2 只覆盖"可复算"的指标**（求和、计数）。中位数、分位数这类指标需要另做路径。
3. **指标字典是手工维护的**，覆盖不到的口径会输出 `UNVERIFIED` 而不是假装通过。
4. **沙箱是课程级防护**，生产环境应换容器 + seccomp。
5. **离线剧本只服务演示**，真实泛化能力必须用真模型 + 完整评估集验证。

---

## 下一步可扩展（按性价比排序）

1. 多表 JOIN 的规划能力（现在只处理单表聚合 + 单次关联）
2. 图表生成（Analyst 产出 PNG，Reporter 引用）
3. 失败经验库（把 `critiques` 落成 `lessons.jsonl`，下次规划前检索）
4. 前端实时消息流（Streamlit，若要现场演示而非录屏）
