"""
ContentPlanner: преобразует интенты игрока в план генерации контента.
"""
from typing import Dict, List, Optional, Any
import random
from dataclasses import dataclass


@dataclass
class ContentPlan:
    """План контента для генерации."""
    intent: str
    tone: str
    template_id: Optional[str] = None
    keywords: List[str] = None
    constraints: Dict[str, Any] = None

    def __post_init__(self):
        if self.keywords is None:
            self.keywords = []
        if self.constraints is None:
            self.constraints = {}


class ContentPlanner:
    """Планирует контент диалога на основе интента и состояния."""

    def __init__(self):
        self.intent_to_tone = {
            "greet": "friendly",
            "ask_quest": "neutral",
            "accept_quest": "supportive",
            "give_quest": "formal",
            "provide_info": "informative",
            "ask_name": "friendly",
            "small_talk": "casual",
            "ack": "neutral",
            "clarify": "neutral",
            "farewell": "polite",
            "insult": "hostile",
            "apology": "neutral",
            "help": "supportive",
        }

        self.intent_templates = {
            "greet": [
                "greet_friendly",
                "greet_neutral",
                "greet_dismissive",
            ],
            "ask_quest": [
                "ask_quest_respectful",
                "ask_quest_urgent",
            ],
            "accept_quest": [
                "ack_friendly",
                "ack_neutral",
            ],
            "give_quest": [
                "give_quest_formal",
                "give_quest_casual",
            ],
            "provide_info": [
                "info_detailed",
                "info_brief",
            ],
            "ask_name": [
                "introduce_self",
            ],
            "clarify": [
                "clarify_neutral",
                "clarify_friendly",
            ],
            "small_talk": [
                "small_talk_friendly",
                "small_talk_sarcastic",
            ],
            "ack": [
                "ack_friendly",
                "ack_neutral",
            ],
            "farewell": [
                "farewell_polite",
                "farewell_casual",
            ],
        }

    def plan(self, intent: str, npc_persona: str, state: Dict[str, Any],
             player_relationship: int) -> ContentPlan:
        """
        Планировать контент на основе намерения.
        """
        # Выбрать базовый тон по интенту
        base_tone = self.intent_to_tone.get(intent, "neutral")

        # Модифицировать тон в зависимости от персоны
        if npc_persona == "gruff":
            tone = "gruff"
        elif npc_persona == "formal":
            tone = "formal"
        elif npc_persona == "friendly":
            tone = "friendly"
        else:
            tone = base_tone

        # Выбрать шаблон на основе интента
        templates = self.intent_templates.get(intent, [])
        template_id = random.choice(templates) if templates else None

        # Ограничения на основе отношений
        relationship_level = "cold" if player_relationship < -50 else \
                            "neutral" if player_relationship < 25 else \
                            "warm" if player_relationship < 75 else "intimate"

        constraints = {
            "relationship_level": relationship_level,
            "avoid_spoilers": True,
            "keep_consistency": True,
        }

        keywords = self._extract_keywords(state, intent)

        return ContentPlan(
            intent=intent,
            tone=tone,
            template_id=template_id,
            keywords=keywords,
            constraints=constraints
        )

    def _extract_keywords(self, state: Dict[str, Any], intent: str) -> List[str]:
        """Извлечь ключевые слова из состояния для использования в шаблонах."""
        keywords = []

        if "active_quests" in state and state["active_quests"]:
            keywords.append(state['active_quests'][0])

        if intent == "provide_info":
            # Для provide_info вернуть осмысленные темы
            if keywords:
                # Если есть активный квест, используй его
                pass
            else:
                # Иначе генери generic тему
                keywords = ["местности", "событиям", "моему делу"]
        elif intent == "give_quest":
            keywords = ["приключение"]
        elif intent == "small_talk":
            keywords = ["погоде", "новостям"]

        return keywords
