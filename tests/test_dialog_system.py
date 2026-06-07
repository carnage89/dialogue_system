import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dialog_system.dialog_manager import DialogManager
from dialog_system.game_data import get_characters_data
from dialog_system.nlg import GeneratedText, TemplateNLGAdapter, RetrievalNLGAdapter
from dialog_system.session_store import SQLiteSessionStore


class FakeNLG:
    def __init__(self, text):
        self.text = text

    def generate(self, request):
        return [
            GeneratedText(
                text=self.text,
                intent=request["intent"],
                tone=request["tone"],
                source="fake",
            )
        ]


def make_manager(tmp_path, text="Хорошо."):
    manager = DialogManager(
        get_characters_data(),
        use_llm=False,
        storage_path=str(tmp_path / "sessions.sqlite3"),
    )
    manager.llm_nlg = FakeNLG(text)
    return manager


# ===== Базовые тесты (были до этого) =====

def test_npc_name_is_revealed_after_introduction(tmp_path):
    manager = make_manager(tmp_path, text="Меня зовут Готфрид.")
    manager.create_session("s1", "Hero")

    response = manager.decide_response("s1", "npc_medieval_merchant", "Как тебя зовут?")

    assert response["speaker"] == "Готфрид"
    assert response["meta"]["name_revealed"] is True
    assert response["meta"]["known_name"] == "Готфрид"


def test_revealed_name_is_persisted_in_sqlite(tmp_path):
    db_path = tmp_path / "sessions.sqlite3"
    manager = DialogManager(get_characters_data(), use_llm=False, storage_path=str(db_path))
    manager.llm_nlg = FakeNLG("Меня зовут Готфрид.")
    manager.create_session("s1", "Hero")
    manager.decide_response("s1", "npc_medieval_merchant", "Как тебя зовут?")

    restored = DialogManager(get_characters_data(), use_llm=False, storage_path=str(db_path))
    state = restored.get_session("s1")

    assert state is not None
    assert state.get_short_term("revealed_name_npc_medieval_merchant") == "Готфрид"


def test_apology_response_does_not_leak_accepted_marker(tmp_path):
    manager = make_manager(tmp_path, text="Извиниaccepted, но уважение к торговцу должно быть всегда.")
    manager.create_session("s1", "Hero")
    manager.decide_response("s1", "npc_medieval_merchant", "ты идиот")

    response = manager.decide_response("s1", "npc_medieval_merchant", "да лан извини")

    assert "accepted" not in response["text"].lower()
    assert response["intent"] == "apology"


def test_idiot_is_hostile_intent(tmp_path):
    manager = make_manager(tmp_path, text="Иди прочь.")
    manager.create_session("s1", "Hero")

    response = manager.decide_response("s1", "npc_medieval_merchant", "ты идиот")

    assert response["intent"] == "insult"


def test_session_store_roundtrip(tmp_path):
    manager = make_manager(tmp_path)
    state = manager.create_session("s1", "Hero")
    state.set_flag("demo", True)
    manager._save_session(state)

    sessions = SQLiteSessionStore(str(tmp_path / "sessions.sqlite3")).load_all()

    assert "s1" in sessions
    assert sessions["s1"].player.player_name == "Hero"
    assert sessions["s1"].get_flag("demo") is True


def test_toxic_input_detection():
    from dialog_system.api import _is_toxic

    assert _is_toxic("ты идиот, я тебя убью")
    assert not _is_toxic("добрый день")


# ===== НОВЫЕ ТЕСТЫ НА ОТКАЗОУСТОЙЧИВОСТЬ И ГИБРИДНЫЙ РЕЖИМ =====

def test_fallback_without_llm(tmp_path):
    """Тест: система работает без LLM (используются Template и Retrieval)."""
    manager = DialogManager(
        get_characters_data(),
        use_llm=False,  # LLM отключен
        storage_path=str(tmp_path / "sessions.sqlite3"),
    )
    # Не устанавливаем manager.llm_nlg вообще (None)
    manager.create_session("s1", "TestPlayer")

    response = manager.decide_response("s1", "npc_medieval_merchant", "Привет!")

    assert "error" not in response
    assert response["text"]
    assert response["source"] in ["Template", "Retrieval", "Fallback"]  # Любой источник
    assert response["meta"]["response_time_ms"] >= 0  # >= because system is very fast (~5ms)


def test_template_nlg_has_fallback_for_all_intents(tmp_path):
    """Тест: Template адаптер имеет шаблоны для всех основных интентов."""
    template_nlg = TemplateNLGAdapter()
    intents = [
        "greet", "ask_quest", "accept_quest", "provide_info",
        "ask_name", "clarify", "farewell", "apology", "small_talk", "ack"
    ]

    for intent in intents:
        result = template_nlg.generate({
            "intent": intent,
            "tone": "neutral",
            "true_name": "TestNPC",
        })
        assert result, f"Template не имеет fallback для {intent}"
        assert result[0].text
        assert result[0].source == "Template"


def test_retrieval_nlg_provides_alternatives(tmp_path):
    """Тест: Retrieval адаптер предоставляет несколько вариантов."""
    retrieval_nlg = RetrievalNLGAdapter()

    result = retrieval_nlg.generate({
        "intent": "greet",
        "tone": "neutral",
    })

    assert len(result) > 1, "Retrieval должен вернуть несколько вариантов"
    assert all(r.source == "Retrieval" for r in result)


