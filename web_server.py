import asyncio
import json
import uuid
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field
import uvicorn
from config import config
from s3_logger import S3Logger
import db

# Configure logging
log_format = logging.Formatter('%(asctime)s | %(levelname)-8s | %(name)s | %(message)s')

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

if not root_logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(log_format)
    root_logger.addHandler(console_handler)

    try:
        file_handler = logging.FileHandler('visa_bot.log', mode='a', encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(log_format)
        root_logger.addHandler(file_handler)
    except Exception as e:
        print(f"Warning: Could not create log file: {e}")

logger = logging.getLogger(__name__)


# ── Pydantic models ────────────────────────────────────────────────────────────

class ApplicantCreate(BaseModel):
    passport_number: str = Field(..., min_length=5, max_length=15)
    visa_number: str = Field(..., min_length=3, max_length=20)
    mobile: str = Field(..., min_length=10, max_length=20)
    email: EmailStr
    country: str = "Pakistan"

class ApplicantUpdate(ApplicantCreate):
    pass

class Applicant(ApplicantCreate):
    id: str
    status: str = "pending"
    last_booked: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())

class TimeSlot(BaseModel):
    start: str = "09:00"
    end: str = "17:00"

class DaySchedule(BaseModel):
    day: str = "Monday"
    slots: List[TimeSlot] = []

class Schedule(BaseModel):
    enabled: bool = True
    days: List[DaySchedule] = []
    start_time: Optional[str] = None
    end_time: Optional[str] = None

class BotRunRequest(BaseModel):
    center: str = "Islamabad"
    applicant_ids: List[str] = []
    max_parallel: int = 2

class SessionStatus(BaseModel):
    session_id: str
    applicant_id: str
    passport_number: str
    status: str
    ip: Optional[str] = None
    poll_count: int = 0
    started_at: Optional[str] = None
    message: Optional[str] = None

class BotStatus(BaseModel):
    running: bool = False
    sessions: List[SessionStatus] = []
    logs: List[dict] = []
    log_cursor: int = 0
    slot_found: bool = False
    slot_details: Optional[dict] = None


# ── Parallel session dataclass ─────────────────────────────────────────────────

@dataclass
class ParallelSession:
    session_id: str
    applicant_id: str
    passport_number: str
    status: str = "starting"
    ip: Optional[str] = None
    poll_count: int = 0
    started_at: datetime = field(default_factory=datetime.now)
    message: Optional[str] = None
    task: Optional[asyncio.Task] = None
    browser: Any = None

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "applicant_id": self.applicant_id,
            "passport_number": self.passport_number,
            "status": self.status,
            "ip": self.ip,
            "poll_count": self.poll_count,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "message": self.message,
        }


# ── Bot runner ─────────────────────────────────────────────────────────────────

from proxy_manager import ProxyManager

