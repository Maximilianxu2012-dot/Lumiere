# Nouri — Companion Sprint

**Team:** Chen (Butler intelligence) · Blackwell (features) · Fontaine (behavior) · Webb (security) · Tanaka (QA)
**Files:** `index.html`, `main.py`, `architectConfig.js`. One file each, no frameworks, palette/fonts preserved.

---

## Round 1 — Dr. Aria Chen · Butler intelligence

**1a. System prompt rewrite.** Replaced the old per-mode prompts in `main.py` with a shared `_BUTLER_CORE` spine + three tone layers. The core defines the character (calm, direct, attentive — a companion, not a chatbot), forces English, and **explicitly forbids**: sycophantic filler ("Great question!", "Absolutely!"), claiming capabilities it lacks, *ever* saying it can't access the user's data, and generic encouragement without a number-backed reason. The three tones (Elite Butler / Performance Coach / Strategic Buddy) change register, never substance — "the same companion in a different room." `architectConfig.js` `SYSTEM_PROMPT` rewritten to match as the canonical character doc. The `chat()` endpoint already injects **memory → name → date → calorie/macro status** as the first block of every prompt; verified and kept.

**1b. Memory pattern recognition.** Memory cache now holds full objects, not flat strings. New `memoriesForPrompt()` **groups by category** (goals → events → preferences → health → reminders → reflections) and **prefixes "recent:"** on anything from the last 7 days, so the Butler leans on what's fresh. Auto-save on detected info now confirms with a quieter **"💭 noted"** that fades after **2s** (was "remembered", 1s).

**1c. Morning briefing.** 6–9 AM, once per morning: a 2–3 sentence bubble built from **yesterday's performance** (calories vs target, protein), the **streak**, and the **next event in memory** ("Don't forget: …"). Auto-dismisses after 8s or on tap; stored in `butlerFired`.

**1d. Evening summary.** 20–22, ≥2 meals logged, once: one sentence from real numbers — "Strong day — protein at 162g and you stayed under your limit." / "Carbs ran high today (240g). Keep tomorrow leaner."

## Round 2 — Noah Blackwell · features

**2a. Goals.** New "My Goal" row in the Profile panel → `renderGoalScreen`: goal type (Lose / Build muscle / Maintain / Eat cleaner), target weight (pre-filled), target date. On save it validates with the **0.75 kg/week (lose) / 0.25 kg/week (gain)** caps, computes `weeklyTarget`, `dailyCalorieAdjustment` (~7700 kcal/kg), and `weeksRemaining`, stores them in **`state.goals`**, and the Butler responds immediately ("12 weeks to lose weight. That means staying around 1,950 kcal daily. I'll keep track."). Dashboard gains a **3px gold progress bar** below the ring: "Week 2 of 12 · On track" — derived from weight data when present, otherwise from recent **calorie adherence** (graceful with zero weight entries).
> *Architecture note (Noah): goals ride in `state.goals` and sync through the existing `user_state` row — no new table, no new endpoint. Exactly what's needed and nothing more.*

**2b. Meal categorization.** Every log groups by time into **Breakfast / Lunch / Afternoon snack / Dinner / Evening snack**, with a Cormorant-italic label above each block. Entries within **45 minutes** collapse into one meal. Label is per group, not per item.

**2c. Fridge scan redesign.** After a scan, ingredients sort into **Protein / Vegetables / Dairy / Grains / Other**, each with a minimal 16px charcoal SVG icon (no emoji). A Butler summary leads ("You have enough for a solid, high-protein meal."), ingredients render as **cream chips with 8px radius**, and the screen offers one gold **Create Recipe** primary + a quiet **Add more ingredients** link.

## Round 3 — Isabel Fontaine · behavioral design

**3a. Streak, refined.** Dashboard shows a **row of 7 dots** (gold = logged, outlined = missed); today's dot pulses. A **13:00 nudge** ("You haven't logged yet today. Don't break the streak.") fires once, **only when streak > 2** (no nagging new users). A broken streak is acknowledged once on next open: "Yesterday broke the streak. Today starts a new one." — no drama.

