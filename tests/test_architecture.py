"""架构规则由测试执行，而不是靠约定。"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent / "insightcrew"

# 每一层允许依赖的层。依赖只能指向内层。
LAYERS = {
    "kernel": {"kernel"},
    "domain": {"domain"},
    "roles": {"roles", "kernel", "domain"},
    "flows": {"flows", "roles", "kernel", "domain"},
    "adapters": {"adapters", "kernel", "domain"},
    "evals": {"evals", "adapters", "flows", "roles", "kernel", "domain"},
    "app": {"app", "evals", "adapters", "flows", "roles", "kernel", "domain"},   # 最外层：没有任何层依赖它
}

# 每一层允许引入的第三方包。表里没有的包，那一层就不认识。
# 要点：domain 只认识 Pydantic；智能体库只出现在 kernel 和 adapters；flows 和 roles 一个第三方包都不引入。
THIRD_PARTY = {
    "kernel": {"pydantic", "pydantic_core", "pydantic_ai"},
    "domain": {"pydantic"},
    "roles": set(),
    "flows": set(),
    "adapters": {"pydantic", "pydantic_ai", "httpx2"},
    "evals": {"pydantic"},
    "app": {"pydantic", "pydantic_settings", "dotenv", "fastapi", "uvicorn", "typer"},
}


def files() -> list[Path]:
    return [p for p in ROOT.rglob("*.py") if len(p.relative_to(ROOT).parts) > 1]


def imports(path: Path):
    """产出 (顶层包名, 项目内的层名或 None, 引入的名字)。"""
    layer = path.relative_to(ROOT).parts[0]
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], None, alias.name
        elif isinstance(node, ast.ImportFrom):
            names = [a.name for a in node.names]
            if node.level == 0:
                parts = (node.module or "").split(".")
                inner = parts[1] if parts[0] == "insightcrew" and len(parts) > 1 else None
                for name in names:
                    yield parts[0], inner, name
            elif node.level == 1:
                for name in names:
                    yield "insightcrew", layer, name
            else:
                target = (node.module or "").split(".")[0] or names[0]
                for name in names:
                    yield "insightcrew", target, name


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_dependencies_point_inward():
    for path in files():
        layer = path.relative_to(ROOT).parts[0]
        for top, target, _ in imports(path):
            if top == "insightcrew" and target is not None:
                assert target in LAYERS[layer], f"{rel(path)}（{layer} 层）不允许依赖 {target} 层"


def test_each_layer_knows_only_its_own_libraries():
    for path in files():
        layer = path.relative_to(ROOT).parts[0]
        for top, _, _ in imports(path):
            if top == "insightcrew" or top in sys.stdlib_module_names:
                continue
            assert top in THIRD_PARTY[layer], f"{rel(path)}（{layer} 层）不允许引入第三方包 {top}"


def test_only_the_runtime_runs_an_agent():
    users = sorted({rel(p) for p in files() for top, _, name in imports(p)
                    if top == "pydantic_ai" and name == "Agent"})
    assert users == ["kernel/runtime.py"]


def test_two_computation_paths_are_independent():
    for name, other in (("indicators", "recompute"), ("recompute", "indicators")):
        path = ROOT / "domain" / f"{name}.py"
        seen = {x for _, target, imported in imports(path) for x in (target or "", imported)}
        assert not any(other in x for x in seen), f"{name}.py 不允许引入 {other}"
        assert all(top in sys.stdlib_module_names for top, _, _ in imports(path)), f"{name}.py 只用标准库"


def test_two_fundamentals_paths_are_independent():
    """基本面同样两条独立路径（BLUEPRINT 18.6）：互不 import，且只用标准库。"""
    for name, other in (("fundamentals", "fundamentals_sql"), ("fundamentals_sql", "fundamentals")):
        path = ROOT / "domain" / f"{name}.py"
        seen = {x for _, target, imported in imports(path) for x in (target or "", imported)}
        assert not any(other in x for x in seen), f"{name}.py 不允许引入 {other}"
        assert all(top in sys.stdlib_module_names for top, _, _ in imports(path)), f"{name}.py 只用标准库"


def test_roles_see_only_what_they_declare_and_judges_never_see_the_debate():
    from insightcrew.roles import ALL, JUDGES
    assert len({r.writes for r in ALL}) == len(ALL), "两个角色不能写同一个键"
    for judge in JUDGES:
        assert not {"bull/argument", "bear/argument"} & set(judge.reads)
        assert not any(k.startswith("judge.") for k in judge.reads), "评审看不到彼此的结论"
    for role in ALL:
        assert role.name == "trader" or "portfolio/state" not in role.reads


def test_channel_analysts_read_only_their_own_evidence():
    """两个新分析师各读各的证据（BLUEPRINT 18.9 的 withheld 表，WP13 的两行）。"""
    from insightcrew.roles import DISCLOSURES, FUNDAMENTALS
    assert set(FUNDAMENTALS.reads) == {"ledger/instrument", "fundamentals/facts",
                                       "fundamentals/card", "audit/fundamentals"}
    assert not {"ledger/facts", "ledger/card", "disclosures/items", "disclosures/analysis",
                "technical/analysis"} & set(FUNDAMENTALS.reads), "基本面分析师不看价格事实与别人的输出"
    assert set(DISCLOSURES.reads) == {"ledger/instrument", "disclosures/items",
                                      "disclosures/card", "audit/disclosures"}
    assert not {"ledger/facts", "ledger/card", "fundamentals/facts", "fundamentals/analysis",
                "technical/analysis"} & set(DISCLOSURES.reads), "公告分析师不看任何事实与别人的输出"


def test_the_views_that_grew_with_channels():
    """WP16 长出来的视图（18.9 的表）：研究员与主管读两个通道的分析，评审另读原始证据；
    技术分析师与交易员一个通道键都不读。"""
    from insightcrew.roles import BEAR, BULL, JUDGES, MANAGER, TECHNICAL, TRADER
    brief = {"fundamentals/status", "fundamentals/analysis",
             "disclosures/status", "disclosures/analysis"}
    for role in (BULL, BEAR, MANAGER):
        assert brief <= set(role.reads), f"{role.name} 要能看到两个通道的分析与状态"
    for judge in JUDGES:
        assert brief | {"fundamentals/facts", "disclosures/items"} <= set(judge.reads)
        assert not {"bull/argument", "bear/argument"} & set(judge.reads), "评审仍然不看辩论"
    for role in (TECHNICAL, TRADER):
        assert not any(key.startswith(("fundamentals/", "disclosures/")) for key in role.reads), \
            f"{role.name} 不读任何通道键（18.9）"


def test_every_contract_is_frozen_and_closed():
    from insightcrew.domain import types
    from insightcrew.roles import ALL
    contracts = ({r.output for r in ALL}
                 | {types.Fact, types.Finding, types.Outcome, types.ChannelStatus,
                    types.FundamentalsCard, types.Item, types.DisclosureCard, types.Signal,
                    types.DisclosureAnalysis, types.AnalysisSeq})
    for contract in contracts:
        assert contract.model_config.get("frozen") is True, f"{contract.__name__} 必须不可变"
        assert contract.model_config.get("extra") == "forbid", f"{contract.__name__} 不得接受契约外的字段"
