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


class RecipeRequest(BaseModel):
    ingredients: list[str]
    targets: Targets | None = None
    remaining_calories: int | None = None


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
    # Mifflin-St Jeor: most accurate BMR formula in clinical use
    base = 10 * p.weight_kg + 6.25 * p.height_cm - 5 * p.age
    bmr = base + 5 if p.sex == "m" else base - 161
    cal = round(bmr * ACTIVITY_FACTOR[p.activity] + GOAL_DELTA[p.goal])

    protein_per_kg = {"lose": 2.0, "maintain": 1.8, "gain": 2.0}[p.goal]
    protein = round(p.weight_kg * protein_per_kg)
    fat = round(cal * 0.30 / 9)
    carbs = round((cal - protein * 4 - fat * 9) / 4)
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


@app.post("/api/recipe", response_model=Recipe)
async def generate_recipe(req: RecipeRequest) -> Recipe:
    if not req.ingredients:
        raise HTTPException(400, "Keine Zutaten angegeben")

    constraints: list[str] = []
    if req.remaining_calories is not None:
        constraints.append(
            f"Ziel: ~{req.remaining_calories} kcal pro Portion (±100 kcal Toleranz)."
        )
    if req.targets:
        constraints.append(f"Hoher Proteinanteil bevorzugt (Tagesziel {req.targets.protein_g} g).")

    prompt = (
        f"Verfügbare Zutaten mit Mengen: {', '.join(req.ingredients)}.\n\n"
        "WICHTIG — Mengen-Disziplin:\n"
        "- Halte dich strikt an die verfügbaren Mengen. Du darfst weniger verwenden, NIEMALS mehr.\n"
        "- Wenn jemand z.B. '2 Eier' hat, kann das Rezept 1 oder 2 Eier verlangen — niemals 5 oder 20.\n"
        "- Reichen die Mengen nur für eine kleine Portion, schlage entsprechend skaliert vor "
        "(z.B. 'Frühstück für 1 Person') statt eine größere Portion zu erfinden.\n"
        "- Falls eine Zutat als 'Konservendose, Inhalt unklar' o.ä. gelistet ist, ignoriere sie.\n"
        "- Wähle eine sinnvolle Auswahl der Zutaten — nicht alles muss verwendet werden, "
        "aber rechne nichts hinzu, was nicht in der Liste steht (außer Grundwürze: Salz, Pfeffer, Öl).\n\n"
        f"{chr(10).join(constraints)}\n\n"
        "Stil: reduziert, hochwertig, alltagstauglich. Klare Schritte, präzise Mengen "
        "in metrischen Einheiten. Keine Floskeln. Antworte auf Deutsch."
    )
    return await generate_structured([prompt], Recipe)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
