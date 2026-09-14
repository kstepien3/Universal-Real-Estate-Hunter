from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _get_total_ram_bytes() -> int | None:
    """Detect total system physical RAM using native OS APIs (zero external dependencies)."""
    sys_name = platform.system()
    if sys_name == "Windows":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullTotalPhys)
        except Exception:
            return None
    elif sys_name == "Darwin":
        try:
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], timeout=2)
            return int(out.strip())
        except Exception:
            return None
    elif sys_name == "Linux":
        try:
            with Path("/proc/meminfo").open(encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) * 1024
        except Exception:
            return None
    return None


def _get_nvidia_gpu() -> dict[str, Any] | None:
    """Query nvidia-smi if installed to get GPU model name and total VRAM in MB."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        res = subprocess.run(
            [smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            first_line = res.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in first_line.split(",")]
            name = parts[0]
            vram_mb = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
            return {"name": name, "vram_mb": vram_mb}
    except Exception:
        return None
    return None


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


def detect_hardware_profile() -> dict[str, Any]:
    """
    Detects user hardware (OS, RAM, Apple Silicon, NVIDIA GPU) and recommends
    the optimal local Ollama model for technical real-estate analysis.
    """
    raw_system = platform.system()
    system_display = "macOS" if raw_system == "Darwin" else raw_system
    is_apple_silicon = raw_system == "Darwin" and platform.machine() in ("arm64", "aarch64")

    ram_bytes = _get_total_ram_bytes()
    ram_gb = round(ram_bytes / (1024**3), 1) if ram_bytes else None

    gpu_info = _get_nvidia_gpu()
    gpu_name = gpu_info["name"] if gpu_info else None
    vram_mb = gpu_info["vram_mb"] if gpu_info else None
    vram_gb = round(vram_mb / 1024, 1) if vram_mb else None

    # Construct human-readable device label
    label_parts = [system_display]
    if ram_gb:
        mem_type = "Unified RAM" if is_apple_silicon else "RAM"
        label_parts.append(f"{ram_gb:.0f} GB {mem_type}")
    if is_apple_silicon:
        label_parts.append("Apple Silicon (Metal)")
    elif gpu_name:
        gpu_label = gpu_name
        if vram_gb:
            gpu_label += f" ({vram_gb:.0f} GB VRAM)"
        label_parts.append(gpu_label)
    else:
        label_parts.append("Tylko procesor CPU")

    device_label = " · ".join(label_parts)

    # Determine recommended model based on architecture and memory
    if is_apple_silicon:
        if ram_gb and ram_gb >= 24.0:
            recommended_model = "bielik:11b-v2.3-instruct"
            recommendation_reason = (
                f"Wykryto macOS Apple Silicon z {ram_gb:.0f} GB zunifikowanej pamięci. "
                "Bielik 11B lub Qwen 14B zmieszczą się w całości w akceleracji Metal."
            )
        elif ram_gb and ram_gb >= 16.0:
            recommended_model = "bielik:11b-v2.3-instruct"
            recommendation_reason = (
                f"Wykryto macOS Apple Silicon ({ram_gb:.0f} GB RAM). "
                "Bielik 11B zapewni najwyższą polską precyzję prawną przy płynnej pracy."
            )
        else:
            recommended_model = "qwen2.5:7b"
            recommendation_reason = (
                "Wykryto macOS Apple Silicon (<16 GB RAM). "
                "Model qwen2.5:7b zapewni szybką i stabilną pracę bez presji na pamięć."
            )
    elif vram_gb and vram_gb >= 11.0:
        recommended_model = "bielik:11b-v2.3-instruct"
        recommendation_reason = (
            f"Wykryto GPU z {vram_gb:.0f} GB VRAM ({gpu_name}). "
            "Bielik 11B zmieści się w całości w pamięci karty z pełną akceleracją."
        )
    elif vram_gb and vram_gb >= 5.5:
        recommended_model = "qwen2.5:7b"
        recommendation_reason = (
            f"Wykryto GPU z {vram_gb:.0f} GB VRAM ({gpu_name}). "
            "Model qwen2.5:7b optymalnie wypełnia pamięć karty i generuje z pełną prędkością."
        )
    elif ram_gb and ram_gb >= 16.0:
        recommended_model = "qwen2.5:7b"
        recommendation_reason = (
            f"Wykryto {ram_gb:.0f} GB RAM (praca na CPU / brak VRAM >6GB). "
            "Model qwen2.5:7b zapewni dobrą jakość analizy."
        )
    else:
        recommended_model = "llama3.2:3b"
        recommendation_reason = (
            "Wykryto ograniczoną ilość pamięci lub brak dedykowanego GPU. "
            "Model 3B zapewni szybkie generowanie bez zacinania komputera."
        )

    return {
        "system": raw_system,
        "system_display": system_display,
        "is_apple_silicon": is_apple_silicon,
        "ram_total_gb": ram_gb,
        "gpu_name": gpu_name,
        "gpu_vram_gb": vram_gb,
        "device_label": device_label,
        "recommended_model": recommended_model,
        "recommendation_reason": recommendation_reason,
        "suggested_models": SUGGESTED_OLLAMA_MODELS,
    }
