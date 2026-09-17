import asyncio
import base64
import io
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from PIL import Image

from config import settings

SUGGESTED_OLLAMA_VISION_MODELS: list[dict[str, Any]] = [
    {
        "id": "qwen2.5vl:7b",
        "name": "Qwen 2.5 VL 7B",
        "tag": "qwen2.5vl:7b",
        "size_gb": 6.0,
        "min_ram_gb": 10,
        "min_vram_gb": 8,
        "badge": "Zrównoważony",
        "description": "Szybki model wizyjny o wysokiej wierności schematów JSON; rekomendowany do audytu zdjęć.",
    },
    {
        "id": "llama3.2-vision:11b",
        "name": "Llama 3.2 Vision 11B",
        "tag": "llama3.2-vision:11b",
        "size_gb": 8.0,
        "min_ram_gb": 16,
        "min_vram_gb": 10,
        "badge": "Wysoka precyzja",
        "description": "Dokładniejsza analiza rzutów i wad technicznych; wymaga mocniejszego GPU.",
    },
    {
        "id": "minicpm-v:8b",
        "name": "MiniCPM-V 8B",
        "tag": "minicpm-v:8b",
        "size_gb": 8.0,
        "min_ram_gb": 16,
        "min_vram_gb": 10,
        "badge": "Rzuty i OCR",
        "description": "Mocny w odczycie rzutów architektonicznych i tekstu na zdjęciach.",
    },
    {
        "id": "moondream",
        "name": "Moondream 2",
        "tag": "moondream",
        "size_gb": 1.7,
        "min_ram_gb": 6,
        "min_vram_gb": 4,
        "badge": "Lekki (CPU)",
        "description": "Malutki model wizyjny do szybkiej klasyfikacji render vs fotografia na CPU.",
    },
]

DEFAULT_VISION_MODEL = "gpt-4o-mini"
DEFAULT_VISION_OLLAMA_MODEL = "qwen2.5vl:7b"

_VISION_MODEL_FAMILY_TOKENS: tuple[str, ...] = (
    "vision",
    "llava",
    "moondream",
    "minicpm",
    "bakllava",
    "qwen2.5vl",
    "qwen2-vl",
    "-vl",
    ":vl",
    "vl-",
    "_vl",
)


def is_vision_model(name: str | None) -> bool:
    """True when an installed model tag looks vision-capable.

    Single source of truth for vision suggestions: exact/base-name match against
    SUGGESTED_OLLAMA_VISION_MODELS, plus tight family tokens for models the user
    installed outside the suggested list (e.g. a newer qwen-vl tag).
    """
    if not name:
        return False
    low = name.strip().lower()
    for suggestion in SUGGESTED_OLLAMA_VISION_MODELS:
        tag = str(suggestion.get("tag", "")).lower()
        if low == tag or low.split(":")[0] == tag.split(":")[0]:
            return True
    return any(tok in low for tok in _VISION_MODEL_FAMILY_TOKENS)


def is_local_vision_base(base_url: str | None) -> bool:
    """True if the endpoint URL points to a local model engine (Ollama, LM Studio, etc.)."""
    if not base_url:
        return False
    low = base_url.lower()
    return any(
        tok in low
        for tok in (
            "localhost",
            "127.0.0.1",
            "host.docker.internal",
            "0.0.0.0",
            ":11434",
            ":1234",
            "ollama",
        )
    )


def _resolve_docker_base(base_url: str) -> str:
    """In Docker, rewrites localhost/127.0.0.1 to host.docker.internal."""
    base = (base_url or "").rstrip("/")
    is_docker = Path("/.dockerenv").exists() or bool(os.environ.get("DOCKER_CONTAINER"))
    if is_docker and ("localhost" in base or "127.0.0.1" in base):
        return re.sub(r"localhost|127\.0\.0\.1", "host.docker.internal", base)
    return base


