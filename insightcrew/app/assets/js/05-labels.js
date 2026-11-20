/* 05-labels.js —— 每一个中文标签：阶段、角色、黑板键、规则、审计检查、停机原因、通道状态。
   已有的表保持原名：STAGES、LANES、ACTOR_STAGE、KEY_STAGE、CHANNEL_STAGES、ANALYST_KEYS、GROUP_NAME。 */

const STAGES = [["intake", "范围闸门", "code"], ["ledger", "事实账本", "code"], ["technical", "技术分析", "model"],
                ["fundamentals", "基本面分析", "model"], ["disclosures", "公告分析", "model"],
                ["news", "新闻分析", "model"], ["sentiment", "情感分析", "model"],
                ["debate", "多空辩论", "model"], ["manager", "研究主管", "model"], ["trader", "交易员", "model"],
                ["risk", "风险辩论", "model"],
                ["audit", "审计", "code"], ["judges", "评审", "model"], ["report", "报告", "code"]];
const STAGE_NAME = Object.fromEntries(STAGES.map(([key, name]) => [key, name]));
STAGE_NAME.evidence = "证据";
const STAGE_ORDER = STAGES.map(([key]) => key);
const ANALYST_KEYS = ["technical", "fundamentals", "disclosures", "news", "sentiment"];   // evidence 之后并行的几位
/* 一段什么时候出现在流水线里：通道段看自己的状态键，风险段看第一个发言的键 */
const CHANNEL_STAGES = { fundamentals: "fundamentals/status", disclosures: "disclosures/status",
                         news: "news/status", sentiment: "sentiment/status", risk: "risk/aggressive" };
const ACTOR_STAGE = { intake: "intake", ledger: "ledger", evidence: "ledger", technical: "technical",
                      fundamentals: "fundamentals", disclosures: "disclosures", news: "news",
                      sentiment: "sentiment", "risk.aggressive": "risk", "risk.neutral": "risk",
                      "risk.conservative": "risk",
                      bull: "debate", bear: "debate",
                      manager: "manager", trader: "trader", audit: "audit" };
/* 产物键前缀 → 流水线段：证据阶段代写的键归到各自的段 */
const KEY_STAGE = [["judges/", "judges"], ["run/", "report"], ["news/", "news"], ["macro/", "news"],
                   ["sentiment/", "sentiment"], ["risk/", "risk"], ["tools/", "technical"]];
const STAGE_LANE = { technical: "technical", fundamentals: "fundamentals", disclosures: "disclosures",
                     news: "news", sentiment: "sentiment", debate: "bull", manager: "manager", trader: "trader" };
const LANES = [["app", "系统", "code"], ["intake", "范围闸门", "code"], ["ledger", "事实账本", "code"],
               ["evidence", "证据", "code"],
               ["technical", "技术分析师", "model"], ["fundamentals", "基本面分析师", "model"],
               ["disclosures", "公告分析师", "model"], ["news", "新闻分析师", "model"],
               ["sentiment", "情感分析师", "model"],
               ["bull", "多方研究员", "model"], ["bear", "空方研究员", "model"],
               ["manager", "研究主管", "model"], ["trader", "交易员", "model"],
               ["risk.aggressive", "激进风控", "model"], ["risk.conservative", "保守风控", "model"],
               ["risk.neutral", "中立风控", "model"], ["audit", "审计", "code"],
               ["judge.evidence", "证据评审", "model"], ["judge.conservative", "保守评审", "model"],
               ["judge.aggressive", "进取评审", "model"], ["flow", "流程", "code"]];
const GROUP_NAME = { technical: "技术分析师", fundamentals: "基本面分析师", disclosures: "公告分析师",
                     news: "新闻分析师", sentiment: "情感分析师",
                     bull: "多方研究员", bear: "空方研究员", manager: "研究主管",
                     trader: "交易员", risk: "风险辩论（三方）", judge: "评审（三个视角）" };
const CHANNEL_STATE = {                       // 每条通道自己的三态文案（empty 的说法各不相同）
  fundamentals: { available: "可用", empty: "没有已申报的年度报表", unavailable: "不可用" },
  disclosures: { available: "可用", empty: "窗口内没有公告", unavailable: "不可用" },
  news: { available: "可用", empty: "窗口内没有新闻", unavailable: "不可用" },
  sentiment: { available: "可用", empty: "窗口内没有帖子", unavailable: "不可用" },
  macro: { available: "可用", empty: "没有宏观序列", unavailable: "不可用" } };
const DIRECTION_TEXT = { positive: "正面", negative: "负面", neutral: "中性" };
const STANCE_TEXT = { positive: "正面", mixed: "混合", neutral: "中性", negative: "负面" };
const RISK_NAME = { aggressive: "激进风控", conservative: "保守风控", neutral: "中立风控" };
const PSTATE_TEXT = { not_provided: "未提供组合", flat: "空仓", held: "持有", unavailable: "不可用" };
const BAND_TEXT = { bullish: "强烈看多", mildly_bullish: "略偏多", neutral: "中性", mixed: "多空分歧",
                    mildly_bearish: "略偏空", bearish: "强烈看空" };
