import json
import os
from contextlib import asynccontextmanager
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

MODEL = "gemini-2.5-flash"
ROOT  = Path(__file__).parent

client = genai.Client(api_key=API_KEY)

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

app = FastAPI(title="Lumière", lifespan=lifespan)


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


# ── Schemas ─────────────────────────────────────────────────────────
class FoodItem(BaseModel):
    name: str
    portion: str = Field(description="z.B. '150 g', '1 Stück', '1 Tasse'")
    calories: int
    protein_g: float
    carbs_g: float
    fat_g: float


class FoodScan(BaseModel):
    items: list[FoodItem]
    confidence: Literal["low", "medium", "high"]


class FridgeItem(BaseModel):
    name: str
    portion: str = ""
    # Tolerant gegenüber dem, was das Modell vorschlägt — verhindert Schema-Crashs
    category: str = "sonstiges"


class FridgeScan(BaseModel):
    items: list[FridgeItem]


Sex = Literal["m", "f"]
Activity = Literal["sedentary", "light", "moderate", "active", "very_active"]
Goal = Literal["lose", "maintain", "gain"]


class Profile(BaseModel):
    age: int = Field(ge=14, le=100)
    weight_kg: float = Field(gt=30, lt=300)
    height_cm: float = Field(gt=120, lt=230)
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
    summary: str = Field(description="Eleganter, kurzer Untertitel — max. 12 Wörter.")
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


BUTLER_PROMPTS: dict[str, str] = {
    "ELITE_BUTLER": (
        "You are the Performance Intelligence Core 'Architect' for Lumière. "
        "Mode: Elite Butler. "
        "ABSOLUTE PROHIBITION: NEVER use 'Sir', 'Mr.', or other formal titles. "
        "If the user's name is known, address them by first name only (e.g. 'Max.'). "
        "If no name is known, begin directly with the content — no greeting. "
        "Tone: polite, analytically precise, Quiet-Luxury. "
        "Facts only, brief recommendations, no filler phrases. "
        "Respond in 2–4 sentences in English."
    ),
    "PERFORMANCE_COACH": (
        "You are the Performance Intelligence Core 'Architect' for Lumière. "
        "Mode: Performance Coach. Direct language, second person, no excuses. "
        "Focus on hard metrics and results. Short, clear, demanding. "
        "Respond in English."
    ),
    "STRATEGIC_BUDDY": (
        "You are the Performance Intelligence Core 'Architect' for Lumière. "
        "Mode: Strategic Buddy. Relaxed, on equal footing, intelligent — no bro-talk. "
        "Short, smart responses, no nonsense. Respond in English."
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
async def generate_structured(parts: list, schema: type[BaseModel]) -> BaseModel:
    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=parts,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.4,
            ),
        )
    except Exception as exc:
        # Volltext der Modellabfrage durchreichen, damit Frontend was Sinnvolles zeigt
        raise HTTPException(status_code=502, detail=f"Gemini-Aufruf fehlgeschlagen: {exc}") from exc

    parsed = response.parsed
    if parsed is None:
        # Rohtext mitgeben, falls vorhanden — hilft beim Debuggen
        raw = (response.text or "").strip()[:300]
        detail = "Modellantwort ungültig" + (f" — '{raw}'" if raw else "")
        raise HTTPException(status_code=502, detail=detail)
    return parsed


def image_part(image: UploadFile, data: bytes) -> types.Part:
    return types.Part.from_bytes(data=data, mime_type=image.content_type or "image/jpeg")


# ── Routes ─────────────────────────────────────────────────────────
@app.get("/")
async def index() -> FileResponse:
    return FileResponse(ROOT / "index.html")


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


@app.post("/api/scan/food", response_model=FoodScan)
async def scan_food(image: UploadFile = File(...), _user: dict = Depends(get_current_user)) -> FoodScan:
    data = await image.read()
    prompt = (
        "Du bist Ernährungsexperte. Analysiere dieses Foto eines Gerichts. "
        "Identifiziere jede Komponente einzeln, schätze Portionsgrößen visuell anhand "
        "von Tellergröße und üblichen Referenzobjekten. Sei eher konservativ bei Kalorien. "
        "Setze 'confidence' auf 'high' nur wenn alles klar erkennbar ist. Antworte auf Deutsch."
    )
    return await generate_structured([prompt, image_part(image, data)], FoodScan)


