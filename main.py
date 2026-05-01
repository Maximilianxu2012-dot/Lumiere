import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()  # liest die .env-Datei und macht GEMINI_API_KEY verfügbar

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY environment variable is not set")

MODEL = "gemini-2.5-flash"
ROOT = Path(__file__).parent

client = genai.Client(api_key=API_KEY)
app = FastAPI(title="Lumière")


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


BUTLER_PROMPTS: dict[str, str] = {
    "ELITE_BUTLER": (
        "Du bist der Performance Intelligence Core 'Architect' von Lumière. "
        "Modus: Elite Butler. "
        "ABSOLUTES VERBOT: Verwende NIEMALS 'Chairman', 'Sir', 'Herr' oder andere formelle Titel. "
        "Falls der Name des Nutzers bekannt ist, sprich ihn beim Vornamen an (z.B. 'Guten Tag, Max.'). "
        "Falls kein Name bekannt ist, starte direkt mit dem Inhalt — keine Anrede. "
        "Tonalität: höflich, sachlich-analytisch, Quiet-Luxury. "
        "Nur Fakten, kurze Empfehlungen, keine Floskeln. "
        "Antworte in 2–4 Sätzen auf Deutsch."
    ),
    "PERFORMANCE_COACH": (
        "Du bist der Performance Intelligence Core 'Architect' von Lumière. "
        "Modus: Performance Coach. Direkte Sprache, du-Form, keine Ausreden. "
        "Fokus auf harte Metriken und Ergebnisse. Kurz, klar, fordernd. "
        "Sprache: Deutsch."
    ),
    "STRATEGIC_BUDDY": (
        "Du bist der Performance Intelligence Core 'Architect' von Lumière. "
        "Modus: Strategic Buddy. Locker, auf Augenhöhe, intelligent — kein Bro-Talk. "
        "Kurze smarte Antworten, no nonsense. Sprache: Deutsch."
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


@app.post("/api/scan/food", response_model=FoodScan)
async def scan_food(image: UploadFile = File(...)) -> FoodScan:
    data = await image.read()
    prompt = (
        "Du bist Ernährungsexperte. Analysiere dieses Foto eines Gerichts. "
        "Identifiziere jede Komponente einzeln, schätze Portionsgrößen visuell anhand "
        "von Tellergröße und üblichen Referenzobjekten. Sei eher konservativ bei Kalorien. "
        "Setze 'confidence' auf 'high' nur wenn alles klar erkennbar ist. Antworte auf Deutsch."
    )
    return await generate_structured([prompt, image_part(image, data)], FoodScan)


@app.post("/api/scan/fridge", response_model=FridgeScan)
async def scan_fridge(image: UploadFile = File(...)) -> FridgeScan:
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
async def profile_targets(profile: Profile) -> Targets:
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
async def refine_scan(req: RefineRequest) -> FoodScan:
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
async def generate_recipe(req: RecipeRequest) -> Recipe:
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
async def chat(req: ChatRequest) -> dict:
    mode = req.mode if req.mode in BUTLER_PROMPTS else "ELITE_BUTLER"
    system = BUTLER_PROMPTS[mode]

    if req.context:
        c = req.context
        if c.user_name:
            system += f"\n\nName des Nutzers: {c.user_name}. Sprich ihn/sie mit dem Vornamen an."
        parts = [f"{c.calories_today:.0f} kcal verbraucht"]
        if c.calories_target:
            remaining = c.calories_target - c.calories_today
            parts.append(f"Ziel {c.calories_target} kcal ({remaining:.0f} kcal verbleibend)")
        if c.protein_target:
            parts.append(f"Protein {c.protein_today:.0f}/{c.protein_target} g")
        system += f"\n\nAktueller Tagesstand: {', '.join(parts)}."

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
