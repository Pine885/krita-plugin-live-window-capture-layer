"""Per-session state and JSON persistence.

Krita-free: no krita or Qt imports.
"""

import json
import uuid
from dataclasses import dataclass, field
from enum import Enum

from .limits import DEFAULT_INTERVAL_MS, MAX_INTERVAL_MS, MIN_INTERVAL_MS

SCHEMA_VERSION = 1
ANNOTATION_TYPE = "window_capture_layer.sessions"
ANNOTATION_DESCRIPTION = "Window Capture sessions"


class State(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    LOST = "lost"
    ERROR = "error"


# A LOST session is still polling for its window, so it counts as running.
RUNNING = (State.ACTIVE, State.LOST)


def clamp_interval(value):
    try:
        interval = int(value)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_MS
    return max(MIN_INTERVAL_MS, min(MAX_INTERVAL_MS, interval))


def new_session_id():
    return uuid.uuid4().hex[:12]


@dataclass
class CaptureSession:
    id: str = field(default_factory=new_session_id)
    title: str = ""
    wid: int = 0
    layer_uuid: str = ""
    layer_name: str = ""
    interval_ms: int = DEFAULT_INTERVAL_MS
    state: State = State.PAUSED
    message: str = ""

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "wid": int(self.wid),
            "layer_uuid": self.layer_uuid,
            "layer_name": self.layer_name,
            "interval_ms": clamp_interval(self.interval_ms),
            "state": State(self.state).value,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            return None
        session_id = data.get("id")
        if not isinstance(session_id, str) or not session_id:
            return None
        try:
            state = State(data.get("state", State.PAUSED.value))
        except ValueError:
            state = State.PAUSED
        wid = data.get("wid", 0)
        try:
            wid = int(wid)
        except (TypeError, ValueError):
            wid = 0
        return cls(
            id=session_id,
            title=str(data.get("title", "")),
            wid=wid,
            layer_uuid=str(data.get("layer_uuid", "")),
            layer_name=str(data.get("layer_name", "")),
            interval_ms=clamp_interval(data.get("interval_ms")),
            state=state,
            message=str(data.get("message", "")),
        )


def to_json(sessions):
    """Serialize sessions. Runtime fields (digest, target) are not stored."""
    payload = {
        "v": SCHEMA_VERSION,
        "sessions": [s.to_dict() for s in sessions],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def from_json(text):
    """Parse stored sessions. Unknown schema versions yield an empty list."""
    if not text:
        return []
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    if payload.get("v") != SCHEMA_VERSION:
        return []
    raw_sessions = payload.get("sessions")
    if not isinstance(raw_sessions, list):
        return []

    sessions = []
    seen = set()
    for entry in raw_sessions:
        session = CaptureSession.from_dict(entry)
        if session is None or session.id in seen:
            continue
        seen.add(session.id)
        sessions.append(session)
    return sessions
