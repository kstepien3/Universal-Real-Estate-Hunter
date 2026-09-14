import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn


def get_shared_status_file() -> str:
    from config import settings

    db_url = settings.DATABASE_URL
    if "sqlite" in db_url and db_url.startswith("sqlite+aiosqlite:///"):
        path = db_url.replace("sqlite+aiosqlite:///", "")
        p = Path(path)
        if p.parent and str(p.parent) not in (".", ""):
            return str(p.parent / "scrape_status.json")
    return str(Path("data") / "scrape_status.json")


def get_shared_cancel_file() -> str:
    from config import settings

    db_url = settings.DATABASE_URL
    if "sqlite" in db_url and db_url.startswith("sqlite+aiosqlite:///"):
        path = db_url.replace("sqlite+aiosqlite:///", "")
        p = Path(path)
        if p.parent and str(p.parent) not in (".", ""):
            return str(p.parent / ".scrape_cancel")
    return str(Path("data") / ".scrape_cancel")


def signal_shared_cancellation() -> None:
    try:
        p = Path(get_shared_cancel_file()).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    except Exception as e:
        logger.debug(f"Failed to signal shared cancellation: {e}")


def check_shared_cancellation() -> bool:
    try:
        return Path(get_shared_cancel_file()).exists()
    except Exception:
        return False


def clear_shared_cancellation() -> None:
    try:
        p = Path(get_shared_cancel_file())
        if p.exists():
            p.unlink()
    except Exception:
        pass


def write_shared_status(payload: dict[str, Any]) -> None:
    try:
        p = Path(get_shared_status_file()).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        temp_p = p.parent / f"{p.name}.tmp.{os.getpid()}"
        temp_p.write_text(json.dumps(payload), encoding="utf-8")
        temp_p.replace(p)
    except Exception as e:
        logger.debug(f"Failed to write shared status: {e}")


def read_shared_status() -> dict[str, Any] | None:
    try:
        p = Path(get_shared_status_file())
        if not p.exists():
            return None
        mtime = p.stat().st_mtime
        content = p.read_text(encoding="utf-8")
        data = json.loads(content)

        if data.get("is_running"):
            from src.services.scrape_lock import get_scrape_lock

            lock = get_scrape_lock()
            stale_time = time.time() - mtime
            # If no progress updates for > 45s and lock is free, or > 120s unconditionally
            if (stale_time > 45 and not lock.is_locked()) or stale_time > 120:
                data["is_running"] = False
                data["current_step"] = "Zakończono lub przerwano proces"
                write_shared_status(data)

        return data
    except Exception:
        return None


