# agents.md — InsightCrew 工作指南

> 初始化自 2026-11-17/18 会话:启动验证、架构分析、代码溯源、评分指南核对、TradingAgents 对标。
> 目的:让后续在此仓库工作的人与 AI 助手快速获得正确上下文,并守住核心设计不被破坏。

## 1. 项目事实

- **课程**:COMP5584 Group Project,截止 **2026-11-23 23:59**,Canvas 提交单个 zip,命名 `COMP5584+GroupProject+Group<ID>.zip`
- **本质**:带验证回路的多智能体数据分析助手。5 角色(Profiler / Planner / Analyst / Critic / Reporter),黑板共享状态,消息总线留痕;核心卖点是 Critic 独立审计——答不了的明确拒绝,不给假数字
- **技术栈**:纯 Python 标准库,零第三方依赖(刻意不用 LangGraph/CrewAI)。**实际需要 Python 3.9+**(`orchestrator.py` 用了 `str.removesuffix`;README 声称 3.8+ 是错的)
- **运行时模型**:`deepseek-chat` @ `https://api.deepseek.com/v1`,任意 OpenAI 兼容接口,经 `.env` 配置(`IC_API_KEY` / `IC_BASE_URL` / `IC_MODEL`)
- **离线模式**:无 key 自动进入 ScriptedLLM 剧本(`core/scripts.py`),编排/沙箱/审计全部真实运行,只有"写代码"一步是规则生成;`IC_FORCE_OFFLINE=1` 可强制
- **数据**:合成数据,`data/make_data.py` 在 `seed(42)` 下可逐字节复现 orders.csv(1200 行)/ refunds.csv(166 行)
- **溯源**:代码由 LLM 在他人的 Windows 机器上约 3 小时生成,经微信 zip 传入本机。注释叙事大体可信,但个别声明未经验证(如"3.8.3 实测")

## 2. 常用命令

```bash
python3 run_demo.py                                # 默认问题,产物在 runs/<时间戳>/
python3 ask.py "八月华南区的营收是多少?"             # 命令行提问(无参则交互)
python3 ask.py "2026年第三季度华东区的营收是多少？" --compare   # 有/无审计对比
python3 -m ui.render_report runs/<时间戳>           # 重新生成 HTML 回放
python3 check_api.py                               # 真模型连通性体检(--run 真跑一题)

python -m eval.run_eval --mode full|no_critic|single   # 三组消融
python -m eval.online_eval                         # 真模型 12 题(需 key)
python -m eval.consistency_test                    # 字典 vs 实现漂移检查(改指标后必须过)
python -m eval.mock_online_test                    # 本地假模型走完整 HTTP 链路

cd data && python3 make_data.py                    # 复现数据(应与仓库文件逐字节一致)
```

## 3. 架构地图

| 文件 | 职责 |
|---|---|
| `core/orchestrator.py` | 唯一驱动循环:顺序调度、反馈边、三重终止(轮数/时间/停滞)、降级兜底、口径归一、覆盖守卫 |
| `core/blackboard.py` | 共享黑板:版本化 artifact(a1/a2)、结构化 filters、critiques |
| `core/messages.py` | 消息总线:6 种 intent 枚举,全部落盘 trace.jsonl(注意:只写留痕,真实数据流走参数直传+黑板) |
| `core/llm.py` | urllib 直连 OpenAI 兼容接口;离线切 ScriptedLLM;JSON 抠取带围栏兜底 |
| `core/sandbox.py` | 三层防护:AST 禁危险模块 + 子进程最小环境 + 超时;`RESULT:{json}` 输出契约 |
| `core/question_parser.py` | 规则解析(地区/时间/指标)+ 范围检查,答不了当场拒绝 |
| `agents/` | 5 角色;base.py 极薄,差异在 prompt/可见信息/工具 |
| `tools/metrics.py` | 四指标的 SQL 实现(Critic 的 L2 第二路径,只被 Critic 用) |
| `tools/metric_dict.py` + `data/metric_dict.json` | 口径字典(L3 知识来源):formula / required_filters / pitfall |
| `ui/render_report.py` | 单文件 HTML 时间轴回放 |