class ParallelBotRunner:

    MAX_PARALLEL_LIMIT = None

    def __init__(self):
        self.running = False
        self.sessions: Dict[str, ParallelSession] = {}
        self.logs: List[dict] = []
        self._stop_requested = False
        self._slot_found = False
        self._slot_details: Optional[dict] = None
        self._lock = asyncio.Lock()
        self._log_cursor = 0
        self._proxy_managers: Dict[str, ProxyManager] = {}
        self._session_logs: Dict[str, List[dict]] = {}
        self._s3_logger: Optional[S3Logger] = None
        # Schedule cache — avoids DB calls in the sync helper method
        self._schedule_cache: dict = {"enabled": True, "days": []}

        if config.S3_LOGGING_ENABLED:
            try:
                self._s3_logger = S3Logger()
                logger.info("S3 session logging enabled")
            except Exception as e:
                logger.warning(f"Failed to initialize S3Logger: {e}")

    def update_schedule_cache(self, schedule: dict):
        """Keep an in-memory copy of the schedule for sync calculations."""
        self._schedule_cache = schedule

    def add_log(self, message: str, log_type: str = "", session_id: str = None, passport: str = None):
        prefix = ""
        if passport:
            prefix = f"[{passport}] "
        elif session_id and session_id in self.sessions:
            prefix = f"[{self.sessions[session_id].passport_number}] "

        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "message": f"{prefix}{message}",
            "type": log_type,
            "session_id": session_id,
        }
        self.logs.append(entry)
        if session_id:
            self._session_logs.setdefault(session_id, []).append(entry)

        if len(self.logs) > 500:
            trim_count = len(self.logs) - 500
            self._log_cursor = max(0, self._log_cursor - trim_count)
            self.logs = self.logs[-500:]
        logger.info(f"[Bot] {prefix}{message}")

    def get_logs_since(self, cursor: int = 0) -> tuple:
        if cursor < 0:
            cursor = 0
        return self.logs[cursor:], len(self.logs)

    async def _upload_session_logs(self, session_id: str, passport: str):
        if not self._s3_logger:
            return
        logs = self._session_logs.pop(session_id, [])
        if not logs:
            return
        try:
            await self._s3_logger.upload_session_logs(passport, session_id, logs)
        except Exception as e:
            logger.warning(f"Failed to upload session logs for {passport}: {e}")

    def _create_proxy_manager(self, session_id: str) -> Optional[ProxyManager]:
        if not config.PROXY_ENABLED:
            return None
        pm = ProxyManager(
            username=config.PROXY_USERNAME,
            password=config.PROXY_PASSWORD,
            host=config.PROXY_HOST,
            port=config.PROXY_PORT,
            sticky_duration_mins=config.PROXY_STICKY_MINS,
            max_rotations_per_session=config.PROXY_MAX_ROTATIONS,
        )
        self._proxy_managers[session_id] = pm
        return pm

    def _calculate_remaining_schedule_time(self) -> int:
        """Calculate remaining seconds in the current schedule window (uses cache)."""
        DAYS_OF_WEEK = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        DEFAULT_DURATION = 3600

        try:
            schedule = self._schedule_cache

            if not schedule.get("enabled", False):
                return DEFAULT_DURATION

            days = schedule.get("days", [])
            if not days:
                return DEFAULT_DURATION

            now = datetime.now()
            current_minutes = now.hour * 60 + now.minute
            current_day_name = DAYS_OF_WEEK[now.weekday()]

            for day_data in days:
                day_name = day_data.get("day", "")
                if day_name != current_day_name and day_name != "Daily":
                    continue

                for slot in day_data.get("slots", []):
                    start_h, start_m = map(int, slot.get("start", "09:00").split(":"))
                    end_h, end_m = map(int, slot.get("end", "17:00").split(":"))
                    start_minutes = start_h * 60 + start_m
                    end_minutes = end_h * 60 + end_m

                    in_window = False
                    if end_minutes < start_minutes:
                        if current_minutes >= start_minutes:
                            remaining_mins = (24 * 60 - current_minutes) + end_minutes
                            in_window = True
                        elif current_minutes < end_minutes:
                            remaining_mins = end_minutes - current_minutes
                            in_window = True
                    else:
                        if start_minutes <= current_minutes < end_minutes:
                            remaining_mins = end_minutes - current_minutes
                            in_window = True

                    if in_window:
                        remaining_secs = remaining_mins * 60
                        logger.info(f"Schedule window ends at {slot.get('end')}, {remaining_mins} min remaining")
                        return max(remaining_secs, 300)

            return DEFAULT_DURATION

        except Exception as e:
            logger.error(f"Error calculating schedule time: {e}")
            return DEFAULT_DURATION

    async def _run_single_session(
        self,
        session: ParallelSession,
        applicant_data: dict,
        center: str,
        proxy_manager: Optional[ProxyManager],
    ):
        from browser_engine import BrowserEngine
        from config import Applicant as ConfigApplicant

        session_id = session.session_id
        passport = session.passport_number
        rotation_count = 0
        max_rotations = config.PROXY_MAX_ROTATIONS if config.PROXY_ENABLED else 1

        app_obj = ConfigApplicant(
            country=applicant_data["country"],
            passport_number=applicant_data["passport_number"],
            visa_number=applicant_data["visa_number"],
            mobile=applicant_data["mobile"],
            email=applicant_data["email"],
            row_index=0,
        )

        while rotation_count < max_rotations and not self._stop_requested and not self._slot_found:
            rotation_count += 1
            browser = None

            try:
                if rotation_count == 1:
                    self.add_log("Starting session...", session_id=session_id, passport=passport)
                else:
                    self.add_log(f"Rotating IP (rotation {rotation_count}/{max_rotations})...", session_id=session_id, passport=passport)

                session.status = "starting"

                if proxy_manager:
                    await proxy_manager.rotate(reason=f"rotation_{rotation_count}")

                browser = BrowserEngine(proxy_manager=proxy_manager)
                session.browser = browser
                await browser.start()

                if proxy_manager:
                    ip = await proxy_manager.verify_ip()
                    session.ip = ip
                    self.add_log(f"Connected with IP: {ip}", session_id=session_id, passport=passport)

                if self._stop_requested or self._slot_found:
                    self.add_log("Stop requested, closing session", session_id=session_id, passport=passport)
                    await browser.close()
                    session.status = "stopped"
                    await self._save_run_record(session, center)
                    await self._upload_session_logs(session_id, passport)
                    return

                session_duration = min(
                    config.SESSION_DURATION_MINUTES * 60,
                    self._calculate_remaining_schedule_time(),
                )
                self.add_log(f"Session duration: {session_duration // 60} min", session_id=session_id, passport=passport)

                session.status = "logging_in"

                success = await browser.book_appointment(
                    app_obj,
                    config.DATE_RANGE_START,
                    config.DATE_RANGE_END,
                    max_hunt_duration=session_duration,
                    center=center,
                )

                await browser.close()
                session.browser = None

                if self._slot_found:
                    session.status = "slot_found"
                    self.add_log(" Session completed - SLOT FOUND!", "success", session_id=session_id, passport=passport)
                    await self._save_run_record(session, center)
                    await self._upload_session_logs(session_id, passport)
                    return
                elif success:
                    session.status = "slot_found"
                    self._slot_found = True
                    self._slot_details = {
                        "passport_number": passport,
                        "session_id": session_id,
                        "found_at": datetime.now().isoformat(),
                    }
                    self.add_log(" Session completed - SLOT FOUND!", "success", session_id=session_id, passport=passport)
                    await self._save_run_record(session, center)
                    await self._upload_session_logs(session_id, passport)
                    return
                else:
                    self.add_log("No slots found, will rotate IP...", session_id=session_id, passport=passport)
                    if rotation_count < max_rotations and not self._stop_requested and not self._slot_found:
                        await asyncio.sleep(config.SESSION_GAP_SECONDS)

            except asyncio.CancelledError:
                self.add_log("Session cancelled", session_id=session_id, passport=passport)
                session.status = "stopped"
                if browser:
                    try:
                        await browser.close()
                    except Exception:
                        pass
                session.browser = None
                await self._save_run_record(session, center)
                await self._upload_session_logs(session_id, passport)
                return

            except Exception as e:
                logger.exception(f"Session error for {passport}: {e}")
                self.add_log(f"Error: {str(e)[:50]}", "error", session_id=session_id, passport=passport)
                if browser:
                    try:
                        await browser.close()
                    except Exception:
                        pass
                    session.browser = None
                self.add_log("Retrying with new IP...", session_id=session_id, passport=passport)
                await asyncio.sleep(config.SESSION_GAP_SECONDS)

        session.status = "completed"
        self.add_log(
            f"Session completed after {rotation_count} rotations - no slots found",
            session_id=session_id,
            passport=passport,
        )
        await self._save_run_record(session, center)
        await self._upload_session_logs(session_id, passport)

    async def _save_run_record(self, session: ParallelSession, center: str):
        """Persist a run record to MongoDB for analytics."""
        try:
            record = {
                "applicant_id": session.applicant_id,
                "passport_number": session.passport_number,
                "session_id": session.session_id,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "started_at": session.started_at.isoformat() if session.started_at else None,
                "ended_at": datetime.now().isoformat(),
                "poll_count": session.poll_count,
                "status": session.status,
                "center": center,
            }
            await db.save_run_record(record)
        except Exception as e:
            logger.warning(f"Failed to save run record for {session.passport_number}: {e}")

    async def run(self, applicant_ids: List[str], center: str = "Islamabad", max_parallel: int = 2):
        async with self._lock:
            if self.running:
                return
            self.running = True

        self._stop_requested = False
        self._slot_found = False
        self._slot_details = None
        self.sessions.clear()
        self._proxy_managers.clear()

        max_parallel = max(1, max_parallel)

        self.add_log("=" * 50)
        self.add_log("PARALLEL BOT STARTED")
        self.add_log(f"Center: {center}")
        self.add_log(f"Applicants: {len(applicant_ids)}")
        self.add_log(f"Max parallel: {max_parallel}")
        self.add_log("=" * 50)

        try:
            data = await db.load_data()
            applicants_map = {a["id"]: a for a in data["applicants"]}

            selected_applicants = []
            for app_id in applicant_ids:
                if app_id in applicants_map:
                    selected_applicants.append(applicants_map[app_id])
                else:
                    self.add_log(f"Applicant {app_id} not found, skipping", "error")

            if not selected_applicants:
                self.add_log("No valid applicants to process", "error")
                return

            for app in selected_applicants:
                for a in data["applicants"]:
                    if a["id"] == app["id"]:
                        a["status"] = "processing"
                        break
            await db.save_data(data)

            tasks = []
            for i, applicant in enumerate(selected_applicants[:max_parallel]):
                session_id = f"sess_{uuid.uuid4().hex[:8]}"
                session = ParallelSession(
                    session_id=session_id,
                    applicant_id=applicant["id"],
                    passport_number=applicant["passport_number"],
                    status="starting",
                    started_at=datetime.now(),
                )
                self.sessions[session_id] = session

                proxy_manager = self._create_proxy_manager(session_id)

                task = asyncio.create_task(
                    self._run_single_session(session, applicant, center, proxy_manager)
                )
                session.task = task
                tasks.append(task)

                self.add_log(
                    f"Created session {i + 1}/{min(len(selected_applicants), max_parallel)}",
                    session_id=session_id,
                    passport=applicant["passport_number"],
                )
                await asyncio.sleep(5)

            self.add_log(f"All {len(tasks)} sessions started, waiting for completion...")

            while tasks:
                if self._slot_found:
                    self.add_log("🎉 SLOT FOUND - Stopping all sessions!", "success")
                    for session in self.sessions.values():
                        if session.task and not session.task.done():
                            session.task.cancel()
                    break

                if self._stop_requested:
                    self.add_log("Stop requested - cancelling all sessions")
                    for session in self.sessions.values():
                        if session.task and not session.task.done():
                            session.task.cancel()
                    break

                done, pending = await asyncio.wait(tasks, timeout=1.0, return_when=asyncio.FIRST_COMPLETED)
                tasks = list(pending)

                for task in done:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        logger.error(f"Task error: {e}")

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            data = await db.load_data()
            for session in self.sessions.values():
                for a in data["applicants"]:
                    if a["id"] == session.applicant_id:
                        if session.status == "slot_found":
                            a["status"] = "slot_found"
                            a["slot_detected_at"] = datetime.now().isoformat()
                        elif session.status == "stopped":
                            a["status"] = "pending"
                        elif session.status == "failed":
                            a["status"] = "failed"
                        else:
                            a["status"] = "no_slot"
                        break
            await db.save_data(data)

            self.add_log("=" * 50)
            if self._slot_found:
                self.add_log(f"🎉 SUCCESS! Slot found for {self._slot_details.get('passport_number', 'unknown')}", "success")
                self.add_log(f"Details: {self._slot_details}", "success")
            else:
                self.add_log("All sessions completed - no slots found")
            self.add_log("=" * 50)

        except Exception as e:
            logger.exception(f"Parallel bot runner error: {e}")
            self.add_log(f"Runner error: {str(e)}", "error")
        finally:
            self.running = False
            self._proxy_managers.clear()
            completed = sum(1 for s in self.sessions.values() if s.status in ["completed", "slot_found"])
            failed = sum(1 for s in self.sessions.values() if s.status == "failed")
            stopped = sum(1 for s in self.sessions.values() if s.status == "stopped")
            self.add_log(f"Final: {completed} completed, {failed} failed, {stopped} stopped")

    def stop(self):
        self._stop_requested = True
        self.add_log("Stop requested for all sessions...")

    async def force_stop(self):
        self._stop_requested = True
        logger.info("Force stop initiated for all sessions")

        for session_id, session in self.sessions.items():
            if session.browser:
                try:
                    await session.browser.close()
                except Exception as e:
                    logger.error(f"Error force closing browser {session_id}: {e}")
                session.browser = None

            if session.task and not session.task.done():
                session.task.cancel()

            session.status = "stopped"

        self.running = False
        self.add_log("All sessions force stopped", "error")

    def get_status(self) -> dict:
        return {
            "running": self.running,
            "sessions": [s.to_dict() for s in self.sessions.values()],
            "slot_found": self._slot_found,
            "slot_details": self._slot_details,
        }