class ProgressTracker:
    """
    Central progress tracker broadcasting state to:
    1. Rich terminal progress bars
    2. Web dashboard real-time API (/api/scrape/status)
    """

    def __init__(self) -> None:
        self.is_running = False
        self.current_portal = ""
        self.current_step = "Bezczynny"
        self.current_page = 0
        self.total_pages = 0
        self.items_scraped = 0
        self.items_qualified = 0
        self.duplicates_found = 0
        self.percentage = 0
        self.cancel_requested = False
        self.logs: list[dict[str, str]] = []
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._rich_progress: Progress | None = None
        self._task_id: Any | None = None
        self._session_started_at: datetime | None = None
        # Progress phase windows: init 0-5, parallel scrape 5-60,
        # sequential listing analysis 60-95, wrap-up 95-100.
        self._parallel_total = 1
        self._parallel_done = 0

    def reset(self) -> None:
        """Resets tracker to idle state with empty logs."""
        self.is_running = False
        self.current_portal = ""
        self.current_step = "Bezczynny"
        self.current_page = 0
        self.total_pages = 0
        self.items_scraped = 0
        self.items_qualified = 0
        self.duplicates_found = 0
        self.percentage = 0
        self.cancel_requested = False
        self.logs = []
        self._session_started_at = None
        self._parallel_total = 1
        self._parallel_done = 0
        self._rich_progress = None
        self._task_id = None

    def _sync_shared_status(self) -> None:
        try:
            write_shared_status(self.get_status_payload())
        except Exception:
            pass

    def mark_starting(self) -> None:
        """Mark the tracker as running immediately, before the pipeline's heavy
        startup work begins. Prevents the dashboard from briefly reporting a
        stale 'completed' status from a previous cycle while a new one starts."""
        clear_shared_cancellation()
        self.is_running = True
        self.cancel_requested = False
        self.current_portal = ""
        self.current_step = "Inicjalizacja scrapingu..."
        self.current_page = 0
        self.total_pages = 0
        self.items_scraped = 0
        self.items_qualified = 0
        self.duplicates_found = 0
        self.percentage = 0
        self.logs = []
        self._session_started_at = datetime.now(UTC)
        self._sync_shared_status()

    def start_session(self, total_portals: int = 3) -> None:
        self.mark_starting()
        self.percentage = 5
        self._parallel_total = max(total_portals, 1)
        self._parallel_done = 0
        self.add_log("🚀 Rozpoczęto cykl scrapingu i analizy ofert.")
        self._sync_shared_status()

        try:
            self._rich_progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold cyan]{task.description}"),
                BarColumn(bar_width=35),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeElapsedColumn(),
                console=Console(),
                transient=False,
            )
            self._rich_progress.start()
            self._task_id = self._rich_progress.add_task("Monitorowanie...", total=100)
        except Exception as e:
            logger.debug(f"Rich progress init skipped: {e}")

    def _refresh_rich(self):
        if self._rich_progress and self._task_id is not None:
            self._rich_progress.update(
                self._task_id,
                description=f"[{self.current_portal or '...'}] {self.current_step}",
                completed=self.percentage,
            )

    def update_portal(self, portal_name: str, current_page: int, total_pages: int):
        self.current_portal = portal_name
        self.current_page = current_page
        self.total_pages = total_pages
        self.percentage = min(60, max(self.percentage, 5))
        self.current_step = f"Pobieranie {portal_name} (strona {current_page}/{total_pages})..."
        self.add_log(f"[{portal_name}] Pobieranie strony {current_page}/{total_pages}...")
        self._refresh_rich()
        self._sync_shared_status()

    def record_portal_done(self) -> None:
        """Mark one parallel scrape worker finished (scrape window 5-60, monotonic)."""
        self._parallel_done = min(self._parallel_total, self._parallel_done + 1)
        candidate = 5.0 + (self._parallel_done / self._parallel_total) * 55.0
        self.percentage = int(min(60, max(self.percentage, candidate)))
        self._refresh_rich()
        self._sync_shared_status()

    def update_portal_page(
        self,
        page: int,
        total_pages: int,
        items_done: int = 0,
        items_total: int = 0,
        phase: str = "search",
    ):
        """Live progress within a single portal scrape (called by scrapers)."""
        self.current_page = page
        self.total_pages = max(total_pages, 1)
        frac = (page - 1) / self.total_pages
        if phase == "detail" and items_total > 0:
            frac += (items_done / items_total) / self.total_pages
        frac = min(1.0, max(0.0, frac))
        # Completed workers + in-flight fraction; max() keeps parallel writers monotonic.
        candidate = 5.0 + ((self._parallel_done + frac) / self._parallel_total) * 55.0
        self.percentage = int(min(60, max(self.percentage, candidate)))

        if phase == "detail":
            self.current_step = (
                f"Pobieranie {self.current_portal} · strona {page}/{self.total_pages} · "
                f"szczegóły {items_done}/{items_total}..."
            )
        else:
            self.current_step = (
                f"Pobieranie {self.current_portal} · strona {page}/{self.total_pages} · {items_done} ogłoszeń"
            )
        self._refresh_rich()
        self._sync_shared_status()

    def set_processing_fraction(self, step_idx: int, total_steps: int, done: int = 0, total: int = 1) -> None:
        """Single analysis-window (60-95) updater: batch position plus in-batch fraction, never backwards."""
        steps = max(total_steps, 1)
        frac = min(1.0, max(0.0, done / max(total, 1)))
        self.current_step = f"Analiza ofert ({self.current_portal}): {done}/{total}..."
        self.percentage = int(min(95, max(self.percentage, 60.0 + ((step_idx - 1 + frac) / steps) * 35.0)))
        self._refresh_rich()
        self._sync_shared_status()

    def record_items(self, count: int, qualified: int = 0, duplicates: int = 0):
        self.items_scraped += count
        self.items_qualified += qualified
        self.duplicates_found += duplicates

    def add_log(self, message: str, level: str = "info", category: str | None = None):
        now_str = datetime.now(UTC).strftime("%H:%M:%S")
        if not category:
            if any(k in message for k in ("[Geokoder]", "[Geoportal]", "[Rejestry]", "[SIDUSIS]")) or level == "geo":
                cat = "geo"
            elif "[Odrzucono]" in message or level == "rejected":
                cat = "rejected"
            elif any(k in message for k in ("[AI Audit]", "[AI]", "LLM")) or level == "ai":
                cat = "ai"
            elif level == "success" or "[Zakwalifikowano]" in message or "⭐" in message:
                cat = "success"
            elif level == "error":
                cat = "error"
            elif level == "warning":
                cat = "warning"
            else:
                cat = "info"
        else:
            cat = category

        if cat == "rejected" and level == "warning":
            level = "info"

        # Prevent exact consecutive duplicate logs
        if self.logs and self.logs[-1].get("message") == message and self.logs[-1].get("category") == cat:
            return

        entry = {"time": now_str, "message": message, "level": level, "category": cat}
        self.logs.append(entry)
        if len(self.logs) > 500:
            self.logs.pop(0)
        self._sync_shared_status()

    def request_cancel(self):
        """Signals cooperative cancellation of the running scrape cycle."""
        self.cancel_requested = True
        signal_shared_cancellation()
        self.current_step = "Zatrzymywanie procesu..."
        self.add_log("🛑 Zażądano zatrzymania scrapingu przez użytkownika.", level="warning")
        self._refresh_rich()
        self._sync_shared_status()

    def is_cancelled(self) -> bool:
        return self.cancel_requested or check_shared_cancellation()

    def cancel_session(self):
        """Marks the session as stopped by user request."""
        self.is_running = False
        self.cancel_requested = False
        clear_shared_cancellation()
        self.current_step = "Zatrzymano przez użytkownika"
        self.add_log("🛑 Cykl scrapingu został przerwany przez użytkownika.", level="warning")
        if self._rich_progress and self._task_id is not None:
            self._rich_progress.update(
                self._task_id,
                description="[bold yellow]Zatrzymano!",
                completed=self.percentage,
            )
            self._rich_progress.stop()
            self._rich_progress = None
        self._sync_shared_status()

    def complete_session(self, summary: dict[str, Any]):
        self.is_running = False
        self.cancel_requested = False
        clear_shared_cancellation()
        if summary.get("error"):
            self.current_step = f"Błąd: {summary['error']}"
            self.add_log(f"❌ Cykl zakończony błędem: {summary['error']}", level="error")
        else:
            self.percentage = 100
            self.current_step = "Zakończono pomyślnie!"
            if summary.get("llm_enabled", True):
                calls = summary.get("llm_calls", 0)
                ok = summary.get("llm_successes", calls - summary.get("llm_failures", 0))
                failed = summary.get("llm_failures", summary.get("llm_failed", 0))
                cache_skipped = summary.get("llm_skipped", 0) - failed
                ai_part = (
                    f", AI LLM: {calls} prób ({ok} udanych"
                    + (f", {failed} nieudanych" if failed else "")
                    + f", pominięto {summary.get('llm_skipped', 0)}"
                    + (f" — cache/reguły: {cache_skipped}" if cache_skipped > 0 else "")
                    + ")"
                )
            else:
                ai_part = ", AI LLM: wyłączona w konfiguracji"
            self.add_log(
                f"✅ Cykl zakończony. Pobrane: {summary.get('total_scraped', 0)}, "
                f"Nowe: {summary.get('new_listings', 0)}, Zakwalifikowane: {summary.get('qualified', 0)}{ai_part}"
            )

        if self._rich_progress and self._task_id is not None:
            self._rich_progress.update(
                self._task_id,
                description="[bold green]Ukończono!",
                completed=100,
            )
            self._rich_progress.stop()
            self._rich_progress = None
        self._sync_shared_status()

    def get_status_payload(self) -> dict[str, Any]:
        elapsed = 0
        if self.is_running and self._session_started_at:
            elapsed = int((datetime.now(UTC) - self._session_started_at).total_seconds())
        return {
            "is_running": self.is_running,
            "cancel_requested": self.cancel_requested,
            "current_portal": self.current_portal,
            "current_step": self.current_step,
            "percentage": self.percentage,
            "current_page": self.current_page,
            "total_pages": self.total_pages,
            "elapsed_seconds": elapsed,
            "items_scraped": self.items_scraped,
            "items_qualified": self.items_qualified,
            "duplicates_found": self.duplicates_found,
            "logs": self.logs[-250:],
        }


# Global singleton tracker
global_tracker = ProgressTracker()