## 4. 不可破坏的设计不变量(改代码前必读)

1. **Critic 零 LLM**:`agents/critic.py` 全部确定性代码(数值=容差比较 TOL=0.01,口径=集合比较)。用 LLM 做审计判定等于毁掉项目核心论点
2. **口径结构化**:filters 六键(region/start/end/exclude_cancelled/coupon_deduction/subtract_refund)必须是布尔/字符串,不许退化成自然语言
3. **双路径独立**:`tools/metrics.py` 只被 Critic 使用;Analyst 的 prompt 只给指标 definition、**不得出现 required_filters**,否则"独立复算"退化成抄写
4. **错误分层路由**:L3 口径错 → 退 Planner 重规划(hint 清空);仅 L2 数值错 → Analyst 带偏差 hint 同层重算,计划不动(`orchestrator._next_hint`)
5. **Reporter 只表达不计算**:报告数字只能来自黑板 artifact,禁止重算
6. **沙箱三层防护不削弱**:AST 检查 + 子进程**不继承 os.environ**(防 API key 泄漏给生成代码)+ 超时;`-I -X utf8` 两个标志缺一不可
7. **指标三处同步**:新增指标必须同时改 `tools/metrics.py`(SUPPORTED + _DISPATCH)、`data/metric_dict.json`、`core/question_parser.py`(SUPPORTED_METRICS),改完跑 `eval.consistency_test`
8. **降级优先于崩溃**:异常统一收口成降级输出;筛选匹配 0 行、区间无交集必须拒绝作答,不许把 0 当答案交付
9. **零依赖**:requirements.txt 里的第三方项全是注释掉的可选项;沙箱内生成代码同样只许用 csv/json/math/statistics

## 5. 已知的坑

- README/注释声称 Python 3.8 兼容——**不实**,3.9+ 才能跑
- `使用说明.md` 第 31 行句子截断(生成时 token 耗尽),可顺手修复
- `start.bat` / `demo.bat` 面向 Windows(项目源自 Windows 机器);macOS 直接用 `python3` 命令
- zip 打包中文文件名会 GBK 乱码,重新打包提交时注意(要求单 zip,指南原文)
- `runs/`、`__pycache__/` 是运行产物,提交打包时排除;`eval/result_*.json` 评测存档要保留(README 引用了其中数字)
- 评分指南从未提及"零依赖";`orchestrator.py:9` 把"不用框架"说成评分点是演绎,答辩时别当理由用(正确说法:为了可控、可审计、可复现)

## 6. 评分与剩余优先级(截至 2026-11-18,剩 5 天)

评分权重:系统设计 30% > 实现 25% > 问题建模 / 分析创新 / 展示各 15%。指南明说"技术复杂度不必然加分",重点是 design **well motivated, clearly implemented**。

优先级:
1. **提交物(必做)**:≤10 分钟演示视频;slides 按指南四段结构(问题动机 / 系统设计 / 实现 / 演示与结果);个人贡献声明(指南明文:无实质贡献者不得分)
2. **可选增强(各约半天)**:模型分层(`IC_QUICK_MODEL`/`IC_DEEP_MODEL`,改 config + llm 各几行,顺带多一个消融维度);memory.jsonl 决策落盘(`Orchestrator._finish` 追加,Planner 检索注入,即 README 未来工作 #3)
3. **已评估后放弃**:as_of 数据快照、解释层辩论——时间不够,风险大于收益
4. **答辩素材**:与 TradingAgents(arXiv:2412.20138)对比——对方用同源 LLM 辩论验证,本项目用确定性双路径验证(理由:同源模型互审只会附和)。现成的 related work 亮点;指南推荐机制清单里的 planner–executor / shared workspace / reflection & verification / tool-using 本项目全部命中
