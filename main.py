import asyncio
import json
import os
import re
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import httpx
import jwt
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY environment variable is not set")

SUPABASE_URL        = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_ANON_KEY   = os.getenv("SUPABASE_ANON_KEY", "").strip()
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "").strip()  # legacy fallback
OPENWEATHER_KEY     = os.getenv("OPENWEATHER_API_KEY", "").strip()

MODEL = "gemini-2.5-flash"
ROOT  = Path(__file__).parent

client = genai.Client(api_key=API_KEY)

# ─────────────────────────────────────────────────────────────────────
# MIGRATION (Yuki) — run once in the Supabase SQL Editor.
# Weight log — one entry per user per day, upserted never duplicated.
# ─────────────────────────────────────────────────────────────────────
# CREATE TABLE IF NOT EXISTS weight_logs (
#   id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
#   user_id     uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
#   date        date NOT NULL,
#   weight_kg   numeric(5,2) NOT NULL CHECK (weight_kg >= 20 AND weight_kg <= 300),
#   created_at  timestamptz NOT NULL DEFAULT now(),
#   UNIQUE (user_id, date)
# );
# ALTER TABLE weight_logs ENABLE ROW LEVEL SECURITY;
# CREATE POLICY "read own"   ON weight_logs FOR SELECT USING (auth.uid() = user_id);
# CREATE POLICY "insert own" ON weight_logs FOR INSERT WITH CHECK (auth.uid() = user_id);
# CREATE POLICY "update own" ON weight_logs FOR UPDATE USING (auth.uid() = user_id);
# CREATE POLICY "delete own" ON weight_logs FOR DELETE USING (auth.uid() = user_id);
# ─────────────────────────────────────────────────────────────────────

# ── JWKS cache (Supabase ECC P-256 JWT verification) ─────────────────
_jwks_keys: list = []

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _jwks_keys
    if SUPABASE_URL:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json")
                if r.status_code == 200:
                    _jwks_keys = r.json().get("keys", [])
        except Exception as e:
            print(f"JWKS load warning: {e}")
    yield

app = FastAPI(title="Nouri", lifespan=lifespan)


