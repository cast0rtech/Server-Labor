"""Versión de RedLab Tester y comprobación de actualizaciones (GitHub Releases).

Al publicar una nueva versión: subir APP_VERSION aquí y en los .iss
(instalador.iss, update_servidor.iss, update_sonda.iss), y crear un Release
en GitHub con el tag "vX.Y.Z" adjuntando los instaladores de actualización.
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Optional

APP_VERSION = "1.0.1"
GITHUB_REPO = "cast0rtech/Server-Labor"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"


def _parse(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", version)[:4]) or (0,)


def check_for_update(timeout: float = 4.0) -> Optional[dict]:
    """Devuelve {'version', 'url'} si hay una versión más nueva; None si no (o sin red)."""
    try:
        req = urllib.request.Request(
            RELEASES_API,
            headers={"User-Agent": f"RedLabTester/{APP_VERSION}", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest = str(data.get("tag_name", "")).lstrip("vV")
        if latest and _parse(latest) > _parse(APP_VERSION):
            return {"version": latest, "url": data.get("html_url") or RELEASES_PAGE}
    except Exception:
        pass
    return None