# ── Global runner ──────────────────────────────────────────────────────────────

bot_runner = ParallelBotRunner()


# ── Scheduler ──────────────────────────────────────────────────────────────────

async def check_schedule():
    DAYS_OF_WEEK = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

    while True:
        try:
            data = await db.load_data()
            schedule = data.get("schedule", {})

            # Keep schedule cache fresh
            bot_runner.update_schedule_cache(schedule)

            if schedule.get("enabled", False) and not bot_runner.running:
                days = schedule.get("days", [])
                if days:
                    now = datetime.now()
                    current_minutes = now.hour * 60 + now.minute
                    current_day_name = DAYS_OF_WEEK[now.weekday()]

                    in_window = False
                    for day_data in days:
                        day_name = day_data.get("day", "")
                        if day_name != current_day_name and day_name != "Daily":
                            continue
                        for slot in day_data.get("slots", []):
                            start_h, start_m = map(int, slot.get("start", "09:00").split(":"))
                            end_h, end_m = map(int, slot.get("end", "17:00").split(":"))
                            start_minutes = start_h * 60 + start_m
                            end_minutes = end_h * 60 + end_m
                            if end_minutes < start_minutes:
                                if current_minutes >= start_minutes or current_minutes < end_minutes:
                                    in_window = True
                                    break
                            else:
                                if start_minutes <= current_minutes < end_minutes:
                                    in_window = True
                                    break
                        if in_window:
                            break

                    if in_window:
                        pending = [a for a in data["applicants"] if a.get("status") == "pending"]
                        if pending:
                            settings = data.get("settings", {})
                            max_parallel = settings.get("max_parallel", 2)
                            applicant_ids = [a["id"] for a in pending[:max_parallel]]
                            logger.info(f"Scheduled run triggered for {len(applicant_ids)} applicants")
                            asyncio.create_task(bot_runner.run(applicant_ids, max_parallel=max_parallel))

        except Exception as e:
            logger.error(f"Scheduler error: {e}")

        await asyncio.sleep(60)


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    # Seed schedule cache from DB on startup
    try:
        data = await db.load_data()
        bot_runner.update_schedule_cache(data.get("schedule", {}))
    except Exception as e:
        logger.warning(f"Could not pre-load schedule cache: {e}")

    asyncio.create_task(check_schedule())
    logger.info("Scheduler started")
    logger.info("Qatar Visa Bot Web Server is ready (Parallel Mode)")
    yield
    logger.info("Shutting down - cleaning up...")
    await bot_runner.force_stop()
    await db.disconnect()
    logger.info("Shutdown complete")


# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Qatar Visa Bot API",
    description="REST API for the visa bot control panel with parallel session support",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

web_dir = Path(__file__).parent / "web"
if not web_dir.exists():
    web_dir = Path(__file__).parent / "static"

if web_dir.exists():
    app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")
    logger.info(f"Serving static files from: {web_dir}")
else:
    logger.warning(f"Static files directory not found: {web_dir}")


# ── Static routes ──────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    index_path = web_dir / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"message": "Qatar Visa Bot API", "version": "2.0.0", "status": "running"}

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "bot_running": bot_runner.running,
        "active_sessions": len([s for s in bot_runner.sessions.values() if s.status not in ["completed", "failed", "stopped"]]),
        "timestamp": datetime.now().isoformat(),
    }

@app.get("/styles.css")
async def styles():
    css_path = web_dir / "styles.css"
    if css_path.exists():
        return FileResponse(str(css_path), media_type="text/css")
    raise HTTPException(status_code=404, detail="CSS file not found")

@app.get("/app.js")
async def script():
    js_path = web_dir / "app.js"
    if js_path.exists():
        return FileResponse(str(js_path), media_type="application/javascript")
    raise HTTPException(status_code=404, detail="JS file not found")


# ── Applicants ─────────────────────────────────────────────────────────────────