# ── Auth ─────────────────────────────────────────────────────────────
def get_current_user(authorization: str = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.removeprefix("Bearer ").strip()

    # Primary: JWKS-based (ECC P-256 or RSA — whatever Supabase uses)
    if _jwks_keys:
        try:
            header   = jwt.get_unverified_header(token)
            kid      = header.get("kid")
            key_data = next((k for k in _jwks_keys if k.get("kid") == kid), _jwks_keys[0])
            pub_key  = (ECAlgorithm if key_data.get("kty") == "EC" else RSAAlgorithm).from_jwk(
                json.dumps(key_data)
            )
            alg     = "ES256" if key_data.get("kty") == "EC" else "RS256"
            payload = jwt.decode(token, pub_key, algorithms=[alg], options={"verify_aud": False})
            return {"id": payload["sub"], "email": payload.get("email", "")}
        except Exception:
            pass  # fall through to HS256 fallback

    # Fallback: legacy HS256 shared secret
    if SUPABASE_JWT_SECRET:
        try:
            payload = jwt.decode(
                token, SUPABASE_JWT_SECRET, algorithms=["HS256"],
                options={"verify_aud": False},
            )
            return {"id": payload["sub"], "email": payload.get("email", "")}
        except Exception:
            pass

    raise HTTPException(status_code=401, detail="Invalid or expired token")


def _sb_headers(token: str) -> dict:
    return {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ── Rate limiting (Priya) ───────────────────────────────────────────
# Per-user sliding window, in-memory. Single Render instance → adequate.
# (Multi-worker/horizontal scaling would need a shared store like Redis.)
_RATE_WINDOW = 60.0    # seconds
# Per-bucket request ceilings (per user, per window). Scan is most sensitive.
_RATE_LIMITS: dict[str, int] = {
    "scan":   30,
    "butler": 30,
    "memory": 120,
    "goals":  60,
}
_rate_buckets: dict[str, deque] = defaultdict(deque)
_rate_lock = threading.Lock()


def _check_rate(key: str, limit: int) -> None:
    now = time.monotonic()
    with _rate_lock:
        dq = _rate_buckets[key]
        while dq and dq[0] <= now - _RATE_WINDOW:
            dq.popleft()
        if len(dq) >= limit:
            raise HTTPException(status_code=429, detail="Slow down — try again in a moment.")
        dq.append(now)


def rate_limited(bucket: str):
    """Dependency that authenticates the user AND enforces a per-user rate limit."""
    limit = _RATE_LIMITS.get(bucket, 60)
    def dep(user: dict = Depends(get_current_user)) -> dict:
        _check_rate(f"{bucket}:{user['id']}", limit)
        return user
    return dep


# ── Schemas ─────────────────────────────────────────────────────────
class FoodItem(BaseModel):
    name: str
    portion: str = Field(description="e.g. '150 g', '1 piece', '1 cup'")
    # Canonical point estimates — always filled, used for tracking/summing.
    calories: int
    protein_g: float
    carbs_g: float
    fat_g: float
    # Per-item confidence in the macro estimate.
    confidence: Literal["low", "medium", "high"] = "medium"
    # When confidence is "low", a human-readable calorie range like "320–480".
    # Empty string when confidence is medium/high.
    calories_range: str = ""


class FoodScan(BaseModel):
    items: list[FoodItem]
    confidence: Literal["low", "medium", "high"]


class FridgeItem(BaseModel):
    name: str
    portion: str = ""
    # Tolerant gegenüber dem, was das Modell vorschlägt — verhindert Schema-Crashs
    category: str = "other"


class FridgeScan(BaseModel):
    items: list[FridgeItem]


Sex = Literal["m", "f"]
Activity = Literal["sedentary", "light", "moderate", "active", "very_active"]
Goal = Literal["lose", "maintain", "gain"]


class Profile(BaseModel):
    # Bounds kept in sync with the onboarding input validation in index.html.
    age: int = Field(ge=10, le=100)
    weight_kg: float = Field(ge=30, le=300)
    height_cm: float = Field(ge=100, le=250)
    sex: Sex
    activity: Activity
    goal: Goal


class Targets(BaseModel):
    calories: int
    protein_g: int
    carbs_g: int
    fat_g: int


class Recipe(BaseModel):
    title: str
    summary: str = Field(description="Elegant, short subtitle — max. 12 words.")
    duration_min: int
    servings: int
    calories_per_serving: int
    protein_g: int
    carbs_g: int
    fat_g: int
    ingredients: list[str] = Field(description="Zutaten mit präzisen Mengenangaben.")
    steps: list[str]


class Restrictions(BaseModel):
    allergies: list[str] = []
    noGos: list[str] = []


class RefineRequest(BaseModel):
    original_items: list[FoodItem]
    description: str


class TastePrefs(BaseModel):
    cuisines: list[str] = []
    goals: list[str] = []


class RecipeRequest(BaseModel):
    ingredients: list[str] = []
    targets: Targets | None = None
    remaining_calories: int | None = None
    restrictions: Restrictions | None = None
    taste: TastePrefs | None = None


class HealthContextInput(BaseModel):
    diabetes_type: str | None = None  # None, "type1", "type2"
    lactose_intolerant: bool = False
    gluten_intolerant: bool = False
    hypertension: bool = False
    other_notes: str = ""


class MealItemForCheck(BaseModel):
    name: str
    portion: str = ""
    calories: int = 0
    protein_g: float = 0
    carbs_g: float = 0
    fat_g: float = 0


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatContext(BaseModel):
    calories_today: float = 0
    protein_today: float = 0
    carbs_today: float = 0
    fat_today: float = 0
    calories_target: int | None = None
    protein_target: int | None = None
    user_name: str | None = None
    today_date: str | None = None  # user's local date, YYYY-MM-DD
    # Structured [{memory_text, category}] (current frontend) or legacy list[str]
    # from an older cached build — _normalize_memories() accepts both.
    memories: list = []
    recent_weights: list[dict] | None = None  # last 7: [{"date": "YYYY-MM-DD", "weight_kg": 74.5}]
    goal_weight: float | None = None
    goal_type: str | None = None  # "lose" / "gain" / "maintain" / "clean"


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    mode: str = "ELITE_BUTLER"
    context: ChatContext | None = None


class ButlerCheckRequest(BaseModel):
    calories_today: float = 0
    protein_today: float = 0
    carbs_today: float = 0
    fat_today: float = 0
    calories_target: int = 2000
    protein_target: int = 150
    carbs_target: int = 200
    hour: int = 12
    mode: str = "ELITE_BUTLER"
    user_name: str | None = None
    fired_today: list[str] = []
    health_context: HealthContextInput | None = None
    last_meal: list[MealItemForCheck] = []
    memories: list = []  # structured or legacy — see ChatContext.memories
    recent_weights: list[dict] | None = None
    goal_weight: float | None = None
    goal_type: str | None = None


# Shared character spine — every mode inherits this. Tone is layered on top.
_BUTLER_CORE = (
    "You are 'Architect', the nutrition companion inside Nouri — not a chatbot, a companion who knows this person.\n"
    "CHARACTER: calm, direct, genuinely attentive. You remember what matters and reason from it.\n"
    "LANGUAGE: always respond in English (US launch).\n"
    "NEVER use sycophantic filler — no 'Great question!', 'Absolutely!', 'I'm happy to help', "
    "'Of course!', or empty enthusiasm. No exclamation-mark cheerleading.\n"
    "NEVER claim a capability you do not have. NEVER say you lack access to the user's data, "
    "history, or numbers — the relevant context is always provided to you below; use it.\n"
    "NEVER give generic encouragement ('keep it up!', 'you've got this') without a specific, "
    "number-backed reason drawn from their actual data.\n"
    "Lead with substance: a fact, a status read, or one concrete recommendation. Be brief. "
    "When you cite numbers, use the ones provided. Address the user by first name when known, "
    "never with titles like 'Sir' or 'Mr.'.\n"
    "WEIGHT: never use 'obese', 'fat', 'overweight', 'bad', 'cheat', or any shaming language. "
    "Speak factually — 'You're 1.2kg down', never 'Amazing progress!' or 'You're falling behind'. "
    "Acknowledge plateaus calmly: 'Plateaus happen. Stay consistent, the trend will resume.' "
    "Only raise weight if the user asks or there is a meaningful change (>0.5kg over ~7 days). "
    "Weight is one signal among many — never the primary measure of a person."
)

BUTLER_PROMPTS: dict[str, str] = {
    "ELITE_BUTLER": (
        _BUTLER_CORE + "\n\nTONE — Elite Butler: formal, precise, quietly luxurious. "
        "Measured and composed; understatement over emphasis. 2–4 sentences."
    ),
    "PERFORMANCE_COACH": (
        _BUTLER_CORE + "\n\nTONE — Performance Coach: direct and motivating, second person, no excuses. "
        "Hard metrics, clear demands, short. Push without insulting."
    ),
    "STRATEGIC_BUDDY": (
        _BUTLER_CORE + "\n\nTONE — Strategic Buddy: casual, honest, on equal footing — intelligent, no bro-talk. "
        "Plain and warm, still specific. Short."
    ),
}


# ── Macro logic ────────────────────────────────────────────────────
ACTIVITY_FACTOR: dict[str, float] = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
    "very_active": 1.9,
}
GOAL_DELTA: dict[str, int] = {"lose": -450, "maintain": 0, "gain": 350}


def compute_targets(p: Profile) -> Targets:
    # Mifflin-St Jeor BMR (validated for ages 18–65; remains best available for 14–100)
    base = 10 * p.weight_kg + 6.25 * p.height_cm - 5 * p.age
    bmr  = base + 5 if p.sex == "m" else base - 161

    # TDEE via activity multiplier
    tdee = bmr * ACTIVITY_FACTOR[p.activity]

    # Age-based metabolic correction:
    # After 60 metabolic rate drops ~5% beyond what Mifflin predicts;
    # after 70 another ~5% (each decade adds ~5% reduction).
    if p.age >= 70:
        tdee *= 0.90
    elif p.age >= 60:
        tdee *= 0.95

    # Goal-based calorie delta — more conservative deficit for older adults
    # to preserve muscle mass and micronutrient intake
    if p.goal == "lose" and p.age >= 60:
        delta = -300   # cap deficit at 300 kcal for 60+
    else:
        delta = GOAL_DELTA[p.goal]

    cal = round(tdee + delta)

    # Hard calorie floor — never go below safe minimums
    cal_floor = 1500 if p.sex == "m" else 1200
    cal = max(cal, cal_floor)

    # Age-adjusted protein targets:
    # 65+ need more protein to counter sarcopenia (muscle loss with age)
    if p.age >= 65:
        protein_per_kg = {"lose": 2.2, "maintain": 2.0, "gain": 2.2}[p.goal]
    elif p.age >= 18:
        protein_per_kg = {"lose": 2.0, "maintain": 1.8, "gain": 2.0}[p.goal]
    else:
        # Teens (14–17): slightly lower — still growing, less intensive cutting needed
        protein_per_kg = {"lose": 1.8, "maintain": 1.6, "gain": 1.8}[p.goal]

    protein = round(p.weight_kg * protein_per_kg)
    fat     = round(cal * 0.30 / 9)
    carbs   = round((cal - protein * 4 - fat * 9) / 4)
    return Targets(calories=cal, protein_g=protein, carbs_g=max(carbs, 0), fat_g=fat)


# ── Gemini wrapper ─────────────────────────────────────────────────
async def generate_structured(parts: list, schema: type[BaseModel], temperature: float = 0.4) -> BaseModel:
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=parts,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=temperature,
            ),
        )
    except Exception as exc:
        # Pass the model error through so the frontend can show something useful
        raise HTTPException(status_code=502, detail=f"AI request failed: {exc}") from exc

    parsed = response.parsed
    if parsed is None:
        # Include raw text if present — helps with debugging
        raw = (response.text or "").strip()[:300]
        detail = "Invalid AI response" + (f" — '{raw}'" if raw else "")
        raise HTTPException(status_code=502, detail=detail)
    return parsed


def image_part(image: UploadFile, data: bytes) -> types.Part:
    return types.Part.from_bytes(data=data, mime_type=image.content_type or "image/jpeg")


# ── Routes ─────────────────────────────────────────────────────────
@app.get("/api/ping")
async def ping() -> dict:
    """Unauthenticated wake-up endpoint — frontend hits it on load to warm the
    Render dyno before the user tries to scan. Intentionally requires no auth."""
    return {"status": "ok"}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(ROOT / "index.html", media_type="text/html; charset=utf-8")


@app.get("/architectConfig.js")
async def architect_config() -> FileResponse:
    return FileResponse(ROOT / "architectConfig.js", media_type="application/javascript")


@app.get("/supabaseConfig.js")
async def supabase_config_js() -> Response:
    js = (
        f'const SUPABASE_URL="{SUPABASE_URL}";'
        f'const SUPABASE_ANON_KEY="{SUPABASE_ANON_KEY}";'
    )
    return Response(js, media_type="application/javascript")