def resolve_vision_target(
    api_base: str | None = None,
    api_key: str | None = None,
    model_name: str | None = None,
) -> tuple[str, str, str]:
    """Resolves (base_url, api_key, model) for the Vision audit.

    The vision model is independent from the text model: text models are not
    multimodal, so a text Ollama model is never inherited here.
    Precedence for base: explicit args > app config vision_base_url >
    VISION_* setting > configured local engine (when the text provider is
    local) > OPENROUTER (when openrouter configured) > OPENAI_*/LLM_* settings > OpenAI default.
    Precedence for model: explicit args > app config vision_model > VISION_*
    setting > qwen2.5vl:7b on local base > google/gemini-2.0-flash-001 on OpenRouter > OPENAI_MODEL > OpenAI default.
    The API key stays ENV-owned and never lives in the app config.
    A bare-host base URL (e.g. http://localhost:11434) gets `/v1` appended for the
    OpenAI-compatible `/chat/completions` endpoint; custom paths are left untouched.
    """
    cfg_model: str | None = None
    cfg_base: str | None = None
    cfg_local_base: str | None = None
    cfg_provider: str = ""
    try:
        from src.services.config_manager import config_manager

        cfg = config_manager.get_config()
        cfg_model = getattr(cfg, "vision_model", None) or None
        cfg_base = getattr(cfg, "vision_base_url", None) or None
        cfg_provider = str(getattr(cfg, "llm_provider", "") or "").lower().strip()
        if cfg_provider in ("local", "ollama", "local_openai"):
            cfg_local_base = getattr(cfg, "local_llm_base_url", None) or getattr(cfg, "ollama_base_url", None) or None
    except Exception:
        pass

    # 1. Detect model intent (Ollama vs OpenRouter vs OpenAI)
    req_model = model_name or cfg_model or getattr(settings, "VISION_MODEL", None)
    is_ollama_requested = False
    is_openrouter_requested = False
    is_openai_requested = False

    if req_model:
        req_norm = req_model.strip()
        if "/" in req_norm:
            is_openrouter_requested = True
        elif req_norm.lower().startswith("gpt-"):
            is_openai_requested = True
        elif is_vision_model(req_norm) or ":" in req_norm:
            is_ollama_requested = True

    # 2. Base URL resolution
    candidate_base = api_base or cfg_base or getattr(settings, "VISION_BASE_URL", None)
    is_openrouter_candidate = False
    if not candidate_base:
        if is_ollama_requested:
            candidate_base = cfg_local_base or getattr(settings, "OLLAMA_BASE_URL", None) or "http://localhost:11434"
        elif is_openrouter_requested:
            candidate_base = "https://openrouter.ai/api/v1"
            is_openrouter_candidate = True
        elif is_openai_requested:
            candidate_base = getattr(settings, "OPENAI_BASE_URL", None) or "https://api.openai.com/v1"
        elif cfg_local_base:
            candidate_base = cfg_local_base
        elif cfg_provider == "openrouter":
            candidate_base = "https://openrouter.ai/api/v1"
            is_openrouter_candidate = True
        elif cfg_provider in ("local", "ollama", "local_openai"):
            candidate_base = getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")
        elif getattr(settings, "OPENAI_API_KEY", None) or getattr(settings, "OPENAI_BASE_URL", None):
            candidate_base = getattr(settings, "OPENAI_BASE_URL", None) or "https://api.openai.com/v1"
        elif getattr(settings, "OPENROUTER_API_KEY", None):
            candidate_base = "https://openrouter.ai/api/v1"
            is_openrouter_candidate = True
        else:
            candidate_base = getattr(settings, "LLM_BASE_URL", None) or "https://api.openai.com/v1"

    target_base = _resolve_docker_base(candidate_base)
    if "//" in target_base and "/" not in target_base.split("//", 1)[1]:
        target_base = f"{target_base}/v1"

    is_local_base = is_local_vision_base(target_base)
    is_openrouter_base = "openrouter.ai" in target_base or is_openrouter_candidate

    # 3. Key resolution
    target_key = (
        api_key
        or getattr(settings, "VISION_API_KEY", None)
        or (getattr(settings, "OPENROUTER_API_KEY", None) if is_openrouter_base else None)
        or (getattr(settings, "OPENAI_API_KEY", None) if not is_local_base else None)
        or (getattr(settings, "LLM_API_KEY", None) if not is_local_base else None)
        or ""
    )

    # 4. Model resolution
    if is_local_base:
        default_model = DEFAULT_VISION_OLLAMA_MODEL
    elif is_openrouter_base:
        default_model = "google/gemini-2.0-flash-001"
    else:
        default_model = getattr(settings, "OPENAI_MODEL", None) or DEFAULT_VISION_MODEL

    model = req_model or default_model
    return target_base, target_key, model