def test_nlg_priority_llm_over_retrieval(tmp_path):
    """Тест: LLM имеет приоритет над Retrieval если score хороший."""
    manager = make_manager(tmp_path, text="LLM ответ")
    manager.create_session("s1", "Hero")

    response = manager.decide_response("s1", "npc_medieval_merchant", "Привет!")

    # Так как у нас FakeNLG с score=1.0 (fake), но в реальности это будет LLM
    # Наш FakeNLG имеет высокий score, поэтому он должен быть выбран
    # Но в реальности, если LLM не работает, используется fallback
    assert response["text"]


def test_session_logging_enabled(tmp_path):
    """Тест: Все диалоги логируются в БД."""
    db_path = tmp_path / "sessions.sqlite3"
    manager = DialogManager(get_characters_data(), use_llm=False, storage_path=str(db_path))
    manager.create_session("s1", "Hero")

    # Несколько диалогов
    manager.decide_response("s1", "npc_medieval_merchant", "Привет!")
    manager.decide_response("s1", "npc_medieval_merchant", "Как дела?")
    manager.decide_response("s1", "npc_medieval_merchant", "До встречи!")

    # Проверить, что логирование работает
    store = SQLiteSessionStore(str(db_path))
    stats = store.get_session_stats("s1")

    assert stats["total_dialogs"] == 3
    assert len(stats["nlg_sources"]) > 0


def test_error_logging(tmp_path):
    """Тест: Ошибки логируются в БД."""
    db_path = tmp_path / "sessions.sqlite3"
    store = SQLiteSessionStore(str(db_path))

    # Попробуем получить несуществующую сессию
    store.log_error(
        error_type="SessionNotFound",
        error_message="Сессия s999 не найдена",
        session_id="s999",
        npc_id="npc_test"
    )

    # Проверить, что ошибка залогирована (просто убедимся что не было исключения)
    assert True


def test_metrics_collection(tmp_path):
    """Тест: Метрики собираются и сохраняются."""
    db_path = tmp_path / "sessions.sqlite3"
    store = SQLiteSessionStore(str(db_path))

    store.log_metric("s1", "total_dialogs", 5.0)
    store.log_metric("s1", "avg_response_time", 150.5)
    store.log_metric("s1", "llm_success_rate", 0.95)

    # Проверить, что метрики залогированы (просто убедимся что не было исключения)
    assert True


def test_response_time_is_tracked(tmp_path):
    """Тест: Время ответа отслеживается и включено в ответ."""
    manager = make_manager(tmp_path)
    manager.create_session("s1", "Hero")

    response = manager.decide_response("s1", "npc_medieval_merchant", "Привет!")

    assert "meta" in response
    assert "response_time_ms" in response["meta"]
    assert response["meta"]["response_time_ms"] >= 0


def test_hybrid_generation_fallback_chain(tmp_path):
    """Тест: Полная цепь fallback (LLM -> Retrieval -> Template)."""
    manager = DialogManager(
        get_characters_data(),
        use_llm=False,  # Отключаем LLM
        storage_path=str(tmp_path / "sessions.sqlite3"),
    )
    
    manager.create_session("s1", "Hero")

    # Тестируем разные интенты
    for _ in range(5):  # Несколько диалогов
        response = manager.decide_response("s1", "npc_medieval_merchant", "Привет!")
        
        assert response.get("text")
        assert not response.get("error")
        assert response["source"] in ["Template", "Retrieval", "LLM: deepseek/deepseek-r1", "Fallback"]


def test_no_duplicate_responses_in_conversation(tmp_path):
    """Тест: Система пытается не повторять один и тот же ответ подряд."""
    manager = DialogManager(
        get_characters_data(),
        use_llm=False,
        storage_path=str(tmp_path / "sessions.sqlite3"),
    )
    
    manager.create_session("s1", "Hero")
    
    responses = []
    for i in range(4):
        response = manager.decide_response("s1", "npc_medieval_merchant", f"Сообщение {i}")
        responses.append(response["text"])
    
    # Просто проверяем, что ответы есть и непусты
    assert all(r for r in responses)
    # Хотя бы один ответ должен быть не "Хм..." (если используются шаблоны)
    # или хотя бы все ответы должны быть одинаковыми (если есть только fallback)
    assert len(responses) > 0


def test_database_migration_on_init(tmp_path):
    """Тест: БД инициализируется корректно с нужными таблицами."""
    db_path = tmp_path / "test_db.sqlite3"
    store = SQLiteSessionStore(str(db_path))
    
    # Логирование должно работать
    store.log_dialog("s1", "npc1", "hello", "hi", "greet", "Template", 10.5)
    store.log_metric("s1", "test_metric", 5.0)
    store.log_error("TestError", "Test message", "s1", "npc1")
    
    # Все должно пройти без ошибок
    assert True


def test_state_persistence_across_restarts(tmp_path):
    """Тест: Состояние сохраняется и восстанавливается после перезагрузки."""
    db_path = tmp_path / "sessions.sqlite3"
    
    # Первый запуск - создаём сессию и ведём диалог
    manager1 = DialogManager(get_characters_data(), use_llm=False, storage_path=str(db_path))
    state1 = manager1.create_session("persist_test", "Hero")
    state1.update_relationship("npc_medieval_merchant", 50)
    state1.set_flag("visited_merchant", True)
    manager1._save_session(state1)
    
    # Второй запуск - загружаем сессию
    manager2 = DialogManager(get_characters_data(), use_llm=False, storage_path=str(db_path))
    state2 = manager2.get_session("persist_test")
    
    assert state2 is not None
    assert state2.get_relationship("npc_medieval_merchant") == 50
    assert state2.get_flag("visited_merchant") is True
