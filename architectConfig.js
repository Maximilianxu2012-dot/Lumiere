"use strict";

const ARCHITECT = (() => {

  // ── System Prompt (Referenz für zukünftige API-Calls) ─────────────
  const SYSTEM_PROMPT = `
DU BIST DER "PERFORMANCE INTELLIGENCE CORE" (CODENAME: ARCHITECT).
DEINE MISSION: DIE PHYSISCHE UND MENTALE OPTIMIERUNG DES NUTZERS MIT MATHEMATISCHER PRÄZISION UND ELITÄREM SERVICE.

--- IDENTITÄT & STEUERUNG ---
Deine Persönlichkeit ist variabel und wird durch das Feld [PERSONALITY_MODE] gesteuert. Dein Standard-Modus ist "ELITE BUTLER".

1. ELITE BUTLER: Hochgradig höflich, loyal, distanziert-analytisch. Keine persönliche Anrede. Quiet Luxury Tonalität. (Navy/Gold Vibe).
2. PERFORMANCE COACH: Direkt, fordernd, keine Ausreden. Fokus auf harte Metriken und Disziplin. (Black/Red Vibe).
3. STRATEGIC BUDDY: Locker, intelligent, auf Augenhöhe. Smarter Austausch ohne "Bro-Talk". (Clean White Vibe).

--- ÜBERTRIEBENE CORE-FEATURES (PROAKTIV) ---
A. PREDICTIVE MACRO ROUTING:
Du berechnest im Hintergrund ständig den Pfad des Tages. Bei 80% Verbrauch der Kohlenhydrate vor 14:00 Uhr generierst du proaktiv ein "Abend-Protokoll" (High-Protein/Low-Carb), um die Bilanz zu retten.

B. DEEP RESEARCH & RESTAURANT SCAN:
Du nutzt APIs (wie Perplexity), um nicht nur Kalorien zu schätzen, sondern Inhaltsstoffe zu validieren. Bei Restaurant-Nennungen scannst du die Karte nach der optimalen Option.

C. BIO-FEEDBACK & SLEEP-ADAPTION:
Bei geringem Schlaf (Daten-Input < 6h) senkst du automatisch das Kalorienziel oder erhöhst den Protein-Anteil, um Muskelabbau durch Cortisol zu verhindern.

D. MICRO-HABIT ENFORCEMENT:
Bei 3-tägiger Zielverfehlung wechselst du automatisch in den "Intervention Mode" – deine Tonalität wird schärfer, die Analysen detaillierter.

--- KOMMUNIKATIONSPROTOKOLL ---
- Starte NIEMALS mit Floskeln wie "Ich hoffe, es geht dir gut".
- Starte IMMER mit Fakten, einem Status-Report oder einer proaktiven Warnung.
- Nutze Markdown-Tabellen für Nährwerte und klare Listen für Handlungsanweisungen.
- Wenn der Nutzer "unlogische" Daten eingibt (Cheat-Meals ohne Protokoll), reagiere mit höflichem, aber bestimmtem Zynismus.

BEISPIEL (Mode: Elite Butler):
"Status-Update. Das aktuelle Glykogen-Level ist nach dem letzten Input gesättigt. Empfehlung für die Abendmahlzeit: 300g mageres Protein, 0g Carbs."
`.trim();

  // ── Modes ─────────────────────────────────────────────────────────
  const MODES = {
    ELITE_BUTLER:      "ELITE_BUTLER",
    PERFORMANCE_COACH: "PERFORMANCE_COACH",
    STRATEGIC_BUDDY:   "STRATEGIC_BUDDY",
  };

  const MODE_LABELS = {
    ELITE_BUTLER:      "Elite Butler",
    PERFORMANCE_COACH: "Performance Coach",
    STRATEGIC_BUDDY:   "Strategic Buddy",
  };

  // Persist mode across sessions
  const _MODE_KEY = "architect_mode_v1";
  let _mode = (() => {
    try {
      const saved = localStorage.getItem(_MODE_KEY);
      return (saved && MODES[saved]) ? saved : MODES.ELITE_BUTLER;
    } catch { return MODES.ELITE_BUTLER; }
  })();

  function setMode(mode) {
    if (!MODES[mode]) return;
    _mode = mode;
    try { localStorage.setItem(_MODE_KEY, mode); } catch {}
  }

  function getMode()      { return _mode; }
  function getModeLabel() { return MODE_LABELS[_mode]; }

  // ── Thresholds ────────────────────────────────────────────────────
  const THRESHOLD_WARNING      = 0.90;  // ≥90% → proaktive Warnung
  const THRESHOLD_OVERRUN      = 1.00;  // ≥100% → Budget überschritten
  const THRESHOLD_CARB_ROUTING = 0.80;  // ≥80% Carbs vor 14:00 → Abend-Protokoll

  // ── Message builders ──────────────────────────────────────────────
  function _msgOverrun(over, pct) {
    return {
      ELITE_BUTLER:
        `Das Tagesbudget wurde um ${over} kcal überschritten ` +
        `(${pct}% des Ziels verbraucht). Empfehlung für verbleibende Mahlzeiten: ` +
        `ausschließlich mageres Protein — 0 g Kohlenhydrate. Die Bilanz ist noch kontrollierbar.`,
      PERFORMANCE_COACH:
        `ÜBERRUN: +${over} kcal — ${pct}% des Ziels. Das kostet Ergebnisse. ` +
        `Keine Ausreden. Nächste Mahlzeit: reines Protein, kein Zucker, null Fett. Jetzt korrigieren.`,
      STRATEGIC_BUDDY:
        `Kurzes Update: du bist ${over} kcal über deinem Ziel (${pct}%). ` +
        `Kein Drama — aber heute Abend wirklich leicht bleiben. Salat + Protein, das reicht.`,
    }[_mode];
  }

  function _msgWarning(pct, remaining) {
    return {
      ELITE_BUTLER:
        `${pct}% des Tagesbudgets verbraucht — noch ${remaining} kcal verbleiben. ` +
        `Abend-Protokoll: High-Protein, reduzierte Kohlenhydrate empfohlen.`,
      PERFORMANCE_COACH:
        `${pct}% weg. Noch ${remaining} kcal übrig — nutze sie präzise. ` +
        `Nur Protein und Gemüse ab jetzt. Kein Spielraum für leere Kalorien.`,
      STRATEGIC_BUDDY:
        `Hey, du bist bei ${pct}% — noch ${remaining} kcal für heute. ` +
        `Heute Abend vielleicht etwas leichter essen? Du bist gut auf Kurs, halt es so.`,
    }[_mode];
  }

  function _msgCarbRouting(carbPct) {
    return {
      ELITE_BUTLER:
        `Makro-Routing aktiv. ${carbPct}% des Kohlenhydrat-Kontingents bereits vor 14:00 Uhr verbraucht. ` +
        `Abend-Protokoll angepasst: High-Protein / Low-Carb.`,
      PERFORMANCE_COACH:
        `Kohlenhydrate bei ${carbPct}% — und es ist noch nicht Mittag. ` +
        `Heute Abend: ausschließlich Protein. Kein Verhandeln.`,
      STRATEGIC_BUDDY:
        `Kurzer Check: ${carbPct}% der Kohlenhydrate schon weg, und es ist noch vor 14 Uhr. ` +
        `Heute Abend lieber Protein-heavy. Easy fix.`,
    }[_mode];
  }

  // ── Main export ───────────────────────────────────────────────────
  /**
   * Prüft nach jedem Log-Eintrag, ob das Kalorien-Budget
   * überschreitet oder proaktive Maßnahmen nötig sind.
   *
   * @param {{ calories: number, protein: number, carbs: number, fat: number }} totals
   * @param {{ calories: number, protein_g: number, carbs_g: number, fat_g: number }} targets
   * @returns {{ triggered: boolean, type: string, severity: string, message: string } | null}
   */
  function checkCalorieOverrun(totals, targets) {
    if (!targets || !totals) return null;

    const calRatio  = targets.calories > 0 ? totals.calories / targets.calories : 0;
    const carbRatio = targets.carbs_g  > 0 ? totals.carbs    / targets.carbs_g  : 0;
    const hour = new Date().getHours();

    // Priority 1: Budget überschritten
    if (calRatio >= THRESHOLD_OVERRUN) {
      const over = Math.round(totals.calories - targets.calories);
      const pct  = Math.round(calRatio * 100);
      return { triggered: true, type: "overrun", severity: "critical", message: _msgOverrun(over, pct) };
    }

    // Priority 2: ≥ 90% verbraucht → proaktive Warnung
    if (calRatio >= THRESHOLD_WARNING) {
      const pct       = Math.round(calRatio * 100);
      const remaining = Math.round(targets.calories - totals.calories);
      return { triggered: true, type: "warning", severity: "warning", message: _msgWarning(pct, remaining) };
    }

    // Priority 3: Predictive Macro Routing — 80% Carbs vor 14:00
    if (carbRatio >= THRESHOLD_CARB_ROUTING && hour < 14) {
      const carbPct = Math.round(carbRatio * 100);
      return { triggered: true, type: "carb_routing", severity: "info", message: _msgCarbRouting(carbPct) };
    }

    return null;
  }

  return { MODES, MODE_LABELS, SYSTEM_PROMPT, setMode, getMode, getModeLabel, checkCalorieOverrun };
})();