# ── User state (cloud persistence) ──────────────────────────────────
@app.get("/api/user/state")
async def load_state(
    user: dict = Depends(get_current_user),
    authorization: str = Header(None),
) -> dict:
    token = (authorization or "").removeprefix("Bearer ").strip()
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"{SUPABASE_URL}/rest/v1/user_state",
            headers=_sb_headers(token),
            params={"select": "state"},
        )
    rows = r.json() if r.status_code == 200 else []
    return rows[0]["state"] if rows else {}


@app.post("/api/user/state")
async def save_state(
    request: Request,
    user: dict = Depends(get_current_user),
    authorization: str = Header(None),
) -> dict:
    token      = (authorization or "").removeprefix("Bearer ").strip()
    state_data = await request.json()
    async with httpx.AsyncClient() as c:
        await c.post(
            f"{SUPABASE_URL}/rest/v1/user_state",
            headers={**_sb_headers(token), "Prefer": "resolution=merge-duplicates"},
            json={"user_id": user["id"], "state": state_data},
        )
    return {"ok": True}


# ── Butler memory ───────────────────────────────────────────────────
class MemoryInput(BaseModel):
    memory_text: str
    category: Literal[
        "goal", "event", "preference", "health", "reminder",
        "reflection", "habit", "habit_candidate",
    ] = "event"
    expires_at: str | None = None  # ISO timestamp, optional


@app.get("/api/memory")
async def list_memory(
    _user: dict = Depends(rate_limited("memory")),
    authorization: str = Header(None),
) -> dict:
    """Return all non-expired memories for the current user, newest first."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    now_iso = datetime.now(timezone.utc).isoformat()
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"{SUPABASE_URL}/rest/v1/butler_memory",
            headers=_sb_headers(token),
            params={
                "select": "id,memory_text,category,created_at,expires_at",
                "or": f"(expires_at.is.null,expires_at.gt.{now_iso})",
                "order": "created_at.desc",
                "limit": "50",
            },
        )
    rows = r.json() if r.status_code == 200 else []
    return {"memories": rows}


@app.post("/api/memory/cleanup")
async def cleanup_memory(
    _user: dict = Depends(rate_limited("memory")),
    authorization: str = Header(None),
) -> dict:
    """Delete durable memories (no expires_at) older than 90 days. Called once
    per session on login. RLS guarantees only the caller's own rows are touched.
    The 90-day prune is category-agnostic — it matches any durable memory
    (goal/preference/health/reflection) with no expiry, so new categories are
    covered automatically."""
    from datetime import timedelta
    token  = (authorization or "").removeprefix("Bearer ").strip()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    async with httpx.AsyncClient() as c:
        await c.delete(
            f"{SUPABASE_URL}/rest/v1/butler_memory",
            headers=_sb_headers(token),
            params={"expires_at": "is.null", "created_at": f"lt.{cutoff}"},
        )
    return {"ok": True}


@app.post("/api/memory")
async def save_memory(
    body: MemoryInput,
    user: dict = Depends(rate_limited("memory")),
    authorization: str = Header(None),
) -> dict:
    text = body.memory_text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty memory")
    token = (authorization or "").removeprefix("Bearer ").strip()
    payload = {"user_id": user["id"], "memory_text": text[:500], "category": body.category}
    if body.expires_at:
        payload["expires_at"] = body.expires_at
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{SUPABASE_URL}/rest/v1/butler_memory",
            headers=_sb_headers(token),
            json=payload,
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail="Could not save memory")
    return {"ok": True}


# ── Proactive memory extraction (Laure / Chen / Marcus) ─────────────
# A lightweight second Gemini pass turns raw interactions into durable memory.
# The frontend fires these endpoints and never awaits them, so the user is never
# blocked. Scan-derived insights are staged as "habit_candidate"; they only
# become spoken "habit" memories once the daily pattern analysis confirms them
# against ≥5 days of data — a single pizza never becomes "user eats unhealthy".
class ExtractScanInput(BaseModel):
    items: list[MealItemForCheck] = []
    time_of_day: str = ""


class ExtractChatInput(BaseModel):
    user_message: str = ""
    butler_response: str = ""


_WORD_RE = re.compile(r"[a-z0-9]+")
_UUID_RE = re.compile(r"^[0-9a-fA-F-]{16,40}$")
_MEM_SELECT = "id,memory_text,category,created_at,expires_at"


def _words(s: str) -> set[str]:
    return set(_WORD_RE.findall((s or "").lower()))


def _word_overlap(a: str, b: str) -> float:
    """Fraction of the smaller bag-of-words shared by both strings (0–1).
    Cheap semantic dedup: >0.70 ⇒ treat as the same memory."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def _normalize_memories(raw) -> list[dict]:
    """Accept the structured shape [{memory_text, category}] (current frontend)
    OR the legacy flat list[str] (older cached builds); return clean dicts."""
    out: list[dict] = []
    for m in raw or []:
        if isinstance(m, dict):
            txt = str(m.get("memory_text", "")).strip()
            cat = str(m.get("category", "")).strip() or "general"
        elif isinstance(m, str):
            txt, cat = m.strip(), "general"
        else:
            continue
        if txt:
            out.append({"memory_text": txt, "category": cat})
    return out


def _parse_json_block(text: str):
    """Best-effort JSON extraction from a model reply (tolerates ``` fences)."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"(\[.*\]|\{.*\})", t, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                return None
    return None


async def _gemini_text(prompt: str, temperature: float = 0.2) -> str:
    """Plain-text Gemini call, run off the event loop. Returns '' on any error —
    memory extraction must never surface an error to the user."""
    def _call() -> str:
        r = client.models.generate_content(
            model=MODEL,
            contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
            config=types.GenerateContentConfig(temperature=temperature),
        )
        return (r.text or "").strip()
    try:
        return await asyncio.to_thread(_call)
    except Exception:
        return ""


async def _sb_fetch_memories(c, token, category: str | None = None,
                             only_active: bool = True) -> list[dict]:
    params = {"select": _MEM_SELECT, "limit": "200"}
    if category:
        params["category"] = f"eq.{category}"
    if only_active:
        params["or"] = f"(expires_at.is.null,expires_at.gt.{datetime.now(timezone.utc).isoformat()})"
    r = await c.get(f"{SUPABASE_URL}/rest/v1/butler_memory",
                    headers=_sb_headers(token), params=params)
    return r.json() if r.status_code == 200 else []


async def _sb_insert_memory(c, token, user_id, text, category, expires_at=None):
    payload = {"user_id": user_id, "memory_text": text[:500], "category": category}
    if expires_at:
        payload["expires_at"] = expires_at
    await c.post(f"{SUPABASE_URL}/rest/v1/butler_memory",
                 headers=_sb_headers(token), json=payload)


async def _sb_update_memory(c, token, mem_id, text, expires_at=None):
    # created_at is refreshed so the 90-day prune treats an updated memory as recent.
    payload = {
        "memory_text": text[:500],
        "expires_at": expires_at,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await c.patch(f"{SUPABASE_URL}/rest/v1/butler_memory",
                  headers=_sb_headers(token),
                  params={"id": f"eq.{mem_id}"}, json=payload)


async def _sb_delete_memory(c, token, mem_id):
    await c.delete(f"{SUPABASE_URL}/rest/v1/butler_memory",
                   headers=_sb_headers(token), params={"id": f"eq.{mem_id}"})


async def _store_with_dedup(c, token, user_id, text, category, existing, expires_at=None) -> bool:
    """Insert a memory, or UPDATE an existing same-category one when >70% of the
    smaller word-bag overlaps. `existing` is mutated so near-duplicates inside a
    single batch also collapse. Returns True if a new row was inserted."""
    text = (text or "").strip()
    if not text:
        return False
    for m in existing:
        if m.get("category") == category and _word_overlap(text, m.get("memory_text", "")) > 0.70:
            if m.get("id"):
                await _sb_update_memory(c, token, m["id"], text, expires_at=expires_at)
            m["memory_text"] = text
            return False
    await _sb_insert_memory(c, token, user_id, text, category, expires_at)
    existing.append({"id": None, "memory_text": text, "category": category})
    return True


EXTRACT_SCAN_PROMPT = """You are a memory extraction system for a nutrition app. Given this food scan result,
extract 0–2 memory strings worth storing about the user's eating habits or preferences.
Return a JSON array of strings, or an empty array [] if nothing notable.

