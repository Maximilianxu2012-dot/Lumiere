# Nouri — Post-Fix Report

**Strike team:** Marcus Webb (architecture) + Sarah Chen (product)
**Date:** 2026-05-31
**Scope:** Fixes #2–#9 from the expert panel. Fix #1 (true proactivity) was deliberately skipped — it requires a native app + push notifications, which is out of scope for the web build.

---

## What was fixed

### ✅ #2 — UTC day boundary (CRITICAL)
- `today()` now uses `new Date().toLocaleDateString('en-CA')` → local YYYY-MM-DD, not UTC.
- The "yesterday" calculation in the morning recap was fixed the same way (it was also UTC and would have been off by a day).
- **Verified:** zero `toISOString().slice(0,10)` day-boundary references remain.
- **Impact:** A US user logging dinner at 8 PM Pacific now lands on the correct day instead of tomorrow's UTC bucket.

### ✅ #3 — Scan accuracy communication
- Added a muted one-liner under every scan's nutrition total: *"Estimates may vary — adjust portions if needed."* (Inter, 11px, opacity 0.5, no gold.)
- Every macro number per item (kcal / P / C / F) is now a **tap-to-edit** input. Edits recompute the total live; corrected values are what get saved to the journal.

### ✅ #4 — Sync data loss / multi-device (CRITICAL)
- New table **`daily_logs`** (`user_id`, `date` text YYYY-MM-DD, `log_data` jsonb, `updated_at`), PK `(user_id, date)`, full RLS (own-rows only).
- New endpoints: `GET /api/logs/recent` (last **7 days** only) and `POST /api/logs/day` (per-day upsert; `updated_at` refreshed every write → **most-recent-write-wins**).
- Frontend now tracks **dirty days** and upserts only changed days. Profile/settings/onboarding still live in `user_state`; the food log no longer rides inside that blob.
- **One-time migration (data-preserving):** on next login, each user's legacy log (inside `user_state.state.log`) is copied into `daily_logs`, one row per day, then `logsMigratedV2` is set. Nothing is deleted server-side. **Verified:** both existing users' `user_state` rows still contain their `log`.
- **Impact:** Two devices editing different days can no longer erase each other. Same-day conflicts resolve by timestamp instead of silently clobbering the whole history.

### ✅ #5 — Onboarding length
Condensed from ~12 sequential screens toward the <90s target, **without dropping any data point**:
- **Vitals on one screen:** age + height + weight + sex now collected together (was 3 separate steps).
- **Lifestyle on one screen:** activity + sleep selectors side by side, with a single optional "anything I should know" free-text field (was 2 steps + 2 separate optional note prompts).
- Diet preferences and health/allergy notes both keep a clearly visible **"Skip for now."**
- Relationship steps (Butler name, address style, name), goal + goal-weight, and the plan offer are unchanged — they carry the product's personality and core data.
> See "Decisions needing review" — the two separate optional note fields (activity vs. sleep) were merged into one.

### ✅ #6 — German error leakage
- All **user-facing** strings are English: backend `HTTPException` details ("AI request failed", "Invalid AI response", "Chat error", "No chat response received") and frontend strings (`Error ${status}`, "Meal", aria-labels "Remove", chat `Error:`).
- **Internal Gemini analysis prompts (food scan, fridge, recipe) intentionally remain in German** per the documented `CLAUDE.md` convention ("Interne Analyse-Prompts bleiben auf Deutsch für Qualität"). These are never shown to the user.

### ✅ #7 — COGS optimization
- `POST /api/butler/check` **no longer calls Gemini** to phrase macro triggers (calorie overrun / 90% / protein-low / carb-routing). It now builds deterministic, mode-flavored English messages locally.
- Gemini is reserved for: user-initiated **chat**, **food-scan** analysis, and the **dynamic health-context** note (Pro-only, low frequency, genuinely needs reasoning over an arbitrary meal).
- **Impact:** Per-user LLM cost is now flat regardless of engagement on the most-fired path. Previously every meal log + every 15-min interval could trigger a paid call to rephrase numbers we already had.

