"""FastAPI server for the dialog generation system."""
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any
import os
from dotenv import load_dotenv
import re

from .dialog_manager import DialogManager
from .game_data import get_characters_data, get_scenes_data, get_character_by_id, get_scene_by_id

# Load environment variables
load_dotenv()

# Initialize FastAPI
app = FastAPI(title="Dialog Generation System", version="1.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize DialogManager
characters_data = get_characters_data()
use_llm = bool(os.getenv("OPENROUTER_API_KEY")) and bool(os.getenv("NLG_MODEL"))
dm = DialogManager(characters_data=characters_data, use_llm=use_llm)

# Toxicity filter settings
TOXICITY_FILTER = os.getenv("TOXICITY_FILTER", "strict").lower()  # off|soft|strict
TOXICITY_BLOCKLIST = os.getenv(
    "TOXICITY_BLOCKLIST",
    "нахуй,сука,сук,пидор,убью,зарежу,убить,нож,ебать,блять,пизд,нахер,нассу,сдохни"
)
TOXICITY_WORDS = [w.strip() for w in TOXICITY_BLOCKLIST.split(",") if w.strip()]
TOXICITY_REGEX = re.compile(r"\b(" + "|".join(map(re.escape, TOXICITY_WORDS)) + r")\b", re.IGNORECASE)


def _is_toxic(text: str) -> bool:
    if not text:
        return False
    result = bool(TOXICITY_REGEX.search(text))
    if result:
        print(f"[TOXIC] Found toxic content in: '{text}'")
    return result


class SettingsUpdateRequest(BaseModel):
    toxicity_filter: str

print(f"[INFO] Dialog Manager инициализирован. LLM: {use_llm}")


# ===== Pydantic Models =====
class PlayerActionRequest(BaseModel):
    """Запрос действия игрока."""
    session_id: str
    npc_id: str
    action: str
    scene_id: Optional[str] = None


class SessionCreateRequest(BaseModel):
    """Запрос создания сессии."""
    session_id: str
    player_name: str = "Player"
    scene_id: Optional[str] = "inn"


class SessionResponse(BaseModel):
    """Ответ о сессии."""
    session_id: str
    player_name: str
    player: Dict[str, Any]
    world: Dict[str, Any]
    current_scene: str
    current_npc: Optional[str]


# ===== Routes =====
@app.get("/", response_class=HTMLResponse)
async def root():
    """Главная страница с интерфейсом."""
    frontend_path = os.path.join(os.path.dirname(__file__), "web", "frontend.html")
    if os.path.exists(frontend_path):
        return FileResponse(frontend_path, media_type="text/html")
    return HTMLResponse("<h1>Frontend not found</h1>")


@app.get("/api/health")
async def health():
    """Проверка здоровья сервера."""
    return {
        "status": "ok",
        "service": "Dialog Generation System",
        "llm_enabled": use_llm,
    }


@app.get("/api/settings")
async def get_settings():
    """Получить текущие настройки сервера."""
    return {
        "toxicity_filter": TOXICITY_FILTER,
    }


@app.post("/api/settings")
async def update_settings(req: SettingsUpdateRequest):
    """Обновить настройки сервера во время работы."""
    global TOXICITY_FILTER
    value = req.toxicity_filter.lower().strip()
    if value not in {"off", "soft", "strict"}:
        raise HTTPException(status_code=400, detail="Invalid toxicity_filter")
    print(f"[DEBUG] Changing TOXICITY_FILTER from {TOXICITY_FILTER} to {value}")
    TOXICITY_FILTER = value
    print(f"[DEBUG] TOXICITY_FILTER is now {TOXICITY_FILTER}")
    return {"toxicity_filter": TOXICITY_FILTER}


@app.post("/api/session/create")
async def create_session(req: SessionCreateRequest):
    """Создать новую сессию диалога."""
    state = dm.create_session(req.session_id, req.player_name)
    if req.scene_id:
        state.current_scene = req.scene_id

    return {
        "session_id": req.session_id,
        "player_name": req.player_name,
        "scene": req.scene_id or "inn",
        "message": f"Сессия {req.session_id} создана.",
    }


@app.get("/api/session/{session_id}")
async def get_session(session_id: str):
    """Получить информацию о сессии."""
    state = dm.get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "session_id": session_id,
        "player_name": state.player.player_name,
        "player": {
            "name": state.player.player_name,
            "reputation": state.player.reputation,
            "completed_quests": state.player.completed_quests,
            "active_quests": state.player.active_quests,
        },
        "current_scene": state.current_scene,
        "current_npc": state.current_npc,
        "history_length": len(state.history),
    }