Rules:
- Only extract something that would be genuinely useful to know for future conversations
- Focus on preferences, patterns, and notable choices — not just "user ate food"
- Be neutral and factual — no judgments
- Keep each string under 80 characters
- Examples of good memories:
  "User often eats [food] for lunch"
  "User prefers high-protein meals"
  "User logged a large pasta dish — possible comfort food pattern"
- Examples of bad memories (do NOT generate):
  "User ate today" (not useful)
  "User is unhealthy" (judgment, over-generalization)
  "User ate pizza" (single data point, not a pattern)

Food scan data:
__SCAN_ITEMS__
Time of day: __TIME_OF_DAY__"""


@app.post("/api/memory/extract-scan")
async def extract_scan_memory(
    body: ExtractScanInput,
    user: dict = Depends(rate_limited("memory")),
    authorization: str = Header(None),
) -> dict:
    """Stage 0–2 'habit_candidate' memories from a just-logged meal. Candidates
    expire in 30 days and are NEVER injected into the Butler — only the daily
    pattern analysis can promote them to confirmed habits."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not body.items:
        return {"stored": 0}
    scan_items = "; ".join(
        f"{i.name} ({i.portion or 'n/a'}, {i.calories} kcal, "
        f"{i.protein_g:.0f}g protein, {i.carbs_g:.0f}g carbs, {i.fat_g:.0f}g fat)"
        for i in body.items
    )
    prompt = (EXTRACT_SCAN_PROMPT
              .replace("__SCAN_ITEMS__", scan_items)
              .replace("__TIME_OF_DAY__", body.time_of_day.strip() or "unknown"))
    parsed = _parse_json_block(await _gemini_text(prompt, 0.2))
    if not isinstance(parsed, list):
        return {"stored": 0}
    strings = [s.strip()[:80] for s in parsed if isinstance(s, str) and s.strip()][:2]
    if not strings:
        return {"stored": 0}
    expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    async with httpx.AsyncClient() as c:
        existing = await _sb_fetch_memories(c, token, category="habit_candidate")
        for s in strings:
            await _store_with_dedup(c, token, user["id"], s, "habit_candidate", existing, expires_at=expires)
    return {"stored": len(strings)}


EXTRACT_CHAT_PROMPT = """You are a memory extraction system. Given this conversation snippet between a user and
their nutrition butler, extract 0–2 memory strings worth storing long-term.
Return a JSON array of strings, or [] if nothing notable.

Focus on:
- Goals the user mentions ("I want to lose 5kg by summer")
- Health conditions or restrictions mentioned ("I'm lactose intolerant")
- Personal preferences ("I hate eating breakfast")
- Emotional context relevant to eating ("I stress-eat at night")
- Upcoming events that affect eating ("I have a competition next month")

Rules:
- Single-use phrases like "today I..." → do NOT store
- Only store things that would still be useful in 2 weeks
- Keep strings under 100 characters
- No judgments, no assumptions

Conversation:
User: __USER_MESSAGE__
Butler: __BUTLER_RESPONSE__"""


def _classify_chat_memory(s: str) -> tuple[str, str | None]:
    """Map an extracted chat memory to (category, expires_at). Conservative —
    defaults to a durable 'preference'. Mirrors the frontend detectMemory()."""
    low = s.lower()
    if re.search(r"\b(allerg|intoleran|lactose|gluten|celiac|coeliac|diabet|"
                 r"hypertension|blood pressure|condition|ibs|reflux)\b", low):
        return "health", None
    if re.search(r"\b(tomorrow|tonight|next week|next month|competition|wedding|"
                 r"holiday|vacation|trip|race|marathon|event|on monday|on tuesday|"
                 r"on wednesday|on thursday|on friday|on saturday|on sunday)\b", low):
        # Upcoming event — self-cleans after ~60 days if never refreshed.
        return "event", (datetime.now(timezone.utc) + timedelta(days=60)).isoformat()
    if re.search(r"\b(goal|want to|trying to|aim|aiming|plan to|lose|gain|reach|"
                 r"target|cut|bulk|by summer|by christmas|kg|pounds|lbs)\b", low):
        return "goal", None
    return "preference", None


@app.post("/api/memory/extract-chat")
async def extract_chat_memory(
    body: ExtractChatInput,
    user: dict = Depends(rate_limited("memory")),
    authorization: str = Header(None),
) -> dict:
    """Store 0–2 durable memories straight from the user's own words. These come
    from the user directly, so they skip staging and land as a real category."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not body.user_message.strip():
        return {"stored": 0}
    prompt = (EXTRACT_CHAT_PROMPT
              .replace("__USER_MESSAGE__", body.user_message.strip()[:1000])
              .replace("__BUTLER_RESPONSE__", body.butler_response.strip()[:1000]))
    parsed = _parse_json_block(await _gemini_text(prompt, 0.2))
    if not isinstance(parsed, list):
        return {"stored": 0}
    strings = [s.strip()[:100] for s in parsed if isinstance(s, str) and s.strip()][:2]
    if not strings:
        return {"stored": 0}
    async with httpx.AsyncClient() as c:
        existing = [m for m in await _sb_fetch_memories(c, token)
                    if m.get("category") != "habit_candidate"]
        for s in strings:
            cat, exp = _classify_chat_memory(s)
            await _store_with_dedup(c, token, user["id"], s, cat, existing, expires_at=exp)
    return {"stored": len(strings)}


@app.delete("/api/memory/{memory_id}")
async def delete_memory(
    memory_id: str,
    _user: dict = Depends(rate_limited("memory")),
    authorization: str = Header(None),
) -> dict:
    """Delete a single memory. RLS guarantees only the caller's own row is hit."""
    if not _UUID_RE.match(memory_id):
        raise HTTPException(status_code=400, detail="Invalid memory id")
    token = (authorization or "").removeprefix("Bearer ").strip()
    async with httpx.AsyncClient() as c:
        r = await c.delete(
            f"{SUPABASE_URL}/rest/v1/butler_memory",
            headers=_sb_headers(token),
            params={"id": f"eq.{memory_id}"},
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail="Could not delete memory")
    return {"ok": True}


ANALYZE_PATTERNS_PROMPT = """You are a pattern recognition system for a nutrition app.
Analyze 30 days of eating data and identify real behavioral patterns.

Rules for promoting a candidate to a real habit:
- Must appear in ≥60% of relevant days (e.g. skips breakfast 18+ of 30 days)
- Must have at least 5 data points to draw from
- Must be genuinely useful for a nutrition coach to know
- Must NOT be a value judgment ("user is unhealthy")
- Must be stated as an observation, not a conclusion

Return JSON:
{
  "promote": ["string1", "string2"],
  "reject": ["string1"],
  "new_patterns": ["string"]
}

Data:
__DAILY_LOGS_SUMMARY__
Existing candidates:
__HABIT_CANDIDATES__"""


