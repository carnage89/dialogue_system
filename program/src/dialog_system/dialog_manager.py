"""
DialogManager: главный управляющий логикой диалога.
"""
from typing import Dict, List, Optional, Any
import os
import random
import re
import time
from .state_tracker import StateTracker
from .content_planner import ContentPlanner
from .nlg import LLMNLGAdapter, TemplateNLGAdapter, RetrievalNLGAdapter, GeneratedText
from .session_store import SQLiteSessionStore


class DialogManager:
    """Управляет потоком диалога, интегрирует все компоненты."""

    def __init__(self, characters_data: Dict[str, Any], use_llm: bool = True,
                 storage_path: Optional[str] = None):
        self.characters = characters_data
        self.planner = ContentPlanner()
        self.store = SQLiteSessionStore(
            storage_path or os.getenv("DIALOG_SESSION_DB", "dialog_sessions.sqlite3")
        )

        # Инициализировать Template и Retrieval адаптеры (всегда работают)
        self.template_nlg = TemplateNLGAdapter()
        self.retrieval_nlg = RetrievalNLGAdapter()

        # Инициализировать LLM адаптер (если доступен)
        try:
            self.llm_nlg = LLMNLGAdapter() if use_llm else None
        except:
            print("[WARNING] LLM не инициализирован, используются шаблоны и retrieval.")
            self.llm_nlg = None

        self.sessions: Dict[str, StateTracker] = self.store.load_all()

    def create_session(self, session_id: str, player_name: str = "Player") -> StateTracker:
        """Создать новую сессию диалога."""
        state = StateTracker(session_id, player_name)
        self.sessions[session_id] = state
        self._save_session(state)
        return state

    def get_session(self, session_id: str) -> Optional[StateTracker]:
        """Получить существующую сессию."""
        return self.sessions.get(session_id)

    def _save_session(self, state: StateTracker) -> None:
        self.store.save(state)

    def _revealed_name_key(self, npc_id: str) -> str:
        return f"revealed_name_{npc_id}"

    def _get_public_name(self, npc_id: str, npc_data: Dict[str, Any], state: Optional[StateTracker] = None) -> str:
        if state:
            revealed_name = state.get_short_term(self._revealed_name_key(npc_id))
            if revealed_name:
                return revealed_name
        return npc_data.get("unknown_name") or npc_data.get("name") or npc_id

    def _maybe_reveal_name(self, state: StateTracker, npc_id: str, npc_data: Dict[str, Any], text: str) -> bool:
        true_name = npc_data.get("true_name")
        if not true_name or not text:
            return False

        already_revealed = state.get_short_term(self._revealed_name_key(npc_id))
        if already_revealed:
            return False

        if true_name.lower() in text.lower():
            state.set_short_term(self._revealed_name_key(npc_id), true_name)
            state.set_flag(f"name_revealed_{npc_id}", True)
            return True

        return False

    def decide_response(self, session_id: str, npc_id: str,
                        player_action: str) -> Dict[str, Any]:
        """
        Главный метод: на основе действия игрока вернуть ответ NPC.
        """
        start_time = time.time()
        
        state = self.get_session(session_id)
        if not state:
            self.store.log_error("SessionNotFound", "Сессия не найдена", session_id, npc_id)
            return {"error": "Сессия не найдена"}

        npc_data = self.characters.get(npc_id)
        if not npc_data:
            self.store.log_error("NPCNotFound", f"NPC {npc_id} не найден", session_id, npc_id)
            return {"error": f"NPC {npc_id} не найден"}

        # Добавить действие игрока в историю
        state.add_turn("Вы", player_action, intent="user_input")

        # Определить интент NPC с высокой точностью
        intent = self._determine_intent(state, npc_id, player_action)

        # Если квест был предложен и игрок согласился — отдаём квест
        if intent == "accept_quest" and state.get_flag(f"quest_offered_{npc_id}"):
            intent = "give_quest"
            state.set_flag(f"quest_offered_{npc_id}", False)

        anger_key = f"anger_{npc_id}"
        anger_level = state.get_short_term(anger_key, 0) or 0

        # Если NPC золится, не отвечать дружелюбным приветствием
        if intent in ["greet", "small_talk_friendly", "small_talk"] and anger_level >= 2:
            intent = "insult"

        # Спланировать контент
        plan = self.planner.plan(
            intent=intent,
            npc_persona=npc_data.get("persona", "neutral"),
            state=state.get_state_snapshot(),
            player_relationship=state.get_relationship(npc_id)
        )

        # Применить правила
        actions = self._apply_rules(state, npc_id, intent)

        # Генерировать текст
        generated = self._generate_text(npc_data, plan, state, intent, player_action, npc_id)

        # Выбрать ЛУЧШИЙ кандидат (с анти-повтором)
        if generated:
            best = self._select_best_candidate(generated, npc_data, state)
            print(f"[Selection] Выбран {best.source} ответ: '{best.text[:50]}...'")
        else:
            best = GeneratedText(text="Хм...", intent=intent, tone="neutral")
            print("[Selection] Нет кандидатов, используем fallback")

        true_name = npc_data.get("true_name")
        if intent == "ask_name" and true_name and true_name.lower() not in best.text.lower():
            best.text = f"Меня зовут {true_name}."
        if intent == "apology":
            best.text = self._apology_response(npc_data, state, npc_id)
        best.text = self._sanitize_generated_text(best.text)

        name_revealed = self._maybe_reveal_name(state, npc_id, npc_data, best.text)
        speaker_name = self._get_public_name(npc_id, npc_data, state)

        # Обновить состояние
        state.add_turn(speaker_name, best.text,
                       intent=best.intent, emotion=best.tone)
        state.current_npc = npc_id

        # Применить побочные эффекты
        for action in actions:
            self._apply_action(state, action)

        self._save_session(state)

        # Логирование
        response_time_ms = (time.time() - start_time) * 1000
        self.store.log_dialog(
            session_id=session_id,
            npc_id=npc_id,
            player_message=player_action,
            npc_response=best.text,
            intent=intent,
            nlg_source=best.source,
            response_time_ms=response_time_ms
        )
        self.store.log_metric(session_id, f"response_time_ms_{best.source.split(':')[0]}", response_time_ms)

        result = {
            "speaker": speaker_name,
            "text": best.text,
            "intent": best.intent,
            "tone": best.tone,
            "source": best.source,
            "meta": {
                "npc_id": npc_id,
                "session_id": session_id,
                "archetype_name": npc_data.get("name", npc_id),
                "known_name": speaker_name,
                "name_revealed": name_revealed or bool(state.get_short_term(self._revealed_name_key(npc_id))),
                "timestamp": state.history[-1].timestamp if state.history else None,
                "response_time_ms": round(response_time_ms, 2),
            }
        }
        
        return result

    def _determine_intent(self, state: StateTracker, npc_id: str,
                         player_action: str) -> str:
        """Определить интент NPC на основе действия игрока с улучшенной логикой."""
        action_lower = player_action.lower().strip()

        # 0. Бессмысленный ввод
        if self._is_gibberish(action_lower):
            return "clarify"

        # Проверки в порядке приоритета

        if any(word in action_lower for word in ["как тебя зовут", "твое имя", "твоё имя", "кто ты", "назовись", "представься", "имя"]):
            return "ask_name"

        # 1. Прощание (самый высокий приоритет)
        if any(word in action_lower for word in ["пока", "до встречи", "до свидания", "уходу", "прощай", "увидимся", "ухожу"]):
            return "farewell"

        # 1.1. Извинение
        if any(word in action_lower for word in ["извини", "извините", "прости", "прошу прощения"]):
            return "apology"

        # 2. Квесты / помощь
        if any(word in action_lower for word in [
            "квест", "задание", "работ", "помощь", "помоги", "помогу",
            "есть ли", "у тебя есть", "нужна ли", "что у тебя есть", "есть что-то",
            "что можешь", "чем можешь", "чем помочь"
        ]):
            return "ask_quest"

        # 3. Информация / объяснение / запрос подробнейсти
        if any(word in action_lower for word in ["расскажи", "объясн", "знаешь", "скажи", "рассказ", "что", "подробн", "информ", "расскажу", "говори", "объясни", "покажи"]):
            return "provide_info"

        # 4. Приветствие
        if any(word in action_lower for word in ["привет", "добро", "здравств", "эй", "хей", "хай", "салам", "ку"]):
            return "greet"

        # 5. Благодарность / принятие
        if any(word in action_lower for word in ["спасибо", "благодар", "согласен", "согласна", "помогу", "принимаю", "да", "готов", "готова", "чем помочь", "сделаю"]):
            return "accept_quest"

        # 5.1. Короткое подтверждение / реакция
        if any(word in action_lower for word in ["хорошо", "нормально", "норм", "ок", "окей", "ладно", "ясно", "понял", "поняла", "понятно"]):
            return "ack"

        # 6. Оскорбление / угроза / враждебность
        if any(word in action_lower for word in [
            "нахуй", "иди", "уходи", "дурак", "идиот", "ублюдок", "сук",
            "убью", "убить", "зарежу", "нож", "ударю", "прибью", "сдохни",
            "нассу", "плюну", "заткнись"
        ]):
            return "insult"

        # Если текст очень короткий и мы не нашли явный интент
        if len(action_lower) < 3:
            # Пробуем один раз проверить содержимое - может это просто "а", "ы" и т.д.
            # В этом случае берём provide_info как вопрос
            if any(c in action_lower for c in ["?", "!", "а", "ё"]):
                return "provide_info"
            return "small_talk_friendly"

        # По умолчанию - small_talk
        return "small_talk_friendly"

    def _apology_response(self, npc_data: Dict[str, Any], state: StateTracker, npc_id: str) -> str:
        persona = npc_data.get("persona", "neutral")
        anger = state.get_short_term(f"anger_{npc_id}", 0) or 0
        if anger >= 2:
            return "Извинения приняты, но уважение к собеседнику должно быть всегда."
        if persona == "pragmatic":
            return "Извинения приняты. Вернемся к делу и нормальному торгу."
        return "Извинения приняты. Продолжим спокойно."

    def _sanitize_generated_text(self, text: str) -> str:
        """Убрать служебные маркеры, которые иногда просачиваются из модели."""
        cleaned = text.strip()
        cleaned = re.sub(r"\b(?:accepted|intent|tone|source|final)\b\s*:?", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        cleaned = re.sub(r"\s+([,.!?;:])", r"\1", cleaned)
        return cleaned or "Хм..."

    def _is_gibberish(self, text: str) -> bool:
        """Грубая эвристика для бессмысленного ввода."""
        if not text:
            return True
        if len(text) < 6:
            return False

        letters = sum(1 for c in text if c.isalpha())
        non_letters = len(text) - letters
        if non_letters / max(len(text), 1) > 0.35:
            return True

        # Очень длинное "слово" без пробелов с малым количеством гласных
        if " " not in text and len(text) > 14:
            vowels = set("аеёиоуыэюя")
            vowel_count = sum(1 for c in text if c in vowels)
            if vowel_count / max(len(text), 1) < 0.18:
                return True

        # Слишком мало уникальных символов
        unique_ratio = len(set(text)) / max(len(text), 1)
        if len(text) > 10 and unique_ratio < 0.2:
            return True

        return False

    def _select_best_candidate(self, generated: List[GeneratedText], npc_data: Dict[str, Any], state: StateTracker) -> GeneratedText:
        """Выбрать лучший вариант с анти-повтором и лёгкой вариативностью."""
        if not generated:
            return GeneratedText(text="Хм...", intent="small_talk", tone="neutral")

        last_npc_texts = [t.text for t in reversed(state.history) if t.speaker != "Вы"]
        last_npc_texts = last_npc_texts[:2]

        non_repeating = [g for g in generated if g.text not in last_npc_texts]
        pool = non_repeating if non_repeating else generated

        llm_candidates = [g for g in pool if "DeepSeek" in g.source]
        candidates = llm_candidates if llm_candidates else pool

        top_score = max(c.score for c in candidates)
        top = [c for c in candidates if c.score >= top_score - 0.02]
        return random.choice(top)

    def _apply_rules(self, state: StateTracker, npc_id: str, intent: str) -> List[Dict]:
        """Применить правила переходов и побочные эффекты."""
        actions = []

        npc = self.characters.get(npc_id, {})

        # Правило: если даём квест, отметить флаг
        if intent == "ask_quest":
            state.set_flag(f"quest_offered_{npc_id}", True)
            actions.append({"type": "offer_quest", "npc_id": npc_id})

        last_intent_key = f"last_intent_{npc_id}"
        last_intent = state.get_short_term(last_intent_key, None)

        # Правило: улучшить отношения при positive интерактивности (без абуза)
        if intent in ["greet", "small_talk", "help"]:
            anger_key = f"anger_{npc_id}"
            current_anger = state.get_short_term(anger_key, 0) or 0
            current_rel = state.get_relationship(npc_id)

            if last_intent != intent and current_anger == 0 and current_rel >= 0:
                state.update_relationship(npc_id, 5)
                state.update_reputation(1, npc_id)

            if current_anger > 0:
                state.set_short_term(anger_key, max(0, current_anger - 1))

        # Правило: ухудшить отношения при враждебности
        if intent == "insult":
            state.update_relationship(npc_id, -10)
            state.update_reputation(-3, npc_id)
            anger_key = f"anger_{npc_id}"
            current_anger = state.get_short_term(anger_key, 0) or 0
            state.set_short_term(anger_key, min(5, current_anger + 1))

        # Правило: принятие квеста повышает доверие
        if intent == "accept_quest":
            state.update_relationship(npc_id, 3)
            state.update_reputation(2, npc_id)
            state.set_flag(f"accepted_quest_{npc_id}", True)

        if intent == "apology":
            state.update_relationship(npc_id, 4)
            state.update_reputation(1, npc_id)
            anger_key = f"anger_{npc_id}"
            state.set_short_term(anger_key, 0)

        state.set_short_term(last_intent_key, intent)

        return actions

    def _generate_text(self, npc_data: Dict[str, Any], plan: Any,
                       state: StateTracker, intent: str, player_action: str = "", npc_id: Optional[str] = None) -> List[GeneratedText]:
        """Сгенерировать кандидаты текста в гибридном режиме.

        Template отвечает за простые устойчивые реплики, LLM - за открытые
        контекстные ответы. Если LLM недоступна, остаются Template/Retrieval.
        """
        template_first_intents = {
            "greet",
            "farewell",
            "ack",
            "accept_quest",
            "ask_name",
            "apology",
        }
        llm_first_intents = {
            "ask_quest",
            "provide_info",
            "small_talk",
            "small_talk_friendly",
            "clarify",
            "insult",
        }

        # Подготовить контекст для всех методов
        history_turns = max(1, min(30, int(os.getenv("NLG_HISTORY_TURNS", "12"))))
        history_text = "\n".join(
            [f"{t.speaker}: {t.text}" for t in state.get_history(history_turns)]
        )

        public_name = self._get_public_name(npc_id, npc_data, state) if npc_id else npc_data.get("unknown_name", "NPC")

        nlg_request = {
            "intent": intent,
            "tone": plan.tone,
            "context": history_text or "Начало диалога.",
            "target_character": npc_data.get("name", "NPC"),
            "true_name": npc_data.get("true_name", ""),
            "public_name": public_name,
            "player_name": state.player.player_name,
            "system_prompt": npc_data.get("system_prompt", ""),
            "player_action": player_action,
            "relationship": state.get_relationship(npc_id) if npc_id else 0,
            "anger": state.get_short_term(f"anger_{npc_id}", 0) if npc_id else 0,
        }

        # 1. Сначала готовим Template: он быстрый и служит fallback.
        template_results = []
        try:
            print(f"[Dialog] Пытаюсь Template для {intent}")
            template_results = self.template_nlg.generate(nlg_request)
            if template_results:
                print(f"[Dialog] Template успешен: {len(template_results)} вариант(ов)")
            else:
                print("[Dialog] Template вернул пусто")
        except Exception as e:
            print(f"[Dialog] Template ошибка: {e}")

        if intent in template_first_intents and template_results:
            print(f"[Dialog] Hybrid: простой intent '{intent}', используем Template")
            return template_results

        # 2. Для открытых intent пытаемся LLM. Это не отключает fallback.
        llm_results = []
        if self.llm_nlg:
            try:
                print(f"[Dialog] Hybrid: пытаюсь LLM для {intent}")
                llm_results = self.llm_nlg.generate(nlg_request)
                if llm_results:
                    print(f"[Dialog] LLM успешен: {len(llm_results)} вариант(ов)")
                    return llm_results
                else:
                    print("[Dialog] LLM вернул пусто, переходим к fallback")
            except Exception as e:
                print(f"[Dialog] LLM ошибка: {e}, переходим к fallback")
        else:
            print("[Dialog] LLM не активирован, используем fallback")

        # 3. Retrieval подключается как fallback, когда LLM не сработала.
        fallback_candidates = list(template_results)
        try:
            print(f"[Dialog] Добавляю Retrieval кандидатов")
            retrieval_results = self.retrieval_nlg.generate(nlg_request)
            fallback_candidates.extend(retrieval_results)
        except Exception as e:
            print(f"[Dialog] Retrieval ошибка: {e}")

        print(f"[Dialog] Hybrid fallback кандидатов: {len(fallback_candidates)} (LLM=False)")
        return fallback_candidates if fallback_candidates else [GeneratedText(
            text="Хм...",
            intent=intent,
            tone=plan.tone,
            score=0.50,
            source="Fallback"
        )]

    def _apply_action(self, state: StateTracker, action: Dict[str, Any]):
        """Применить побочный эффект (quest, flag, relation)."""
        action_type = action.get("type")

        if action_type == "offer_quest":
            quest_id = action.get("npc_id") + "_quest"
            state.update_quest(quest_id, "active")

    def get_npc_list(self) -> List[Dict[str, Any]]:
        """Получить список всех NPC."""
        return [
            {
                "id": npc_id,
                "name": npc_data.get("unknown_name") or npc_data.get("name"),
                "archetype_name": npc_data.get("name"),
                "scene": npc_data.get("scene"),
                "persona": npc_data.get("persona"),
            }
            for npc_id, npc_data in self.characters.items()
        ]