@app.post("/api/scan/fridge", response_model=FridgeScan)
async def scan_fridge(image: UploadFile = File(...), _user: dict = Depends(get_current_user)) -> FridgeScan:
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
        "Antworte auf Deutsch."
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
async def refine_scan(req: RefineRequest, _user: dict = Depends(get_current_user)) -> FoodScan:
    if not req.description.strip():
        raise HTTPException(400, "Keine Beschreibung angegeben")
    orig = ", ".join(f"{i.name} ({i.portion}, {i.calories} kcal)" for i in req.original_items) or "nichts erkannt"
    prompt = (
        f"Die KI hat folgende Mahlzeit erkannt: {orig}.\n\n"
        f"Der Nutzer korrigiert/ergänzt: \"{req.description}\"\n\n"
        "Erstelle auf Basis dieser Korrektur eine vollständige, revidierte Komponentenliste "
        "mit realistischen Nährwerten. Setze 'confidence' auf 'high' wenn die Beschreibung klar ist. "
        "Antworte auf Deutsch."
    )
    return await generate_structured([prompt], FoodScan)


@app.post("/api/recipe", response_model=Recipe)
async def generate_recipe(req: RecipeRequest, _user: dict = Depends(get_current_user)) -> Recipe:
    inspiration_mode = not req.ingredients

    if inspiration_mode and not req.taste:
        raise HTTPException(400, "Keine Zutaten oder Geschmackspräferenzen angegeben")

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
        "Stil: reduziert, hochwertig, alltagstauglich. Klare Schritte, präzise Mengen "
        "in metrischen Einheiten. Keine Floskeln. Antworte auf Deutsch."
    )
    return await generate_structured([prompt], Recipe)


@app.post("/api/chat")
async def chat(req: ChatRequest, _user: dict = Depends(get_current_user)) -> dict:
    mode = req.mode if req.mode in BUTLER_PROMPTS else "ELITE_BUTLER"
    system = BUTLER_PROMPTS[mode]

    # Erstnachricht = nur 1 Message im Array (noch keine Antwort gegeben)
    is_first = len(req.messages) == 1

    if req.context:
        c = req.context
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
        parts = [f"{c.calories_today:.0f} kcal consumed"]
        if c.calories_target:
            remaining = c.calories_target - c.calories_today
            parts.append(f"target {c.calories_target} kcal ({remaining:.0f} remaining)")
        if c.protein_target:
            parts.append(f"protein {c.protein_today:.0f}/{c.protein_target}g")
        system += f"\n\nCurrent day status: {', '.join(parts)}."
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
        raise HTTPException(status_code=502, detail=f"Chat-Fehler: {exc}") from exc

    if not response.text:
        raise HTTPException(status_code=502, detail="Keine Chat-Antwort erhalten")

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


@app.post("/api/butler/check")
async def butler_check(req: ButlerCheckRequest, _user: dict = Depends(get_current_user)) -> dict:
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
            health_prompt = (
                f"Health profile: {', '.join(conditions)}.\n\n"
                f"Meal just logged: {meal_desc}.\n\n"
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
    desc = _TRIGGER_DESC[trigger].format(
        cal=cal, target=target, over=over, rem=rem,
        prot=req.protein_today, ptgt=ptgt,
        cpct=cal / target * 100 if target else 0,
        carb=req.carbs_today, ctgt=ctgt,
    )
    mode = req.mode if req.mode in BUTLER_PROMPTS else "ELITE_BUTLER"
    name_note = (
        f"User's name: {req.user_name}. Address them by first name."
        if req.user_name else "No personal address."
    )
    prompt = (
        f"{BUTLER_PROMPTS[mode]}\n\n"
        f"{name_note}\n\n"
        f"Situation: {desc}\n\n"
        "Write a precise message (max. 2 sentences). "
        "State concrete numbers. No filler phrases. Give a specific recommendation."
    )
    try:
        r = client.models.generate_content(
            model=MODEL,
            contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
            config=types.GenerateContentConfig(temperature=0.35),
        )
        msg = (r.text or "").strip()
        if not msg:
            return {"triggered": False, "type": None, "message": None}
        return {"triggered": True, "type": trigger, "severity": _BUTLER_SEVERITY[trigger], "message": msg}
    except Exception:
        return {"triggered": False, "type": None, "message": None}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
