"""Settings, from the command line and PHIPDF_* environment variables.

Two modes:
- local (the default; `phi-pdf`): for one person on their own computer. Listens on 127.0.0.1 only, keeps files
  until they are deleted, opens the browser, may use AI keys from the environment, may download OCR languages.
- server (`phi-pdf serve --public`): for many people. Each visitor gets a private workspace, files are deleted
  after PHIPDF_KEEP_HOURS (2 by default), uploads and space are limited, the server's own AI keys are never used.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"PHIPDF_{name}", default).strip()


def _num(name: str, default: float) -> float:
    try:
        return float(_env(name) or default)
    except ValueError:
        return default


def _flag(name: str, default: bool) -> bool:
    value = _env(name)
    return default if not value else value.lower() in ("1", "true", "yes", "on")


def user_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PHI PDF Studio"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "PHI PDF Studio"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "phi-pdf-studio"


@dataclass
class Settings:
    mode: str = "local"                    # local | server
    host: str = "127.0.0.1"
    port: int = 8790
    data_dir: Path = field(default_factory=user_data_dir)
    max_upload_mb: int = 2048
    keep_hours: float = 0                  # 0 = keep files until deleted
    password: str = ""                     # optional: everyone must type it (a private server)
    api_key: str = ""                      # optional: the REST API needs "Authorization: Bearer <key>"
    workers: int = 2
    max_workspace_mb: int = 0              # 0 = no limit
    runs_per_workspace: int = 0            # running tools at once per visitor (0 = no limit)
    allow_downloads: bool = True           # OCR languages can be downloaded from the Settings page
    server_ai_keys: bool = True            # ANTHROPIC_API_KEY / GEMINI_API_KEY from the environment may be used
    allowed_hosts: list[str] = field(default_factory=list)
    https: bool = False                    # cookies marked Secure (behind an HTTPS proxy)

    @property
    def public(self) -> bool:
        return self.mode == "server"

    @classmethod
    def load(cls, mode: str = "local", **overrides) -> "Settings":
        cpus = os.cpu_count() or 2
        if mode == "server":
            base = cls(mode="server", host="0.0.0.0", port=8080, data_dir=Path("data").resolve(), max_upload_mb=200,
                       keep_hours=2, workers=max(2, cpus), max_workspace_mb=1024, runs_per_workspace=2,
                       allow_downloads=False, server_ai_keys=False)
        else:
            base = cls(workers=max(2, min(4, cpus // 2 or 1)))
        values = {
            "host": _env("HOST", base.host), "port": int(_num("PORT", base.port)),
            "data_dir": Path(_env("DATA") or base.data_dir).expanduser().resolve(),
            "max_upload_mb": int(_num("MAX_UPLOAD_MB", base.max_upload_mb)),
            "keep_hours": _num("KEEP_HOURS", base.keep_hours), "password": _env("PASSWORD"),
            "api_key": _env("API_KEY"), "workers": int(_num("WORKERS", base.workers)),
            "max_workspace_mb": int(_num("MAX_SPACE_MB", base.max_workspace_mb)),
            "runs_per_workspace": int(_num("RUNS_PER_VISITOR", base.runs_per_workspace)),
            "allow_downloads": _flag("ALLOW_DOWNLOADS", base.allow_downloads),
            "server_ai_keys": _flag("SERVER_AI_KEYS", base.server_ai_keys),
            "allowed_hosts": [h.strip().lower() for h in _env("ALLOWED_HOSTS").split(",") if h.strip()],
            "https": _flag("HTTPS", base.https),
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(mode=mode, **values)
