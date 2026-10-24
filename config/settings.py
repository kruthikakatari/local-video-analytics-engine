"""
Central configuration.

System-wide values come from the `.env` file (see `.env.example`).
Camera definitions live in `config/cameras.json`, zones in `config/zones.json`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is optional at runtime
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent.parent

if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")


def _str(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _path(name: str, default: str) -> Path:
    p = Path(_str(name, default))
    return p if p.is_absolute() else BASE_DIR / p


@dataclass
class Settings:
    # --- Web server (FastAPI) ---
    host: str = field(default_factory=lambda: _str("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int("PORT", 8000))

    # --- MQTT broker (Mosquitto) ---
    mqtt_host: str = field(default_factory=lambda: _str("MQTT_BROKER_HOST", "localhost"))
    mqtt_port: int = field(default_factory=lambda: _int("MQTT_BROKER_PORT", 1883))
    mqtt_topic_prefix: str = field(default_factory=lambda: _str("MQTT_TOPIC_PREFIX", "surveillance"))
    mqtt_qos: int = field(default_factory=lambda: _int("MQTT_QOS", 1))

    # --- AI models ---
    yolo_model: Path = field(default_factory=lambda: _path("YOLO_MODEL", "models/yolov8n.pt"))
    fire_model: str = field(default_factory=lambda: _str("FIRE_MODEL", "models/fire_smoke.pt"))
    detector_backend: str = field(default_factory=lambda: _str("DETECTOR_BACKEND", "auto"))
    img_size: int = field(default_factory=lambda: _int("IMG_SIZE", 640))
    confidence_threshold: float = field(default_factory=lambda: _float("CONFIDENCE_THRESHOLD", 0.5))
    fire_confidence_threshold: float = field(default_factory=lambda: _float("FIRE_CONFIDENCE_THRESHOLD", 0.5))

    # --- Frame sampling / performance ---
    process_fps: float = field(default_factory=lambda: _float("PROCESS_FPS", 5))
    stream_fps: float = field(default_factory=lambda: _float("STREAM_FPS", 15))
    stream_width: int = field(default_factory=lambda: _int("STREAM_WIDTH", 960))
    stream_jpeg_quality: int = field(default_factory=lambda: _int("STREAM_JPEG_QUALITY", 70))
    fire_every_n_frames: int = field(default_factory=lambda: _int("FIRE_EVERY_N_FRAMES", 3))
    rtsp_transport: str = field(default_factory=lambda: _str("RTSP_TRANSPORT", "tcp"))
    reconnect_seconds: float = field(default_factory=lambda: _float("RECONNECT_SECONDS", 5))

    # --- Rules ---
    crowd_threshold: int = field(default_factory=lambda: _int("CROWD_THRESHOLD", 5))
    loitering_seconds: float = field(default_factory=lambda: _float("LOITERING_SECONDS", 10))
    event_cooldown_seconds: float = field(default_factory=lambda: _float("EVENT_COOLDOWN_SECONDS", 5))
    incident_clear_seconds: float = field(default_factory=lambda: _float("INCIDENT_CLEAR_SECONDS", 10))
    incident_update_seconds: float = field(default_factory=lambda: _float("INCIDENT_UPDATE_SECONDS", 5))
    fire_confirm_frames: int = field(default_factory=lambda: _int("FIRE_CONFIRM_FRAMES", 2))
    person_events_in_security_mode: bool = field(
        default_factory=lambda: _bool("PERSON_EVENTS_IN_SECURITY_MODE", False))
    disaster_mode: bool = field(default_factory=lambda: _bool("DISASTER_MODE", False))

    # --- Storage ---
    database_path: Path = field(default_factory=lambda: _path("DATABASE_PATH", "database/surveillance.db"))
    snapshot_dir: Path = field(default_factory=lambda: _path("SNAPSHOT_DIR", "snapshots"))
    cameras_file: Path = field(default_factory=lambda: _path("CAMERAS_FILE", "config/cameras.json"))
    zones_file: Path = field(default_factory=lambda: _path("ZONES_FILE", "config/zones.json"))

    # --- Misc ---
    force_demo: bool = field(default_factory=lambda: _bool("FORCE_DEMO", False))
    enable_analytics: bool = field(default_factory=lambda: _bool("ENABLE_ANALYTICS", True))
    log_level: str = field(default_factory=lambda: _str("LOG_LEVEL", "INFO"))

    @property
    def fire_model_path(self) -> Path | None:
        """None when fire/smoke detection is intentionally disabled (FIRE_MODEL empty)."""
        if not self.fire_model:
            return None
        p = Path(self.fire_model)
        return p if p.is_absolute() else BASE_DIR / p

    def topic(self, *parts: str) -> str:
        return "/".join([self.mqtt_topic_prefix, *parts])


settings = Settings()
