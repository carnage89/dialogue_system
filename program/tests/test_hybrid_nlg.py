import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dialog_system.dialog_manager import DialogManager
from dialog_system.game_data import get_characters_data
from dialog_system.nlg import GeneratedText


class FakeLLMNLG:
    def __init__(self, text="LLM answer"):
        self.text = text
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        return [
            GeneratedText(
                text=self.text,
                intent=request["intent"],
                tone=request["tone"],
                score=0.98,
                source="LLM: fake",
            )
        ]


def make_hybrid_manager(tmp_path, llm_text="LLM contextual answer"):
    manager = DialogManager(
        get_characters_data(),
        use_llm=False,
        storage_path=str(tmp_path / "sessions.sqlite3"),
    )
    manager.llm_nlg = FakeLLMNLG(llm_text)
    return manager


def generate_for_intent(manager, state, intent):
    npc_id = "npc_medieval_merchant"
    npc_data = get_characters_data()[npc_id]
    plan = manager.planner.plan(
        intent=intent,
        npc_persona=npc_data.get("persona", "neutral"),
        state=state.get_state_snapshot(),
        player_relationship=state.get_relationship(npc_id),
    )
    return manager._generate_text(npc_data, plan, state, intent, "test", npc_id)


def test_hybrid_keeps_template_for_simple_intents(tmp_path):
    manager = make_hybrid_manager(tmp_path, "LLM should not answer greeting")
    fake_llm = manager.llm_nlg
    state = manager.create_session("s1", "Hero")

    result = generate_for_intent(manager, state, "greet")

    assert result[0].source == "Template"
    assert fake_llm.calls == []


def test_hybrid_uses_llm_for_open_intents(tmp_path):
    manager = make_hybrid_manager(tmp_path, "LLM contextual answer")
    fake_llm = manager.llm_nlg
    state = manager.create_session("s1", "Hero")

    result = generate_for_intent(manager, state, "provide_info")

    assert result[0].source == "LLM: fake"
    assert result[0].text == "LLM contextual answer"
    assert fake_llm.calls
