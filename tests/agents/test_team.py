import importlib.util
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.cli.utils.agent_loader import AgentLoader

from batchward.agents import tools
from batchward.agents.numbers_guard import NumbersGuardPlugin
from batchward.agents.team import DEFAULT_MODEL, RULES, build_app, build_team

ROOT = Path(__file__).resolve().parents[2]


def names(functions):
    return sorted(f.__name__ for f in functions)


def test_the_desk_routes_to_three_specialists():
    desk = build_team()
    assert desk.name == "desk"
    assert [agent.name for agent in desk.sub_agents] == ["analyst", "forecaster", "reporter"]
    assert desk.tools == []


def test_each_specialist_holds_exactly_its_tools():
    specialists = {agent.name: agent for agent in build_team().sub_agents}
    assert names(specialists["analyst"].tools) == names(tools.ANALYST_TOOLS)
    assert names(specialists["forecaster"].tools) == names(tools.FORECASTER_TOOLS)
    assert names(specialists["reporter"].tools) == names(tools.REPORTER_TOOLS)


def test_every_agent_is_bound_by_the_shared_rules():
    desk = build_team()
    for agent in [desk, *desk.sub_agents]:
        assert agent.instruction.startswith(RULES), agent.name
    assert "must come from a tool result" in RULES


def test_the_model_comes_from_the_environment_unless_given(monkeypatch):
    monkeypatch.delenv("BATCHWARD_MODEL", raising=False)
    assert build_team().model == DEFAULT_MODEL
    monkeypatch.setenv("BATCHWARD_MODEL", "gemini-test-model")
    assert {a.model for a in [build_team(), *build_team().sub_agents]} == {"gemini-test-model"}
    assert build_team(model="explicit").model == "explicit"


def test_adk_can_load_the_desk_entry_point():
    path = ROOT / "agents" / "desk" / "agent.py"
    spec = importlib.util.spec_from_file_location("desk_agent_entry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert isinstance(module.root_agent, LlmAgent)
    assert module.root_agent.name == "desk"


def test_the_app_runs_every_answer_through_the_numbers_guard_with_context_caching():
    app = build_app()
    assert app.name == "desk"
    assert app.root_agent.name == "desk"
    assert [type(plugin) for plugin in app.plugins] == [NumbersGuardPlugin]
    assert app.context_cache_config is not None


def test_adks_loader_picks_up_the_app_so_the_guard_cannot_be_skipped():
    loaded = AgentLoader(str(ROOT / "agents")).load_agent("desk")
    assert isinstance(loaded, App)
    assert any(isinstance(plugin, NumbersGuardPlugin) for plugin in loaded.plugins)
