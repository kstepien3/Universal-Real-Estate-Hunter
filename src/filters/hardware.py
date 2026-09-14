from __future__ import annotations

from typing import Any

SUGGESTED_OLLAMA_MODELS: list[dict[str, Any]] = [
    {
        "id": "qwen2.5:7b",
        "name": "Qwen 2.5 7B",
        "tag": "qwen2.5:7b",
        "size_gb": 4.7,
        "min_ram_gb": 8,
        "min_vram_gb": 6,
        "badge": "Zrównoważony",
        "description": "Najwyższa wierność schematów JSON i świetny język polski. Optymalny domyślny wybór.",
    },
    {
        "id": "bielik:11b-v2.3-instruct",
        "name": "Bielik 11B v2.3 Instruct",
        "tag": "bielik:11b-v2.3-instruct",
        "size_gb": 6.5,
        "min_ram_gb": 16,
        "min_vram_gb": 10,
        "badge": "Polski ekspert",
        "description": "Polski model (SpeakLeash). Eksperckie rozumienie pojęć prawnych, budowlanych i lokalnych realiów.",
    },
    {
        "id": "qwen2.5:14b",
        "name": "Qwen 2.5 14B",
        "tag": "qwen2.5:14b",
        "size_gb": 9.0,
        "min_ram_gb": 24,
        "min_vram_gb": 12,
        "badge": "Wysoka precyzja",
        "description": "Maksymalna dokładność audytu technicznego i wykrywania ryzyk. Wymaga mocniejszego GPU lub Maca.",
    },
    {
        "id": "llama3.2:3b",
        "name": "Llama 3.2 3B",
        "tag": "llama3.2:3b",
        "size_gb": 2.0,
        "min_ram_gb": 6,
        "min_vram_gb": 4,
        "badge": "Lekki (CPU)",
        "description": "Bardzo szybki i oszczędny. Rekomendowany dla słabszych laptopów bez dedykowanej karty graficznej.",
    },
    {
        "id": "llama3.1:8b",
        "name": "Llama 3.1 8B",
        "tag": "llama3.1:8b",
        "size_gb": 4.9,
        "min_ram_gb": 10,
        "min_vram_gb": 8,
        "badge": "Uniwersalny",
        "description": "Stabilny model od Meta. Dobra znajomość polskiego i wysoka odporność na błędy parsowania.",
    },
]