**3b. Scan micro-moments.** Well-balanced meal (protein > 20g, calories within ±20% of a quarter-day) → one of **8 rotating lines**, never the same twice in a row (tracked in `state.lastMicroFeedback`). Heavy meal (one meal > 50% of the daily goal) → **Butler says nothing; the ring pulses amber once**. Silence is the signal.

**3c. Weekly reflection.** Sunday 18:00–20:00, once per ISO week: "How did this week feel — on track, or harder than expected?" → "On track" gets a brief acknowledgment; "Harder than expected" opens a one-line field saved as a **`reflection`** memory the Butler carries forward.

## Round 4 — Marcus Webb · security & stability

- **4a/4c — new endpoints/tables:** none. Goals sync through `user_state`, already JWT-authed and RLS-scoped (`auth.uid() = user_id`). Verified RLS still enabled on `user_state`, `daily_logs`, `butler_memory` (1/4/4 policies). Nothing new to secure because nothing new was exposed.
- **4b — rate limiting:** generalized to per-bucket ceilings. **memory 120/min**, scan/butler 30/min (unchanged), goals N/A (no endpoint). 429 → "Slow down — try again in a moment."
- **4d — input sanitization:** goal weight parsed as float with **30–300** bounds (and a <40 health reject); target date validated as a **real future date ≤ 5 years out**. No user input is concatenated into queries — memory/goal data goes to PostgREST as JSON bodies under RLS.
- **4e — sweep:** zero new German user-facing strings; zero secret literals; `architectConfig.js` still free of fabricated capabilities; the 90-day memory prune is category-agnostic, so the new `reflection` category is covered automatically. New `MemoryInput` accepts `reflection`.

## Yuki Tanaka — QA (what was checked / caught)
Validated continuously: `main.py` compiles; `architectConfig.js` and the full inline app script pass `node --check` after every round.
- **Caught & fixed:** the briefing/evening/streak triggers initially referenced `t.entries`, which doesn't exist on `todayTotals()` → switched to a dedicated `entriesToday` count. The streak-break check referenced a `_priorStreak` field that was never set → simplified to use the still-current `logStreak` (which only resets on the next log).
- **Verified by construction:** morning/evening/lunch/reflection triggers each guard on `butlerFired`/`lastReflectionWeek` so they fire once; memory injection groups + flags recent; goal validation rejects unrealistic targets before saving; fridge results categorize and `Create Recipe` reuses the guarded `makeRecipe` (null-targets safe); offline scan/fridge/recipe still show the calm states from the prior sprint.

---

## Deploy sign-off
- Aria — Butler prompt live, memory grouped + recency-weighted ✓
- Noah — Goals, meal categories, fridge redesign shipped ✓
- Isabel — streak dots, nudges, micro-feedback, weekly reflection; all once-only ✓
- Marcus — no new attack surface, memory rate-limited, RLS intact, inputs bounded ✓
- Yuki — syntax/logic pass clean; two real bugs caught and fixed before deploy ✓

## Intentionally left for the next sprint
- **A weight log.** Goal progress falls back to calorie adherence because there's nowhere to record actual weigh-ins. The honest "0.3 kg behind" number needs a lightweight weight-entry history.
- **Recomputing calorie targets when the goal changes.** Today the goal stores a `dailyCalorieAdjustment` but the daily target still comes from onboarding's `compute_targets`. They should reconcile.
- **Sugar signal.** Round 3b's "sugar extremely high" branch isn't wired because `FoodItem` carries no sugar field; only the >50%-of-day calorie rule triggers the amber pulse.

## The single most important thing to build next
**A weight-entry history.** It's the keystone the whole companion rests on: it turns goal progress from a calorie-adherence guess into a real trajectory, lets the morning briefing say "down 0.4 kg this week — on pace," and gives the Butler the one piece of ground truth it's currently missing. Everything we built this sprint gets sharper the moment the app knows what the user actually weighs over time.
