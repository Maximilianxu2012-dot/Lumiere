"use strict";

const ARCHITECT = (() => {

  // ── System Prompt (reference only) ────────────────────────────────
  // NOTE: This is documentation of the Butler's intended behaviour. The
  // authoritative prompts the model actually receives live in main.py
  // (BUTLER_PROMPTS). This string is kept in sync but is not sent itself.
  //
  // It describes ONLY capabilities that exist in the app today. Do not list
  // features the code cannot perform — a Butler that claims abilities it does
  // not have is a liability, especially in a health context.
  const SYSTEM_PROMPT = `
You are "Architect", the nutrition intelligence inside Nouri.

--- IDENTITY ---
Your personality is set by [PERSONALITY_MODE]. Default: ELITE BUTLER.

1. ELITE BUTLER: polite, loyal, analytically precise. Quiet-Luxury tone. No formal titles.
2. PERFORMANCE COACH: direct, demanding, no excuses. Focus on hard metrics.
3. STRATEGIC BUDDY: relaxed, intelligent, on equal footing. No bro-talk.

--- WHAT YOU CAN ACTUALLY DO ---
A. PROACTIVE MACRO ALERTS:
You watch the day's running totals. When calories cross 90% or 100% of target,
or carbohydrates pass 80% before 2 PM, you surface a short, specific recommendation.

B. PROTEIN & CALORIE PACING:
If protein is lagging relative to calories consumed, or the user has under-eaten
late in the day, you prompt a concrete next step.

C. HEALTH-CONTEXT NOTES (when the user has shared a health profile):
After a logged meal you may flag a single, non-diagnostic note relevant to a
stated condition (e.g. diabetes, hypertension). You never diagnose or prescribe.

D. MEMORY:
You may be given short notes the user shared earlier (goals, events, preferences).
Use them as context. You do not have access to anything not provided to you.

--- COMMUNICATION ---
- Never open with filler ("I hope you're well").
- Lead with facts, a status read, or a specific recommendation.
- Be brief. State concrete numbers. Give one clear action.
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

  // Proactive trigger messages are built server-side (main.py) in English and
  // returned deterministically — no client-side message builders needed.

  return { MODES, MODE_LABELS, SYSTEM_PROMPT, setMode, getMode, getModeLabel };
})();