def declared_finish_label(source: Any) -> str | None:
    """Unwraps a listing's finish condition (enum, plain string, or None) to a plain label."""
    finish_val = getattr(source, "finish_condition", None)
    if finish_val is None:
        return None
    return str(getattr(finish_val, "value", finish_val) or "") or None


VISION_AUDIT_PROMPT = """Jesteś rzeczoznawcą budowlanym. Przeprowadź forensic audyt załączonych zdjęć oferty nieruchomości.

Kroki (wykonaj wszystkie, po kolei):
1. Render vs fotografia: sklasyfikuj każde zdjęcie jako render 3D/CAD albo fotografię fizycznego budynku. Kryterium ukończenia: is_render opisuje cały zestaw, a render_confidence (0-1) odzwierciedla pewność tej klasyfikacji.
2. Stan wykończenia: przypisz jeden visual_finish_condition ze słownika DO_ZAMIESZKANIA | DO_WYKONCZENIA | DEWELOPERSKI | SUROWY | DO_REMONTU | NIEZNANY, stosując poprzeczkę Living Quarters poniżej. Kryterium ukończenia: etykieta odpowiada najsłabszemu widocznemu pomieszczeniu mieszkalnemu ze wszystkich zdjęć.
3. Rzut: ustaw has_floorplan. Gdy rzut występuje, wypełnij orientation, usability_score (1-10) i room_layout_notes. Kryterium ukończenia: każdy widoczny rzut ma odzwierciedlenie w floorplan_details albo has_floorplan to false.
4. Wady: wypisz każdą widoczną wadę fizyczną z każdego zdjęcia (wilgoć, pęknięcia, brak balustrad, wystające przewody, prowizoryczne schody, słupy wysokiego napięcia za oknem). Kryterium ukończenia: każde zdjęcie wniosło zero albo więcej wpisów do defects.

Poprzeczka Living Quarters (definicja DO_ZAMIESZKANIA):
1. Kuchnia: zabudowa kuchenna, zlew, płyta/kuchenka, lodówka lub gotowe przyłącza ze sprzętem.
2. Łazienki: wykończone ściany, zamontowana armatura (miska WC, umywalka, prysznic/wanna).
3. Podłogi i ściany: ułożone podłogi (panele, deski, parkiet, płytki), pomalowane ściany, sprawne ogrzewanie.
Zachowaj DO_ZAMIESZKANIA, gdy wnętrze mieszkalne spełnia powyższe trzy punkty; drobne prace zewnętrzne (brak kostki, taras do wykończenia, nieurządzony ogród, poddasze do adaptacji) odnotuj w summary. Gdy opis ogłoszenia podano powyżej, a zdjęcia mu przeczą, wyjaśnij różnicę w discrepancy_note.

Zwróć poprawny obiekt JSON dokładnie o tym schemacie, bez otaczającego tekstu:
{
  "is_render": false,
  "render_confidence": 0.05,
  "visual_finish_condition": "DO_ZAMIESZKANIA",
  "has_floorplan": false,
  "floorplan_details": {
    "orientation": "Salon od południa, wejście od północy",
    "usability_score": 8,
    "room_layout_notes": "Brak pokoi przechodnich, ustawny układ"
  },
  "defects": [],
  "discrepancy_note": null,
  "summary": "Wnętrze w pełni wykończone i umeblowane, gotowe do natychmiastowego zamieszkania."
}
"""