@app.post("/api/memory/analyze-patterns")
async def analyze_patterns(
    user: dict = Depends(rate_limited("memory")),
    authorization: str = Header(None),
) -> dict:
    """Promote candidates that survive a 60%/5-day test into confirmed habits,
    delete the ones that don't, and stage genuinely new patterns. A hard,
    code-enforced floor (≥5 logged days) gates every habit write regardless of
    what the model returns — Chen's guarantee against over-generalization."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    async with httpx.AsyncClient() as c:
        rlog = await c.get(
            f"{SUPABASE_URL}/rest/v1/daily_logs",
            headers=_sb_headers(token),
            params={"select": "date,log_data", "order": "date.desc", "limit": "30"},
        )
        logs = rlog.json() if rlog.status_code == 200 else []
        candidates = await _sb_fetch_memories(c, token, category="habit_candidate")

        n_days = len(logs)
        if n_days == 0:
            return {"promoted": 0, "rejected": 0, "days_analyzed": 0}

        summary_lines = []
        for row in logs:
            entries = row.get("log_data") or []
            names = [str(e.get("name", "?")) for e in entries]
            kcal = sum(int(e.get("calories", 0) or 0) for e in entries)
            summary_lines.append(
                f"{row.get('date')}: {len(entries)} meals, {kcal} kcal — "
                + (", ".join(names[:8]) if names else "nothing logged")
            )
        summary = "\n".join(summary_lines)
        cand_text = "\n".join(f"- {m['memory_text']}" for m in candidates) or "(none)"

        prompt = (ANALYZE_PATTERNS_PROMPT
                  .replace("__DAILY_LOGS_SUMMARY__", summary)
                  .replace("__HABIT_CANDIDATES__", cand_text))
        parsed = _parse_json_block(await _gemini_text(prompt, 0.2))
        if not isinstance(parsed, dict):
            return {"promoted": 0, "rejected": 0, "days_analyzed": n_days}

        def _clean(key):
            return [s.strip() for s in (parsed.get(key) or [])
                    if isinstance(s, str) and s.strip()]
        promote, reject, new_pat = _clean("promote"), _clean("reject"), _clean("new_patterns")

        # Deterministic 5-data-point floor (Chen): without ≥5 logged days, no
        # candidate can become a spoken habit no matter what the model claims.
        allow_habit = n_days >= 5
        promoted = rejected = 0

        if allow_habit:
            existing_habits = await _sb_fetch_memories(c, token, category="habit")
            for s in promote + new_pat:
                await _store_with_dedup(c, token, user["id"], s[:120], "habit",
                                        existing_habits, expires_at=None)
                promoted += 1
            # Remove staging rows for anything just promoted.
            for s in promote:
                for m in candidates:
                    if m.get("id") and _word_overlap(s, m.get("memory_text", "")) > 0.70:
                        await _sb_delete_memory(c, token, m["id"])
        else:
            exp = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
            for s in new_pat:
                await _store_with_dedup(c, token, user["id"], s[:80],
                                        "habit_candidate", candidates, expires_at=exp)

        for s in reject:
            for m in candidates:
                if m.get("id") and _word_overlap(s, m.get("memory_text", "")) > 0.70:
                    await _sb_delete_memory(c, token, m["id"])
                    rejected += 1

    return {"promoted": promoted, "rejected": rejected, "days_analyzed": n_days}


# ── Daily food logs (per-day rows, multi-device safe) ───────────────
class DayLogInput(BaseModel):
    date: str  # local calendar date, YYYY-MM-DD
    log_data: list = []


@app.get("/api/logs/recent")
async def load_recent_logs(
    _user: dict = Depends(get_current_user),
    authorization: str = Header(None),
) -> dict:
    """Return the user's last 7 days of food logs — not the entire history."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"{SUPABASE_URL}/rest/v1/daily_logs",
            headers=_sb_headers(token),
            params={
                "select": "date,log_data,updated_at",
                "order": "date.desc",
                "limit": "7",
            },
        )
    rows = r.json() if r.status_code == 200 else []
    return {"days": {row["date"]: row["log_data"] for row in rows}}


@app.post("/api/logs/day")
async def save_day_log(
    body: DayLogInput,
    user: dict = Depends(get_current_user),
    authorization: str = Header(None),
) -> dict:
    """Upsert a single day's log. updated_at is refreshed → most recent write wins."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{SUPABASE_URL}/rest/v1/daily_logs",
            headers={**_sb_headers(token), "Prefer": "resolution=merge-duplicates"},
            json={
                "user_id": user["id"],
                "date": body.date,
                "log_data": body.log_data,
                # explicit timestamp — column default only fires on INSERT, not on
                # the UPDATE half of an upsert, so we refresh it ourselves every write
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail="Could not save day log")
    return {"ok": True}


# ── Weight log (Rafael / Yuki) ──────────────────────────────────────
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class WeightEntry(BaseModel):
    date: str  # YYYY-MM-DD
    weight_kg: float = Field(ge=20, le=300)


def _weight_context_block(recent_weights, goal_weight=None) -> str:
    """Shared Butler injection for weight context — used by /api/chat and the
    butler/check health prompt so the two never drift. Returns '' when there's
    nothing meaningful to say."""
    if not recent_weights:
        return ""
    if len(recent_weights) == 1:
        return f"\n\nCurrent weight: {recent_weights[0]['weight_kg']}kg."
    weights = sorted(recent_weights, key=lambda x: x["date"])
    latest = weights[-1]["weight_kg"]
    oldest = weights[0]["weight_kg"]
    change = round(latest - oldest, 1)
    span = len(weights)
    direction = "down" if change < 0 else "up" if change > 0 else "stable"
    block = (f"\n\nWeight tracking ({span} entries): currently {latest}kg, "
             f"{abs(change)}kg {direction} over this period.")
    if goal_weight:
        to_go = round(abs(latest - goal_weight), 1)
        block += f" Goal: {goal_weight}kg ({to_go}kg remaining)."
    return block


@app.get("/api/weight")
async def list_weight(
    _user: dict = Depends(rate_limited("memory")),
    authorization: str = Header(None),
) -> dict:
    """Last 90 days of weight entries, newest first. RLS scopes to the caller."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    since = (datetime.now(timezone.utc).date() - timedelta(days=90)).isoformat()
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"{SUPABASE_URL}/rest/v1/weight_logs",
            headers=_sb_headers(token),
            params={"select": "id,date,weight_kg", "date": f"gte.{since}", "order": "date.desc"},
        )
    rows = r.json() if r.status_code == 200 else []
    for row in rows:  # numeric(5,2) returns as string from PostgREST — coerce
        try:
            row["weight_kg"] = float(row["weight_kg"])
        except (TypeError, ValueError):
            pass
    return {"entries": rows}


@app.post("/api/weight")
async def save_weight(
    body: WeightEntry,
    user: dict = Depends(rate_limited("memory")),
    authorization: str = Header(None),
) -> dict:
    """Upsert one entry per (user, date). A retry or same-day re-log updates in
    place — the UNIQUE(user_id, date) constraint + merge-duplicates make it
    idempotent, never a duplicate row."""
    if not _DATE_RE.match(body.date):
        raise HTTPException(status_code=400, detail="Invalid date format")
    token = (authorization or "").removeprefix("Bearer ").strip()
    weight = round(body.weight_kg, 2)
    async with httpx.AsyncClient() as c:
        r = await c.post(
            f"{SUPABASE_URL}/rest/v1/weight_logs",
            headers={**_sb_headers(token), "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "user_id,date"},
            json={"user_id": user["id"], "date": body.date, "weight_kg": weight},
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail="Could not save weight")
    return {"ok": True, "entry": {"date": body.date, "weight_kg": weight}}


@app.delete("/api/weight/{date}")
async def delete_weight(
    date: str,
    _user: dict = Depends(rate_limited("memory")),
    authorization: str = Header(None),
) -> dict:
    """Delete the entry for a date. 404 when there's nothing to delete."""
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=400, detail="Invalid date format")
    token = (authorization or "").removeprefix("Bearer ").strip()
    async with httpx.AsyncClient() as c:
        r = await c.delete(
            f"{SUPABASE_URL}/rest/v1/weight_logs",
            headers={**_sb_headers(token), "Prefer": "return=representation"},
            params={"date": f"eq.{date}", "select": "id"},
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail="Could not delete weight")
    if not (r.json() if r.status_code < 300 else []):
        raise HTTPException(status_code=404, detail="No entry for that date")
    return {"ok": True}