const TAG_TEXT = { bullish: "看多", bearish: "看空", none: "无标签" };
const HIT_TEXT = { stop: "止损", target: "目标", neither: "均未触及", "n/a": "不适用" };
const JUDGES = [["evidence", "证据评审"], ["conservative", "保守评审"], ["aggressive", "进取评审"]];
const REASON = {
  invalid_ticker: "代码格式不正确", unknown_ticker: "代码不在数据集里", no_data: "该日期之前没有已收盘的行情",
  future_date: "分析日期晚于今天", stale_data: "数据过旧", insufficient_history: "历史数据不足",
  source_unavailable: "行情数据源不可用", ledger_inconsistent: "两条计算路径的结果不一致",
  malformed_output: "模型输出两次都不合契约", stagnation: "同样的问题连续两次未被修正",
  audit_failed: "多次打回后审计仍未通过", contested: "评审多数反对，未达成共识",
  budget_exhausted: "token 预算已用尽", time_exhausted: "时间预算已用尽",
  calls_exhausted: "模型请求次数已用尽", model_unavailable: "模型不可用" };
const STATUS_TEXT = { delivered: "交付", review: "REVIEW", running: "进行中", interrupted: "已中断",
                      incomplete: "未正常结束" };
const MODE_TEXT = { scripted: "脚本化", model: "真实模型", replay: "回放" };
/* 模型里的种类徽章（fold 的 facts/items 各带一个 kind） */
const FACT_KIND_TEXT = { ledger: "账本", fundamentals: "基本面", tool: "工具", macro: "宏观" };
const ITEM_KIND_TEXT = { disclosure: "公告", news: "新闻", market: "预测市场", post: "帖子", lesson: "课程" };
/* 规则手册（data/rules.toml）里每条规则的中文含义；标签缺失时回落到键名本身 */
const RULE_LABEL = {
  data_fresh: "数据必须足够新：最后一根已结算 K 线距分析日不超过上限天数",
  min_history: "历史必须足够长：已结算 K 线至少要有下限根数，否则指标算不出来",
  action_matches_rating: "交易动作必须与评级方向一致，不能评级看空却提议买入",
  hold_is_flat: "Hold 动作必须空仓：不给任何价位，仓位为 0",
  sell_requires_position: "Sell 只能减仓：必须有持仓，卖出不超过持仓，且不给出任何价位",
  stop_required: "买入必须同时给出大于 0 的入场价与止损价",
  stop_side: "止损必须低于入场价，目标价（如给出）必须高于入场价，方向不能反",
  size_range: "仓位必须是 (0, 100] 区间内的百分比",
  max_risk_per_trade_pct: "止损被触发时的亏损，占权益的百分比不得超过上限",
  max_position_pct: "新仓加上现有持仓后的总仓位，占权益的百分比不得超过上限",
  min_reward_risk: "潜在收益与风险之比，至少要有下限倍数；没有目标价时不适用",
  min_stop_distance_atr: "止损距离至少是多少倍 ATR，避免止损贴着市价被噪音扫掉",
  entry_near_market: "入场价偏离市价不超过多少倍 ATR，避免远离市价的追价",
  cash_sufficient: "新仓占用的现金不超过可用现金占比；没有给出现金占比时不适用" };
/* 审计检查名（insightcrew/domain/）的中文含义 */
const CHECK_LABEL = {
  analysis_has_claims: "分析必须给出足够多条引用事实的论断",
  claim_cites_known_fact: "论断引用的事实必须在账本里",
  claim_value_matches_fact: "论断转述的数值必须与账本一致",
  disclosures_has_signals: "公告分析必须给出信号",
  signal_cites_known_item: "信号引用的公告条目必须在列表中",
  news_has_signals: "窗口内有新闻时，新闻分析必须给出信号",
  news_signal_cites_known_item: "新闻信号引用的条目必须在列表中",
  sentiment_has_signals: "窗口内有帖子时，情感分析必须给出信号",
  sentiment_signal_cites_known_post: "情感信号引用的帖子必须在列表中",
  sentiment_score_matches_band: "情绪分必须落在所述情绪带的区间内",
  point_cites_claimed_fact: "论点必须引用被分析师引用过的证据",
  horizon_in_range: "持有期必须在允许的区间内",
  proposal_cites_known_fact: "方案引用的事实必须在账本里；Buy 方案必须给出数字依据",
  horizon_matches_thesis: "方案的持有期必须与论点一致",
  lesson_cites_known_field: "教训必须引用结果里的字段",
  lesson_cites_outcome: "教训引用的字段必须是这次结果里存在的",
  lesson_text_has_no_numbers: "教训文字不得出现数字，数字只留在结果记录里" };
