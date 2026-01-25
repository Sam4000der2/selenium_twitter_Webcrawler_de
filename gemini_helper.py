import logging
import re
from datetime import datetime, timedelta
from typing import List

import state_store

logger = logging.getLogger(__name__)

# Fallback-Modellliste (nach aktueller Google-Namenskonvention)
HARDCODED_MODELS = [
    "gemini-4-pro",
    "gemini-4-pro-preview",
    "gemini-4-flash",
    "gemini-4-flash-preview",
    "gemini-3.5-pro",
    "gemini-3.5-pro-preview",
    "gemini-3.5-flash",
    "gemini-3.5-flash-preview",
    "gemini-3-pro",
    "gemini-3-pro-preview",
    "gemini-3-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]


def _parse_version_variant(name: str) -> tuple:
    n = (name or "").lower()
    m = re.search(r"gemini-([0-9]+(?:\.[0-9]+)*?)-(pro|flash|flash-lite)", n)
    version = m.group(1) if m else "0"
    variant = m.group(2) if m else "other"
    version_parts = [int(x) for x in version.split(".") if x.isdigit()]
    while len(version_parts) < 3:
        version_parts.append(0)
    version_tuple = tuple(version_parts[:3])
    variant_rank = {"pro": 0, "flash": 1, "flash-lite": 2}.get(variant, 3)
    is_preview = "preview" in n
    is_latest = "latest" in n
    return version_tuple, variant_rank, is_preview, is_latest


def _is_text_model(name: str) -> bool:
    """
    Filtert auf Text-/Multimodal-Modelle. Schließt image/tts/audio/embedding/robotics/computer-use etc. aus.
    """
    n = (name or "").lower()
    blocked = (
        "image" in n
        or "tts" in n
        or "native-audio" in n
        or "audio" in n
        or "embedding" in n
        or "robotics" in n
        or "computer-use" in n
    )
    return name.startswith("gemini") and not blocked


def _model_sort_key(name: str) -> tuple:
    version_tuple, variant_rank, is_preview, is_latest = _parse_version_variant(name)
    # Neueste Version zuerst, pro vor flash, preview hinter stable gleicher Version/Variante
    return tuple([-v for v in version_tuple]) + (variant_rank, is_preview, not is_latest)


class GeminiModelManager:
    """
    - Hält Modellliste gecached (CSV) und aktualisiert sie höchstens alle refresh_days.
    - Merkt Quota-Exhaust pro Modell mit Variant-basiertem Cooldown (pro: 24h, flash: 4h).
    - NOT_FOUND/404 werden nur alle 7 Tage erneut geprüft.
    """

    def __init__(
        self,
        client,
        refresh_days: int = 7
    ):
        self.client = client
        self.refresh_days = max(1, refresh_days)
        self.statuses: dict[str, dict] = {}
        self.models: List[str] = []
        self.last_refresh: datetime | None = None
        self._load_cache()
        self._ensure_models(force=False)

    # ----------------------- Cache Handling -----------------------
    def _load_cache(self):
        data = state_store.load_gemini_cache()
        if not isinstance(data, dict):
            return
        models_cached = data.get("models") if isinstance(data, dict) else None
        if isinstance(models_cached, list):
            self.models = [m for m in models_cached if m]
        statuses = data.get("statuses", {}) if isinstance(data.get("statuses"), dict) else {}
        last_refresh_raw = data.get("last_refresh")
        if last_refresh_raw:
            try:
                self.last_refresh = (
                    last_refresh_raw if isinstance(last_refresh_raw, datetime) else datetime.fromisoformat(last_refresh_raw)
                )
            except Exception:
                self.last_refresh = None
        for name, row in statuses.items():
            status = (row.get("status") or "ok") if isinstance(row, dict) else "ok"
            last_update = row.get("last_update") if isinstance(row, dict) else None
            try:
                last_dt = (
                    last_update
                    if isinstance(last_update, datetime)
                    else datetime.fromisoformat(last_update) if isinstance(last_update, str) else None
                )
            except Exception:
                last_dt = None
            self.statuses[name] = {
                "status": status,
                "last_update": last_dt,
                "last_error": row.get("last_error", "") if isinstance(row, dict) else "",
            }
        if not self.models:
            self.models = [n for n in self.statuses.keys()]

    def _save_cache(self):
        try:
            payload = {
                "last_refresh": self.last_refresh.isoformat() if self.last_refresh else "",
                "statuses": {},
                "models": self.models,
            }
            for name in self.models:
                st = self.statuses.get(name, {})
                payload["statuses"][name] = {
                    "status": st.get("status", "ok"),
                    "last_update": st.get("last_update").isoformat() if st.get("last_update") else "",
                    "last_error": st.get("last_error", ""),
                }
            state_store.save_gemini_cache(payload)
        except Exception as e:
            logger.error(f"gemini_helper: Konnte Cache nicht speichern: {e}")

    # ----------------------- Model Discovery -----------------------
    def _fetch_remote_models(self) -> list[str]:
        try:
            available = []
            for m in self.client.models.list():
                name = getattr(m, "name", "") or getattr(m, "model", "") or ""
                if "/" in name:
                    name = name.split("/")[-1]
                if _is_text_model(name):
                    available.append(name)
            available = list(dict.fromkeys(available))  # dedupe
            if available:
                ordered = sorted(available, key=_model_sort_key)
                self.last_refresh = datetime.now()
                logger.info(f"gemini_helper: Verfügbare Gemini-Modelle (sortiert): {ordered}")
                return ordered
        except Exception as e:
            logger.error(f"gemini_helper: Modelle konnten nicht dynamisch ermittelt werden: {e}")
        ordered_fallback = sorted(HARDCODED_MODELS, key=_model_sort_key)
        logger.info(f"gemini_helper: Fallback Gemini-Modelle (hardcoded): {ordered_fallback}")
        return ordered_fallback

    def _ensure_models(self, force: bool):
        now = datetime.now()
        needs_refresh = force or (self.last_refresh is None) or (now - self.last_refresh > timedelta(days=self.refresh_days))
        if not self.models or needs_refresh:
            self.models = self._fetch_remote_models()
            # Sicherstellen, dass Status-Einträge existieren
            for m in self.models:
                self.statuses.setdefault(m, {"status": "ok", "last_update": None, "last_error": ""})
            self._save_cache()

    # ----------------------- Eligibility -----------------------
    def _cooldown(self, name: str, status: str) -> timedelta:
        variant_rank = _parse_version_variant(name)[1]
        if status == "quota_exhausted":
            # pro: 24h, flash/flash-lite: 4h, other: 12h
            if variant_rank == 0:
                return timedelta(hours=24)
            if variant_rank == 1 or variant_rank == 2:
                return timedelta(hours=4)
            return timedelta(hours=12)
        if status == "not_found":
            return timedelta(days=7)
        if status == "failed":
            return timedelta(hours=12)
        return timedelta(0)

    def _eligible(self, name: str, now: datetime) -> bool:
        st = self.statuses.get(name, {})
        status = st.get("status", "ok")
        last_update = st.get("last_update")
        if status in {"ok", "unknown"} or last_update is None:
            return True
        cooldown = self._cooldown(name, status)
        return (now - last_update) >= cooldown

    def get_candidate_models(self) -> list[str]:
        now = datetime.now()
        self._ensure_models(force=False)
        eligible = [m for m in self.models if self._eligible(m, now)]
        if eligible:
            return eligible
        # Wenn alle blockiert, hebe die älteste Quota-Sperre auf, um nicht leer zu laufen
        if self.models:
            oldest = min(self.models, key=lambda m: (self.statuses.get(m, {}).get("last_update") or now))
            self.statuses[oldest] = {"status": "ok", "last_update": now, "last_error": ""}
            self._save_cache()
            return [oldest]
        return sorted([m for m in HARDCODED_MODELS if _is_text_model(m)], key=_model_sort_key)

    # ----------------------- Status Updates -----------------------
    def _set_status(self, name: str, status: str, error: str = ""):
        if name not in self.statuses:
            self.statuses[name] = {"status": status, "last_update": datetime.now(), "last_error": error}
            if name not in self.models:
                self.models.append(name)
        else:
            self.statuses[name].update({
                "status": status,
                "last_update": datetime.now(),
                "last_error": error,
            })
        self._save_cache()

    def mark_success(self, name: str):
        self._set_status(name, "ok", "")

    def mark_quota(self, name: str, error: str = ""):
        self._set_status(name, "quota_exhausted", error)

    def mark_not_found(self, name: str, error: str = ""):
        self._set_status(name, "not_found", error)

    def mark_failed(self, name: str, error: str = ""):
        self._set_status(name, "failed", error)

    def refresh_now(self):
        self._ensure_models(force=True)