# ── Food scan prompt (Maya / DeepMind) ──────────────────────────────
# Tuned for portion accuracy and zero hallucinated qualifiers.
#
# Internal test cases the prompt is designed to pass (verify manually when editing):
#   1. Bowl of red liquid with visible lentils → "Red lentil soup", NOT a juice/drink
#      (container = bowl → dish, never a beverage; red color alone proves nothing).
#   2. Clear glass of red liquid → "Cherry juice" (container = glass → beverage).
#   3. Chicken breast filling half a 26 cm plate → ~150–200 g, ~250–330 kcal, high protein,
#      confidence high; portion stated as grams via plate-fraction reasoning.
#   4. Blurry/partially-hidden mixed plate → confidence "low" AND calories_range like
#      "450–700"; still returns single best-estimate numbers for tracking.
#   5. Unidentifiable dish → name "Unknown dish", confidence "low", conservative numbers.
SCAN_PROMPT = (
    "You are a nutrition expert analyzing a photo of a meal. "
    "Identify each component separately.\n\n"
    "CONTAINER DETERMINES TYPE — use shape, never color:\n"
    "- A bowl or deep plate = a soup or dish, NEVER a drink.\n"
    "- A glass, cup, or mug = a beverage.\n"
    "- Red/colored liquid in a bowl is soup; the same color in a glass is juice. "
    "Color alone never decides flavor or type. Use shape, texture, and context.\n\n"
    "PORTION SIZE — estimate by visual reference, not guesswork:\n"
    "- A standard dinner plate is 26 cm across; judge each food as a fraction of it.\n"
    "- A fist ≈ 150–200 ml volume; a cupped hand ≈ 100 g of grains/pasta.\n"
    "- A palm (no fingers) ≈ 85 g of cooked meat or fish; a thumb ≈ 30 g of cheese or fat.\n"
    "- Typical restaurant meat portion 150–200 g; pasta 200–250 g.\n"
    "State the portion in grams or a clear unit (e.g. '180 g', '1 cup', '2 pieces'). "
    "Be conservative — prefer 10% under rather than over.\n\n"
    "CONFIDENCE PER ITEM:\n"
    "- Set each item's 'confidence' to high/medium/low for ITS macro estimate.\n"
    "- 'high' only when the food and portion are both clearly identifiable.\n"
    "- When an item's confidence is 'low', ALSO fill 'calories_range' with a realistic "
    "range string like '320-480' (low-high). For medium/high, leave 'calories_range' empty.\n"
    "- Always return single best-estimate numbers for calories and all macros, even when "
    "confidence is low — the app needs a number to track.\n"
    "Also set the overall scan 'confidence' to the lowest item confidence.\n\n"
    "FOOD NAMES: English, maximum 3 words, concrete and direct. "
    "FORBIDDEN anywhere in output: 'probably', 'likely', 'possibly', 'maybe', 'perhaps', "
    "'flavor', 'wahrscheinlich', 'vermutlich', 'vielleicht'. "
    "Good examples: 'Beet soup', 'Red lentil soup', 'Grilled chicken', 'Cherry juice'. "
    "If a dish is genuinely unidentifiable, name it exactly 'Unknown dish'."
)


@app.post("/api/scan/food", response_model=FoodScan)
async def scan_food(image: UploadFile = File(...), _user: dict = Depends(rate_limited("scan"))) -> FoodScan:
    data = await image.read()
    # Temperature 0.2 → maximum precision / minimal creative drift for estimates.
    return await generate_structured([SCAN_PROMPT, image_part(image, data)], FoodScan, temperature=0.2)


class BarcodeRequest(BaseModel):
    barcode: str


@app.post("/api/scan/barcode", response_model=FoodScan)
async def scan_barcode(req: BarcodeRequest, _user: dict = Depends(rate_limited("scan"))) -> FoodScan:
    barcode = req.barcode.strip()
    url = f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json"
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            r = await c.get(url, headers={"User-Agent": "Nouri-App/1.0"})
            data = r.json()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Open Food Facts unreachable: {exc}") from exc

    if data.get("status") != 1 or "product" not in data:
        raise HTTPException(status_code=404, detail="Product not found in database.")

    p  = data["product"]
    nu = p.get("nutriments", {})

    name     = p.get("product_name") or p.get("product_name_en") or "Unknown Product"
    kcal100  = float(nu.get("energy-kcal_100g") or nu.get("energy_100g", 0) or 0)
    prot100  = float(nu.get("proteins_100g", 0) or 0)
    carb100  = float(nu.get("carbohydrates_100g", 0) or 0)
    fat100   = float(nu.get("fat_100g", 0) or 0)

    # Default portion 100 g — frontend lets user adjust
    portion = "100 g"
    item = FoodItem(
        name=name, portion=portion,
        calories=round(kcal100),
        protein_g=round(prot100, 1),
        carbs_g=round(carb100, 1),
        fat_g=round(fat100, 1),
    )
    return FoodScan(items=[item], confidence="high")


@app.post("/api/scan/fridge", response_model=FridgeScan)
async def scan_fridge(image: UploadFile = File(...), _user: dict = Depends(rate_limited("scan"))) -> FridgeScan:
    data = await image.read()
    prompt = (
        "Erkenne nur die Lebensmittel und Zutaten, die im Bild EINDEUTIG sichtbar sind. "
        "Sei sehr konservativ: lieber etwas weglassen als raten. "
        "WICHTIG bei Verpackungen: Wenn du eine Konservendose, Tüte, geschlossene Verpackung "
        "oder ein nicht eindeutig erkennbares Etikett siehst, schreibe nur was du WIRKLICH "
        "sehen kannst (z.B. 'Konservendose, Inhalt unklar') — erfinde NIEMALS den vermuteten "
        "Inhalt. Nur klar erkennbare Lebensmittel benennen. "
        "Schätze Mengen so genau wie möglich anhand der sichtbaren Anzahl/Größe "
        "(z.B. '4 Stück', '1 Bund', '500 g'). Kategorisiere jedes Item nach dem Schema. "
        "Return all food names, categories, and quantities in English."
    )
    return await generate_structured([prompt, image_part(image, data)], FridgeScan)


@app.post("/api/profile/targets", response_model=Targets)
async def profile_targets(profile: Profile, _user: dict = Depends(get_current_user)) -> Targets:
    return compute_targets(profile)


_CUISINE_LABELS: dict[str, str] = {
    "italian": "Italienisch", "mediterranean": "Mediterran", "french": "Französisch",
    "spanish": "Spanisch", "greek": "Griechisch", "nordic": "Nordisch",
    "german": "Deutsch", "american": "Amerikanisch", "mexican": "Mexikanisch",
    "peruvian": "Peruanisch", "middle_eastern": "Naher Osten", "levantine": "Levantinisch",
    "indian": "Indisch", "asian": "Asiatisch", "japanese": "Japanisch",
    "korean": "Koreanisch", "thai": "Thailändisch", "vietnamese": "Vietnamesisch",
    "chinese": "Chinesisch",
}
_GOAL_LABELS: dict[str, str] = {
    "high_protein": "proteinreich und sättigend",
    "quick":        "schnell zuzubereiten (unter 20 Minuten)",
    "healthy":      "ausgewogen und frisch",
    "light":        "leicht und bekömmlich",
}