@app.get("/api/applicants")
async def list_applicants():
    data = await db.load_data()
    return {"applicants": data.get("applicants", [])}

@app.post("/api/applicants", response_model=Applicant)
async def create_applicant(applicant: ApplicantCreate):
    data = await db.load_data()
    new_applicant = {
        "id": f"app_{uuid.uuid4().hex[:8]}",
        "country": applicant.country,
        "passport_number": applicant.passport_number.upper(),
        "visa_number": applicant.visa_number.upper(),
        "mobile": applicant.mobile,
        "email": applicant.email.lower(),
        "status": "pending",
        "last_booked": None,
        "created_at": datetime.now().isoformat(),
    }
    data["applicants"].append(new_applicant)
    await db.save_data(data)
    logger.info(f"Created applicant: {new_applicant['passport_number']}")
    return new_applicant

@app.put("/api/applicants/{applicant_id}", response_model=Applicant)
async def update_applicant(applicant_id: str, applicant: ApplicantUpdate):
    data = await db.load_data()
    for i, a in enumerate(data["applicants"]):
        if a["id"] == applicant_id:
            data["applicants"][i].update({
                "passport_number": applicant.passport_number.upper(),
                "visa_number": applicant.visa_number.upper(),
                "mobile": applicant.mobile,
                "email": applicant.email.lower(),
                "status": "pending",
            })
            await db.save_data(data)
            logger.info(f"Updated applicant: {applicant_id}")
            return data["applicants"][i]
    raise HTTPException(status_code=404, detail="Applicant not found")

