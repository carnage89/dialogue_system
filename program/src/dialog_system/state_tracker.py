"""
StateTracker: управление состоянием диалога, памятью и фактами мира.
"""
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime
import json


@dataclass
class Turn:
    """Один ход в диалоге."""
    speaker: str
    text: str
    intent: Optional[str] = None
    emotion: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class PlayerProfile:
    """Профиль игрока."""
    player_name: str = "Player"
    reputation: int = 0
    npc_reputation: Dict[str, int] = field(default_factory=dict)
    completed_quests: List[str] = field(default_factory=list)
    active_quests: List[str] = field(default_factory=list)
    inventory: List[str] = field(default_factory=list)
    relationships: Dict[str, int] = field(default_factory=dict)  # NPC id -> rel score


@dataclass
class WorldState:
    """Глобальное состояние мира."""
    world_facts: Dict[str, Any] = field(default_factory=dict)
    active_events: List[str] = field(default_factory=list)
    time_of_day: str = "day"


class StateTracker:
    """Отслеживает и обновляет состояние диалога."""

    def __init__(self, session_id: str, player_name: str = "Player"):
        self.session_id = session_id
        self.player = PlayerProfile(player_name=player_name)
        self.world = WorldState()
        self.history: List[Turn] = []
        self.current_scene: str = "inn"
        self.current_npc: Optional[str] = None
        self.short_term_memory: Dict[str, Any] = {}
        self.flags: Dict[str, bool] = {}

    def add_turn(self, speaker: str, text: str, intent: Optional[str] = None,
                 emotion: Optional[str] = None):
        """Добавить ход диалога."""
        turn = Turn(speaker=speaker, text=text, intent=intent, emotion=emotion)
        self.history.append(turn)

    def get_history(self, last_n: int = 5) -> List[Turn]:
        """Получить последние N ходов."""
        return self.history[-last_n:]

    def set_flag(self, key: str, value: bool):
        """Установить флаг состояния."""
        self.flags[key] = value

    def get_flag(self, key: str, default: bool = False) -> bool:
        """Получить значение флага."""
        return self.flags.get(key, default)

    def update_quest(self, quest_id: str, status: str):
        """Обновить статус квеста."""
        if status == "active" and quest_id not in self.player.active_quests:
            self.player.active_quests.append(quest_id)
        elif status == "completed":
            if quest_id in self.player.active_quests:
                self.player.active_quests.remove(quest_id)
            if quest_id not in self.player.completed_quests:
                self.player.completed_quests.append(quest_id)

    def get_relationship(self, npc_id: str) -> int:
        """Получить уровень отношения с NPC."""
        return self.player.relationships.get(npc_id, 0)

    def update_relationship(self, npc_id: str, delta: int):
        """Изменить отношение с NPC."""
        current = self.player.relationships.get(npc_id, 0)
        self.player.relationships[npc_id] = max(-100, min(100, current + delta))

    def update_reputation(self, delta: int, npc_id: Optional[str] = None):
        """Изменить репутацию игрока для конкретного NPC."""
        if not npc_id:
            return
        current = self.player.npc_reputation.get(npc_id, 0)
        self.player.npc_reputation[npc_id] = max(-100, min(100, current + delta))

    def get_state_snapshot(self) -> Dict[str, Any]:
        """Получить снимок всего состояния."""
        return {
            "session_id": self.session_id,
            "player": asdict(self.player),
            "world": asdict(self.world),
            "current_scene": self.current_scene,
            "current_npc": self.current_npc,
            "history_last_5": [asdict(t) for t in self.get_history(5)],
            "short_term_memory": self.short_term_memory,
            "flags": self.flags,
        }

    def get_short_term(self, key: str, default: Any = None) -> Any:
        """Получить значение из краткосрочной памяти."""
        return self.short_term_memory.get(key, default)

    def set_short_term(self, key: str, value: Any):
        """Установить значение в краткосрочную память."""
        self.short_term_memory[key] = value

    def to_dict(self) -> Dict[str, Any]:
        """Сериализовать для сохранения."""
        return {
            "session_id": self.session_id,
            "player": asdict(self.player),
            "world": asdict(self.world),
            "current_scene": self.current_scene,
            "current_npc": self.current_npc,
            "history": [asdict(t) for t in self.history],
            "short_term_memory": self.short_term_memory,
            "flags": self.flags,
        }

    def to_json(self) -> str:
        """JSON представление."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StateTracker":
        """Восстановить состояние из сериализованного словаря."""
        player_data = data.get("player", {})
        state = cls(
            session_id=data.get("session_id", ""),
            player_name=player_data.get("player_name", "Player"),
        )
        state.player = PlayerProfile(**player_data)
        state.world = WorldState(**data.get("world", {}))
        state.current_scene = data.get("current_scene", "inn")
        state.current_npc = data.get("current_npc")
        state.short_term_memory = data.get("short_term_memory", {})
        state.flags = data.get("flags", {})
        state.history = [Turn(**turn) for turn in data.get("history", [])]
        return state