@app.post("/api/scan/refine", response_model=FoodScan)
async def refine_scan(req: RefineRequest, _user: dict = Depends(rate_limited("scan"))) -> FoodScan:
    if not req.description.strip():
        raise HTTPException(400, "No description provided")
    orig = ", ".join(f"{i.name} ({i.portion}, {i.calories} kcal)" for i in req.original_items) or "nothing detected"
    prompt = (
        f"The AI detected this meal: {orig}.\n\n"
        f"The user corrects/adds: \"{req.description}\"\n\n"
        "Based on this correction, create a complete, revised component list with realistic nutritional values. "
        "Set 'confidence' to 'high' when the description is clear. "
        "Return all food names, descriptions, and portion strings in English."
    )
    return await generate_structured([prompt], FoodScan)


@app.post("/api/recipe", response_model=Recipe)
async def generate_recipe(req: RecipeRequest, _user: dict = Depends(get_current_user)) -> Recipe:
    inspiration_mode = not req.ingredients

    if inspiration_mode and not req.taste:
        raise HTTPException(400, "No ingredients or taste preferences provided")

    constraints: list[str] = []
    if req.remaining_calories is not None:
        constraints.append(
            f"Ziel: ~{req.remaining_calories} kcal pro Portion (±100 kcal Toleranz)."
        )
    if req.targets:
        constraints.append(f"Hoher Proteinanteil bevorzugt (Tagesziel {req.targets.protein_g} g).")
    if req.restrictions:
        avoid = req.restrictions.allergies + req.restrictions.noGos
        if avoid:
            constraints.append(
                f"ABSOLUTES VERBOT — niemals verwenden: {', '.join(avoid)}. "
                "Das gilt auch für versteckte Spuren. Keine Ausnahmen."
            )
    if req.taste:
        if req.taste.cuisines:
            labels = [_CUISINE_LABELS.get(c, c) for c in req.taste.cuisines[:4]]
            constraints.append(f"Bevorzugter Küchenstil: {', '.join(labels)}.")
        if req.taste.goals:
            goal_texts = [_GOAL_LABELS[g] for g in req.taste.goals if g in _GOAL_LABELS]
            if goal_texts:
                constraints.append(f"Fokus: {', '.join(goal_texts)}.")

    if inspiration_mode:
        ingredient_block = (
            "Keine spezifischen Zutaten vorgegeben — kreiere ein freies, inspiriertes Rezept "
            "passend zum Geschmacksprofil. Verwende alltagstaugliche, frische Zutaten."
        )
    else:
        ingredient_block = (
            f"Verfügbare Zutaten mit Mengen: {', '.join(req.ingredients)}.\n\n"
            "WICHTIG — Mengen-Disziplin:\n"
            "- Halte dich strikt an die verfügbaren Mengen. Du darfst weniger verwenden, NIEMALS mehr.\n"
            "- Wenn jemand z.B. '2 Eier' hat, kann das Rezept 1 oder 2 Eier verlangen — niemals 5 oder 20.\n"
            "- Reichen die Mengen nur für eine kleine Portion, schlage entsprechend skaliert vor "
            "(z.B. 'Frühstück für 1 Person') statt eine größere Portion zu erfinden.\n"
            "- Falls eine Zutat als 'Konservendose, Inhalt unklar' o.ä. gelistet ist, ignoriere sie.\n"
            "- Wähle eine sinnvolle Auswahl der Zutaten — nicht alles muss verwendet werden, "
            "aber rechne nichts hinzu, was nicht in der Liste steht (außer Grundwürze: Salz, Pfeffer, Öl)."
        )

    prompt = (
        f"{ingredient_block}\n\n"
        f"{chr(10).join(constraints)}\n\n"
        "Style: refined, high-quality, practical. Clear steps, precise metric measurements. No filler. "
        "Return the recipe title, summary, all ingredients, and all steps in English."
    )
    return await generate_structured([prompt], Recipe)


@app.post("/api/chat")
async def chat(req: ChatRequest, _user: dict = Depends(rate_limited("butler"))) -> dict:
    mode = req.mode if req.mode in BUTLER_PROMPTS else "ELITE_BUTLER"
    system = BUTLER_PROMPTS[mode]

    # Erstnachricht = nur 1 Message im Array (noch keine Antwort gegeben)
    is_first = len(req.messages) == 1

    if req.context:
        c = req.context

        # ── MEMORY FIRST ── structured, categorized injection (Marcus). The
        # Butler always has this context and must never claim it lacks access.
        # habit_candidate entries are staging only — they are NEVER injected.
        categorized: dict[str, list[str]] = {}
        for m in _normalize_memories(c.memories):
            if m["category"] == "habit_candidate":
                continue
            categorized.setdefault(m["category"], []).append(m["memory_text"])
        category_labels = {
            "goal":       "Their goals",
            "habit":      "Their confirmed habits (based on 30+ days of data)",
            "preference": "Their preferences",
            "health":     "Health context",
            "event":      "Upcoming events",
            "reminder":   "Reminders they set",
            "reflection": "Their reflections",
        }
        memory_block = ""
        for cat, label in category_labels.items():
            if cat in categorized:
                memory_block += f"{label}: {'; '.join(categorized[cat])}\n"
        if memory_block:
            system += (
                "\n\n[WHAT YOU KNOW ABOUT THIS USER]\n" + memory_block +
                "\nUse this context naturally. Never recite it back verbatim. "
                "Never mention that you 'remember' things — just use it."
            )
        system += (
            "\n\nYou ALWAYS have the user's current data and memory below. "
            "NEVER say you don't have access to their data, history, or numbers — you do."
        )

        # Identity + addressing
        if c.user_name:
            if is_first:
                system += (
                    f"\n\nUser's name: {c.user_name}. "
                    "Greet them by first name once at the start of this conversation."
                )
            else:
                system += (
                    f"\n\nUser's name: {c.user_name}. "
                    "Conversation is ongoing — respond WITHOUT a greeting or address, go directly to content."
                )

        # Today's date + day status (injected every request)
        if c.today_date:
            system += f"\n\nToday's date: {c.today_date}."
        parts = [f"{c.calories_today:.0f} kcal consumed"]
        if c.calories_target:
            remaining = c.calories_target - c.calories_today
            parts.append(f"target {c.calories_target} kcal ({remaining:.0f} remaining)")
        if c.protein_target:
            parts.append(f"protein {c.protein_today:.0f}/{c.protein_target}g")
        parts.append(f"carbs {c.carbs_today:.0f}g, fat {c.fat_today:.0f}g")
        system += f"\n\nCurrent day status: {', '.join(parts)}."

        # Weight context — factual trend the Butler uses per the tone rules above.
        system += _weight_context_block(c.recent_weights, c.goal_weight)
    elif not is_first:
        system += "\n\nConversation is ongoing — respond WITHOUT a greeting or address, go directly to content."

    contents = [
        types.Content(
            role="user" if msg.role == "user" else "model",
            parts=[types.Part(text=msg.content)],
        )
        for msg in req.messages
    ]

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.7,
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Chat error: {exc}") from exc

    if not response.text:
        raise HTTPException(status_code=502, detail="No chat response received")

    return {"reply": response.text}


_BUTLER_SEVERITY: dict[str, str] = {
    "calories_over":  "critical",
    "calories_90":    "warning",
    "protein_low":    "warning",
    "carbs_routing":  "info",
    "health_context": "info",
}