class VisionAnalyzer:
    """
    Multimodal Vision Intelligence Auditor for Property Photos and Architectural Floorplans.
    Inspects gallery photos using Vision LLMs to detect renders vs reality, verify Living Quarters,
    and spot visual hidden defects.
    """

    IMAGE_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }

    def __init__(self, timeout: float = 25.0):
        self.timeout = timeout

    async def fetch_image_as_data_uri(
        self,
        client: httpx.AsyncClient,
        url: str,
        max_dimension: int = 1024,
        timeout: float = 6.0,
    ) -> str | None:
        """Fetches a remote image and returns an optimized base64 data URI.

        Downscales images exceeding max_dimension to conserve VRAM, tokens,
        and payload size. Converts to JPEG at quality 80.
        """
        if not url:
            return None
        if url.startswith("data:image/"):
            return url
        if not url.startswith(("http://", "https://")):
            return None

        try:
            resp = await client.get(url, headers=self.IMAGE_HEADERS, timeout=timeout)
            if resp.status_code != 200 or not resp.content:
                return None

            try:
                with Image.open(io.BytesIO(resp.content)) as raw_img:
                    proc_img = raw_img.convert("RGB") if raw_img.mode not in ("RGB", "L") else raw_img
                    w, h = proc_img.size
                    if max(w, h) > max_dimension:
                        proc_img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
                    buffer = io.BytesIO()
                    proc_img.save(buffer, format="JPEG", quality=80, optimize=True)
                    b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
                    return f"data:image/jpeg;base64,{b64}"
            except Exception as img_err:
                if len(resp.content) <= 2 * 1024 * 1024:
                    ct = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
                    b64 = base64.b64encode(resp.content).decode("ascii")
                    return f"data:{ct};base64,{b64}"
                logger.debug(f"[VisionAnalyzer] Image processing note for {url[:60]}: {img_err}")
                return None
        except Exception as e:
            logger.debug(f"[VisionAnalyzer] Fetch note for {url[:60]}: {e}")
            return None

    def build_openai_vision_payload(
        self,
        image_urls: list[str],
        declared_finish: str | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Builds standard OpenAI-compatible multimodal JSON payload."""
        user_content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": f"Deklarowany stan w ogłoszeniu: {declared_finish or 'nieokreślony'}.\n{VISION_AUDIT_PROMPT}",
            }
        ]

        for url in image_urls[:6]:  # Limit to top 6 images for latency and cost efficiency
            if url and url.startswith(("http://", "https://", "data:image/")):
                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": url, "detail": "low"},
                    }
                )

        _, _, model = resolve_vision_target(model_name=model_name)
        return {
            "model": model,
            "messages": [{"role": "user", "content": user_content}],
            "temperature": 0.1,
            "max_tokens": 1000,
        }

    def parse_vision_response(
        self,
        raw_text: str,
        declared_finish: str | None = None,
    ) -> dict[str, Any]:
        """Parses and validates Vision LLM response against Living Quarters rules."""
        default_res: dict[str, Any] = {
            "vision_is_render": False,
            "vision_finish_condition": "NIEZNANY",
            "vision_floorplan_details": {},
            "vision_defects": [],
            "discrepancy_detected": False,
            "discrepancy_note": None,
            "vision_summary": "",
        }

        if not raw_text:
            return default_res

        # Strip markdown code fencing if present
        clean_text = raw_text.strip()
        if "```json" in clean_text:
            clean_text = clean_text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in clean_text:
            clean_text = clean_text.split("```", 1)[1].split("```", 1)[0].strip()

        data: dict[str, Any] | None = None
        try:
            parsed = json.loads(clean_text)
            if isinstance(parsed, dict):
                data = parsed
        except Exception:
            first_brace = clean_text.find("{")
            last_brace = clean_text.rfind("}")
            if first_brace != -1 and last_brace > first_brace:
                try:
                    fallback_parsed = json.loads(clean_text[first_brace : last_brace + 1])
                    if isinstance(fallback_parsed, dict):
                        data = fallback_parsed
                except Exception:
                    data = None

        if not data:
            return default_res

        try:
            is_render = bool(data.get("is_render", False))
            visual_finish = str(data.get("visual_finish_condition", "NIEZNANY")).strip().upper()
            floorplan_details = data.get("floorplan_details") or {}
            defects = data.get("defects") or []
            summary = data.get("summary", "")
            discrepancy_note = data.get("discrepancy_note")

            # Check discrepancy with declared finish
            discrepancy_detected = False
            if declared_finish:
                d_norm = declared_finish.upper()
                if "ZAMIESZKANI" in d_norm and visual_finish in ("DO_WYKONCZENIA", "DEWELOPERSKI", "SUROWY"):
                    discrepancy_detected = True
                    if not discrepancy_note:
                        discrepancy_note = (
                            f"Sprzedający deklaruje stan '{declared_finish}', "
                            f"lecz analiza zdjęć wykazuje stan surowy/deweloperski ({visual_finish})."
                        )

            return {
                "vision_is_render": is_render,
                "vision_finish_condition": visual_finish,
                "vision_floorplan_details": floorplan_details,
                "vision_defects": defects,
                "discrepancy_detected": discrepancy_detected,
                "discrepancy_note": discrepancy_note,
                "vision_summary": summary,
            }
        except Exception as e:
            logger.debug(f"[VisionAnalyzer] JSON parse error: {e}")

        return default_res

    async def audit_images(
        self,
        client: httpx.AsyncClient | None = None,
        image_urls: list[str] | None = None,
        declared_finish: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """
        Runs vision audit on listing photos. If no vision API key is configured or
        image list is empty, returns safe default analysis.
        Target precedence: explicit args > VISION_* settings > configured LLM provider > defaults.
        Local Ollama needs no key: VISION_BASE_URL=http://localhost:11434/v1 + VISION_MODEL.
        """
        valid_urls = [u for u in (image_urls or []) if u and u.startswith(("http://", "https://", "data:image/"))]
        if not valid_urls or client is None:
            return self.parse_vision_response("", declared_finish=declared_finish)

        target_base, target_key, model_name = resolve_vision_target(api_base, api_key, model_name)
        is_local = is_local_vision_base(target_base)

        # In offline/no-key mode, return graceful default
        if not target_key and not is_local:
            return self.parse_vision_response("", declared_finish=declared_finish)

        # Download & encode images to base64 data URIs concurrently so local engines
        # (Ollama) and cloud APIs receive pre-processed, lightweight photos.
        tasks = [self.fetch_image_as_data_uri(client, u) for u in valid_urls[:6]]
        fetch_results = await asyncio.gather(*tasks, return_exceptions=True)
        prepared_images: list[str] = []
        for orig_url, res in zip(valid_urls[:6], fetch_results, strict=False):
            if isinstance(res, str) and res.startswith("data:image/"):
                prepared_images.append(res)
            elif not isinstance(res, Exception) and res:
                prepared_images.append(str(res))
            else:
                # Fallback to original url (e.g. mock tests or where fetch was skipped)
                prepared_images.append(orig_url)

        if not prepared_images:
            return self.parse_vision_response("", declared_finish=declared_finish)

        payload = self.build_openai_vision_payload(
            image_urls=prepared_images,
            declared_finish=declared_finish,
            model_name=model_name,
        )

        headers = {
            "Content-Type": "application/json",
        }
        if target_key:
            headers["Authorization"] = f"Bearer {target_key}"

        try:
            resp = await client.post(
                f"{target_base}/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                body = resp.json()
                content = body["choices"][0]["message"]["content"]
                return self.parse_vision_response(content, declared_finish=declared_finish)
            logger.debug(f"[VisionAnalyzer] HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.debug(f"[VisionAnalyzer] Request note: {e}")

        return self.parse_vision_response("", declared_finish=declared_finish)


vision_analyzer = VisionAnalyzer()