### ✅ #8 — Butler memory (foundation)
- New table **`butler_memory`** (`id`, `user_id`, `created_at`, `memory_text`, `category` ∈ goal|event|preference|health|reminder, `expires_at`), full RLS.
- New endpoints: `POST /api/memory` (save) and `GET /api/memory` (non-expired, newest first).
- Frontend detects time-sensitive / durable info in chat messages (events with horizons get an auto-expiry; health/goals/preferences/reminders are durable), saves them, and injects recent memories into both the **chat** prompt and the **health-context** prompt.
- A subtle **"💭 remembered"** indicator appears for ~1s when a memory is captured.

### ✅ #9 — architectConfig.js cleanup
- Removed fabricated capabilities the app cannot perform: **Perplexity restaurant scan, Deep Research ingredient validation, Bio-Feedback sleep-adaption (auto-lowering calories), Micro-Habit intervention mode.**
- `SYSTEM_PROMPT` rewritten to describe only real features (proactive macro alerts, protein/calorie pacing, health-context notes, memory).
- Removed dead, German-language client-side message builders (`checkCalorieOverrun` and `_msg*`) — messaging is now server-side English (see #7).

### ✅ Final sweep
- `Lumière`/`Lumiere` (all casings): **zero** in code/docs. (Note: the GitHub repo itself is still named `Lumiere` — see below.)
- **Validation:** `main.py` compiles; `architectConfig.js` and the full inline app script both pass `node --check`.
- **Supabase security advisors:** no RLS issues on the new tables. Three pre-existing warnings remain (not introduced here) — see below.

---

## Decisions that need Maximilian's review

1. **Onboarding note fields merged.** The two separate optional free-text prompts (activity context, sleep context) are now one combined "anything I should know" field on the lifestyle screen. All structured data (age, height, weight, sex, activity, sleep quality, goal) is still collected. If you want sleep-specific notes kept separate, say so and I'll split them back out.
2. **German internal prompts kept.** Food/fridge/recipe analysis prompts stay German per `CLAUDE.md` (quality). The strike-team brief said "translate every German string," but the project memory says keep them — I followed the project memory since these are never user-facing. Flag if you want them translated anyway.
3. **Health-context note still uses Gemini.** Fix #7 says "only chat + food scan use Gemini." I kept the Pro-only health-context check on Gemini because it must reason over an arbitrary meal vs. arbitrary conditions — it can't be a static template. It's low-frequency and gated behind Pro. Tell me if you'd rather kill it entirely.
4. **Pre-existing Supabase warnings (not from this work):** a `public.rls_auto_enable()` SECURITY DEFINER function is callable via the REST API, and Auth leaked-password protection is off. Worth cleaning up before US launch, but unrelated to these fixes.
5. **`OPENWEATHER_API_KEY` missing from `render.yaml`.** The weather feature degrades to "no data" without it. Add it as an env var in Render if you want weather-based proactive nudges live.
6. **Repo name.** `git remote` points to `…/Lumiere.git`. The app is "Nouri" everywhere in code now; the GitHub repo name is cosmetic but inconsistent.

---

## The biggest remaining weakness (after all fixes)

**Proactivity still doesn't run when the app is closed — and that is the entire product positioning.**

Fix #1 was skipped by design, but it remains the #1 existential risk. "An app that thinks ahead" is currently a `setInterval` inside an open browser tab; the moment the user closes the PWA, the Butler is inert. No native app, no push notifications. Everything we fixed today makes Nouri a *correct, cheaper, multi-device-safe, honest* tracker — but it does not yet make it the proactive companion the brand promises.

**Closely behind it:** scan accuracy. We made the app *honest* about estimates (#3) and *editable*, but the underlying portion estimation is still un-calibrated Gemini. Honesty buys trust for a few weeks; accuracy is what retains. The real moat — a calibrated portion model + the consumption data to train it — still does not exist.

**Recommendation order:** (1) native shell + push to make proactivity real, (2) a scan-accuracy calibration pass with real measured meals, (3) then monetization.