_TRIGGER_DESC: dict[str, str] = {
    "calories_over":  "Calorie budget exceeded: {cal:.0f} of {target} kcal ({over:.0f} kcal over target).",
    "calories_90":    "90% of calorie budget consumed: {cal:.0f} of {target} kcal — {rem:.0f} kcal remaining.",
    "protein_low":    "Protein gap: only {prot:.0f} of {ptgt}g protein consumed, but {cpct:.0f}% of calories used.",
    "carbs_routing":  "Carb alert: {carb:.0f} of {ctgt}g carbs consumed before 2 PM.",
}

# Deterministic, mode-flavoured English messages. These cover macro triggers that
# fire on every meal / interval — no LLM call needed, which keeps per-user cost
# flat regardless of how engaged they are. {name} is "" when no name is known.
_BUTLER_MESSAGES: dict[str, dict[str, str]] = {
    "calories_over": {
        "ELITE_BUTLER":      "{name}You're {over:.0f} kcal over your {target} kcal target. Keep any remaining intake to lean protein and vegetables — the balance is still recoverable.",
        "PERFORMANCE_COACH": "{name}+{over:.0f} kcal over target. That costs results. Next meal: lean protein only, no sugar.",
        "STRATEGIC_BUDDY":   "{name}You're {over:.0f} kcal past your goal — no drama. Keep tonight light: salad and protein and you're fine.",
    },
    "calories_90": {
        "ELITE_BUTLER":      "{name}90% of your budget is used — {rem:.0f} kcal remain. A high-protein, lower-carb meal is the precise close to the day.",
        "PERFORMANCE_COACH": "{name}90% gone, {rem:.0f} kcal left. Spend it on protein and vegetables — nothing empty.",
        "STRATEGIC_BUDDY":   "{name}You're at 90% — {rem:.0f} kcal to play with. Something light and protein-heavy tonight keeps you on track.",
    },
    "protein_low": {
        "ELITE_BUTLER":      "{name}Protein is at {prot:.0f}g of {ptgt}g while {cpct:.0f}% of calories are spent. Prioritise a protein-dense option next.",
        "PERFORMANCE_COACH": "{name}Only {prot:.0f}g protein and {cpct:.0f}% of calories already gone. Fix it — protein-first on the next meal.",
        "STRATEGIC_BUDDY":   "{name}Protein's lagging — {prot:.0f}g of {ptgt}g. Worth making the next thing protein-forward.",
    },
    "carbs_routing": {
        "ELITE_BUTLER":      "{name}{carb:.0f}g of {ctgt}g carbs are used before 2 PM. I'd route the evening high-protein and low-carb to keep the day balanced.",
        "PERFORMANCE_COACH": "{name}Carbs at {carb:.0f}g already and it's not even afternoon. Tonight: protein only.",
        "STRATEGIC_BUDDY":   "{name}{carb:.0f}g carbs in before 2 PM — easy fix, just lean protein tonight.",
    },
}


def build_butler_message(trigger: str, mode: str, name: str | None, vals: dict) -> str:
    mode = mode if mode in _BUTLER_MESSAGES.get(trigger, {}) else "ELITE_BUTLER"
    name_prefix = f"{name}, " if name else ""
    return _BUTLER_MESSAGES[trigger][mode].format(name=name_prefix, **vals)


@app.post("/api/butler/check")
async def butler_check(req: ButlerCheckRequest, _user: dict = Depends(rate_limited("butler"))) -> dict:
    fired = set(req.fired_today)
    cal, ptgt, ctgt = req.calories_today, req.protein_target, req.carbs_target
    target = req.calories_target

    # Trigger evaluation — priority order, each fires at most once per day
    trigger: str | None = None
    if "calories_over" not in fired and cal >= target:
        trigger = "calories_over"
    elif "calories_90" not in fired and cal >= target * 0.90:
        trigger = "calories_90"
    elif "protein_low" not in fired and cal >= target * 0.60 and req.protein_today < ptgt * 0.30:
        trigger = "protein_low"
    elif "carbs_routing" not in fired and req.hour < 14 and req.carbs_today >= ctgt * 0.80:
        trigger = "carbs_routing"

    if not trigger and req.health_context and req.last_meal:
        # Health context check — only runs when no macro trigger fired
        hc = req.health_context
        conditions: list[str] = []
        if hc.diabetes_type:
            conditions.append(f"Diabetes {hc.diabetes_type.replace('type', 'Type ')}")
        if hc.lactose_intolerant:
            conditions.append("Lactose intolerance")
        if hc.gluten_intolerant:
            conditions.append("Gluten intolerance / Celiac disease")
        if hc.hypertension:
            conditions.append("Hypertension")
        if hc.other_notes.strip():
            conditions.append(f"Other: {hc.other_notes.strip()}")

        if conditions:
            meal_desc = "; ".join(
                f"{m.name} ({m.portion}, {m.calories} kcal, {m.carbs_g:.0f}g carbs, {m.fat_g:.0f}g fat)"
                for m in req.last_meal
            )
            mems = _normalize_memories(req.memories)
            notes = "; ".join(m["memory_text"] for m in mems if m["category"] != "habit_candidate")
            memory_note = f"User context to remember: {notes}.\n\n" if notes else ""
            weight_note = _weight_context_block(req.recent_weights, req.goal_weight)
            health_prompt = (
                f"Health profile: {', '.join(conditions)}.\n\n"
                f"{memory_note}"
                f"Meal just logged: {meal_desc}.{weight_note}\n\n"
                "If any food in this meal is potentially problematic for ONE of the listed health conditions, "
                "write a single informative note in 1–2 sentences. Be specific — name the food and the concern. "
                "Do NOT diagnose or prescribe medication. Use language like 'may want to monitor' or "
                "'consider an alternative'. If nothing is relevant, respond with exactly: NO_FLAG\n\n"
                "Respond in English."
            )
            try:
                r = client.models.generate_content(
                    model=MODEL,
                    contents=[types.Content(role="user", parts=[types.Part(text=health_prompt)])],
                    config=types.GenerateContentConfig(temperature=0.3),
                )
                msg = (r.text or "").strip()
                if msg and msg != "NO_FLAG" and not msg.upper().startswith("NO_FLAG"):
                    return {
                        "triggered": True, "type": "health_context",
                        "severity": "info", "message": msg,
                    }
            except Exception:
                pass

    if not trigger:
        return {"triggered": False, "type": None, "message": None}

    rem  = max(target - cal, 0)
    over = max(cal - target, 0)
    vals = {
        "cal": cal, "target": target, "over": over, "rem": rem,
        "prot": req.protein_today, "ptgt": ptgt,
        "cpct": cal / target * 100 if target else 0,
        "carb": req.carbs_today, "ctgt": ctgt,
    }
    # Macro triggers are deterministic — build the message locally instead of
    # paying for a Gemini call to rephrase numbers we already have. Gemini is
    # reserved for user-initiated chat, food-scan analysis, and the dynamic
    # health-context check above.
    msg = build_butler_message(trigger, req.mode, req.user_name, vals)
    return {"triggered": True, "type": trigger, "severity": _BUTLER_SEVERITY[trigger], "message": msg}


@app.get("/api/weather")
async def get_weather(lat: float, lon: float, _user: dict = Depends(get_current_user)) -> dict:
    if not OPENWEATHER_KEY:
        return {"temp": None}
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params={"lat": lat, "lon": lon, "appid": OPENWEATHER_KEY, "units": "metric"},
            )
        if r.status_code != 200:
            return {"temp": None}
        d = r.json()
        return {
            "temp":      round(d["main"]["temp"], 1),
            "condition": d["weather"][0]["main"],
            "humidity":  d["main"]["humidity"],
        }
    except Exception:
        return {"temp": None}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