@app.delete("/api/applicants/{applicant_id}")
async def delete_applicant(applicant_id: str):
    data = await db.load_data()
    original_len = len(data["applicants"])
    data["applicants"] = [a for a in data["applicants"] if a["id"] != applicant_id]
    if len(data["applicants"]) == original_len:
        raise HTTPException(status_code=404, detail="Applicant not found")
    await db.save_data(data)
    logger.info(f"Deleted applicant: {applicant_id}")
    return {"message": "Applicant deleted"}

@app.post("/api/applicants/{applicant_id}/reset", response_model=Applicant)
async def reset_applicant(applicant_id: str):
    data = await db.load_data()
    for a in data["applicants"]:
        if a["id"] == applicant_id:
            a["status"] = "pending"
            a["last_booked"] = None
            await db.save_data(data)
            logger.info(f"Reset applicant: {applicant_id}")
            return a
    raise HTTPException(status_code=404, detail="Applicant not found")


# ── Schedule ───────────────────────────────────────────────────────────────────

@app.get("/api/schedule", response_model=Schedule)
async def get_schedule():
    data = await db.load_data()
    schedule = data.get("schedule", {"enabled": True, "days": []})
    bot_runner.update_schedule_cache(schedule)
    return schedule

@app.post("/api/schedule", response_model=Schedule)
async def update_schedule(schedule: Schedule):
    data = await db.load_data()
    data["schedule"] = schedule.model_dump()
    await db.save_data(data)
    bot_runner.update_schedule_cache(data["schedule"])
    logger.info("Schedule updated")
    return data["schedule"]