@app.post("/api/dialogue/respond")
async def dialogue_respond(req: PlayerActionRequest):
    """Получить ответ NPC на действие игрока."""
    state = dm.get_session(req.session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    # Debug logging
    is_toxic_input = _is_toxic(req.action)
    print(f"[DEBUG] TOXICITY_FILTER={TOXICITY_FILTER}, input='{req.action}', is_toxic={is_toxic_input}")
    
    if TOXICITY_FILTER == "strict" and is_toxic_input:
        print(f"[DEBUG] Blocking toxic input: {req.action}")
        raise HTTPException(status_code=400, detail="Токсичный ввод заблокирован")

    response = dm.decide_response(
        session_id=req.session_id,
        npc_id=req.npc_id,
        player_action=req.action,
    )

    if "error" in response:
        raise HTTPException(status_code=400, detail=response["error"])

    is_toxic_output = _is_toxic(response.get("text", ""))
    print(f"[DEBUG] Response is_toxic={is_toxic_output}, text='{response.get('text', '')}'")
    
    if TOXICITY_FILTER == "strict" and is_toxic_output:
        print(f"[DEBUG] Blocking toxic response")
        response["text"] = "Давай без оскорблений. Продолжим спокойно."
        response["intent"] = "moderation"
        response["tone"] = "neutral"
        response["source"] = "moderation"

    return response


@app.get("/api/dialogue/history/{session_id}")
async def get_history(session_id: str, last_n: int = 10):
    """Получить историю диалога."""
    state = dm.get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    history = state.get_history(last_n)
    return {
        "session_id": session_id,
        "history": [
            {
                "speaker": t.speaker,
                "text": t.text,
                "intent": t.intent,
                "emotion": t.emotion,
                "timestamp": t.timestamp,
            }
            for t in history
        ],
    }


@app.get("/api/npc/list")
async def npc_list():
    """Получить список всех NPC."""
    npcs = dm.get_npc_list()
    return {"npcs": npcs}


@app.get("/api/npc/{npc_id}")
async def get_npc(npc_id: str):
    """Получить информацию о конкретном NPC."""
    npc = get_character_by_id(npc_id)
    if not npc:
        raise HTTPException(status_code=404, detail="NPC not found")

    return {
        "id": npc_id,
        "name": npc.get("unknown_name") or npc.get("name"),
        "archetype_name": npc.get("name"),
        "scene": npc.get("scene"),
        "description": npc.get("description"),
        "persona": npc.get("persona"),
        "traits": npc.get("traits"),
    }


@app.get("/api/scene/list")
async def scene_list():
    """Получить список всех сцен."""
    scenes = get_scenes_data()
    return {
        "scenes": [
            {
                "id": scene_id,
                "name": scene_data.get("name"),
                "description": scene_data.get("description"),
                "npcs": scene_data.get("npcs", []),
                "exits": scene_data.get("exits", []),
            }
            for scene_id, scene_data in scenes.items()
        ]
    }


@app.get("/api/scene/{scene_id}")
async def get_scene(scene_id: str):
    """Получить информацию о конкретной сцене."""
    scene = get_scene_by_id(scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found")

    return {
        "id": scene_id,
        "name": scene.get("name"),
        "description": scene.get("description"),
        "npcs": scene.get("npcs", []),
        "exits": scene.get("exits", []),
    }


@app.get("/api/state/{session_id}")
async def get_full_state(session_id: str):
    """Получить полное состояние игрока."""
    state = dm.get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    return state.get_state_snapshot()


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={
            "error": str(exc),
            "type": type(exc).__name__,
        },
    )


if __name__ == "__main__":
    import uvicorn

    print("[INFO] Starting server on http://0.0.0.0:8000")
    print("[INFO] Open browser: http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