# ── Settings ───────────────────────────────────────────────────────────────────

@app.get("/api/settings")
async def get_settings():
    data = await db.load_data()
    return data.get("settings", {"max_parallel": 10})

@app.post("/api/settings")
async def update_settings(settings: dict):
    data = await db.load_data()
    if "max_parallel" in settings:
        settings["max_parallel"] = max(1, int(settings["max_parallel"]))
    data["settings"] = {**data.get("settings", {}), **settings}
    await db.save_data(data)
    logger.info(f"Settings updated: {settings}")
    return data["settings"]


# ── Bot control ────────────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status(log_cursor: int = 0):
    logs, new_cursor = bot_runner.get_logs_since(log_cursor)
    status = bot_runner.get_status()

    data = await db.load_data()
    applicant_updates = [
        {"id": a["id"], "status": a["status"]}
        for a in data["applicants"]
        if a.get("status") in ["processing", "completed", "failed", "slot_found", "no_slot"]
    ]

    return {
        "running": status["running"],
        "sessions": status["sessions"],
        "slot_found": status["slot_found"],
        "slot_details": status["slot_details"],
        "logs": logs,
        "log_cursor": new_cursor,
        "applicants": applicant_updates,
    }

@app.post("/api/run")
async def run_bot(background_tasks: BackgroundTasks, request: BotRunRequest):
    if bot_runner.running:
        raise HTTPException(status_code=400, detail="Bot is already running")

    data = await db.load_data()

    if not request.applicant_ids:
        pending = [a for a in data["applicants"] if a.get("status") == "pending"]
        if not pending:
            raise HTTPException(status_code=400, detail="No pending applicants")
        request.applicant_ids = [a["id"] for a in pending[:request.max_parallel]]

    all_ids = {a["id"] for a in data["applicants"]}
    invalid_ids = [aid for aid in request.applicant_ids if aid not in all_ids]
    if invalid_ids:
        raise HTTPException(status_code=400, detail=f"Invalid applicant IDs: {invalid_ids}")

    max_parallel = max(1, request.max_parallel)
    applicant_ids = request.applicant_ids

    background_tasks.add_task(
        bot_runner.run,
        applicant_ids=applicant_ids,
        center=request.center,
        max_parallel=max_parallel,
    )

    logger.info(f"Bot started for {len(applicant_ids)} applicants (max parallel: {max_parallel})")
    return {"message": "Bot started", "center": request.center, "applicant_ids": applicant_ids, "max_parallel": max_parallel}

@app.post("/api/stop")
async def stop_bot():
    await bot_runner.force_stop()
    logger.info("Bot stopped by user")
    return {"message": "All sessions force stopped"}


# ── Analytics ──────────────────────────────────────────────────────────────────

@app.get("/api/analytics")
async def get_analytics():
    per_applicant = await db.get_applicant_analytics()
    global_stats = await db.get_global_stats()
    return {"per_applicant": per_applicant, "stats": global_stats}


# ── Entrypoint ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")

    print("\n" + "=" * 60)
    print("Qatar Visa Bot - Web Control Panel (Parallel Mode)")
    print("=" * 60)
    print(f"\nServer starting on {host}:{port}")
    print(f"Open in browser: http://localhost:{port}")
    print("Press Ctrl+C to stop\n")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=True,
    )
