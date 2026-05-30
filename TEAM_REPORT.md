# Nouri — Six-Specialist Build Report

**Date:** 2026-05-31
**Team:** Patel (AI/ML), Okafor (Full-Stack), Reyes (UX), Kim (Health Psych), Brenner (Growth), Sharma (Security)
**Outcome:** All six rounds shipped. Backend compiles, all JS passes `node --check`, RLS verified, deployed to Render.

---

## Round 1 — Dr. Maya Patel (AI/ML)

**Food scan prompt — rewritten (`main.py` `SCAN_PROMPT`):**
- Fully English, structured into CONTAINER / PORTION / CONFIDENCE / NAMES blocks.
- Portion estimation forced by visual reference: 26 cm plate fraction, fist ≈ 150–200 ml, palm ≈ 85 g meat, thumb ≈ 30 g fat, restaurant portion norms.
- Soup-vs-drink decided by **container shape, never color** (bowl = dish, glass = beverage).
- **Per-item confidence** (`FoodItem.confidence`) added to the schema. When an item is `low`, the model also fills **`calories_range`** (e.g. `"320-480"`), surfaced in the scan card as "Low confidence — likely 320–480 kcal." Point estimates are always returned so tracking still has a number.
- Names: English, ≤3 words, banned qualifiers ("probably/likely/possibly/maybe/perhaps/flavor" + German equivalents).
- **Temperature 0.2** for scans (precision); chat stays **0.7**. `generate_structured` now takes a `temperature` param.
- 5 internal test cases documented in comments above `scan_food`.

**Butler chat prompt — upgraded (`chat`):**
- Memory is now the **first** context block; name + today's date + full day status (incl. carbs/fat) injected every request (`ChatContext.today_date` added; frontend sends `today()`).
- Hard rule added: **never claim lack of access** to the user's data/history/numbers.

## Round 2 — James Okafor (Full-Stack)

- **Weather:** confirmed wired end-to-end — `get_weather` reads the key from the environment; frontend `initLocation()` calls `/api/weather`, caches the result, and `proactiveCheck` uses it. On failure it silently skips weather triggers (no crash). *(Kept `os.getenv` rather than `os.environ[...]` so a missing key degrades gracefully instead of crashing boot — see risks.)*
- **Memory cleanup:** `POST /api/memory/cleanup` deletes durable memories (no `expires_at`) older than 90 days. Called once per session (`cleanupMemoryOnce`, guarded by a session flag) on both boot and fresh sign-in.
- **Cold-start mitigation:** `GET /api/ping` → `{"status":"ok"}` (no auth). Frontend fires `fetch("/api/ping")` immediately on load, invisible, to warm the Render dyno before the first scan.

## Round 3 — Sofia Reyes (UX)

- **Calorie ring:** centered (fixed-width block, auto margins, centered parent); subtle `ringPulse` (scale 1.0→1.02, 3s infinite) when consumption is under 50%.
- **Scan result card:** `scanReveal` — springs up 40px, 300ms ease-out, on every scan.
- **Butler chat:** replies render **word-by-word** (`revealWords`, ~20ms/word, each word fades in); user messages stay instant.
- **Empty state:** "Nothing logged yet. Start with a scan." (Cormorant italic, centered, no icons).
- **Bottom nav:** active tab gets a tactile `navPop` (1.0→1.15→1.0, 150ms) only when newly activated.
- `prefers-reduced-motion` respected (pulse + word-fade disabled).

## Round 4 — Dr. Alex Kim (Health Psychology)

- **Post-scan micro-affirmation** (`maybeAffirm`): after a good meal (protein > 25 g, or total still within goal) the Butler says one ≤6-word line ("Good protein hit." / "That keeps you on track."). **Silent** when the meal pushes over budget — no hollow praise.
- **Streak** (`state.logStreak` / `lastLogDate`): consecutive logging days, incremented on every logged meal. Shown as a quiet "Day N" in Gold under the date. Initialized for existing users via the state migration on next load.
- **Morning greeting:** 6–10 AM, nothing logged yet → "Morning. Don't skip breakfast today." Fires once per morning via `butlerFired`.

## Round 5 — Lucas Brenner (Growth)

- **First screen is the Butler**, not a form (chat-driven onboarding).
- **Opening line:** "Before we start — I need to know a few things about you. This takes under two minutes."
- **Condensed the flow** (this was claimed in a prior report but never actually done in code — now real): age + height + weight + sex collapsed onto **one** vitals screen; activity + sleep + optional note collapsed onto **one** lifestyle screen. ~12 steps → 9. No data point dropped (the two old optional note prompts merged into one).
- **Progress bar:** thin Gold bar pinned at top, advances every step (`setStep`, 9 steps).
- **"Skip for now"** on every optional step (lifestyle note, goal detail, diet prefs, health notes) — visible, full-size.
- **Personalized closing line** by goal ("Let's get you there, [Name]. No noise, just results." / "Fuel the work, [Name]. We'll make every gram count.").
- **Dashboard opens with a waiting Butler bubble:** "All set. Scan your first meal when you're ready."

## Round 6 — Priya Sharma (Security)

- **RLS verified** (live SQL): `daily_logs`, `butler_memory`, `user_state` all have RLS enabled with policies scoped to `auth.uid() = user_id` for every operation. Confirmed predicates, not just presence.
- **Endpoint auth audit:** every `/api/*` route that touches user data requires a verified Supabase JWT. Only `/api/ping`, `/`, `/architectConfig.js`, `/supabaseConfig.js` are public — all intentional (`supabaseConfig.js` serves the **anon** key, which is public by design and gated by RLS).
- **Rate limiting:** per-user sliding window, 30 req/60 s, on all scan endpoints (`scan`) and Butler endpoints (`butler` — chat + check). Returns **429** "Slow down — try again in a moment."
- **architectConfig.js:** confirmed zero fabricated capabilities.
- **Final grep:** zero German in user-facing output, zero "Lumière/Lumiere", no secret literals in frontend.

---

## Deploy checklist

- [x] Maya — scan prompt rewritten, 5 test cases documented, temp 0.2
- [x] James — weather wired, `/api/ping` live, memory cleanup on login
- [x] Sofia — ring pulse, scan reveal, chat typewriter, nav pop, empty state (reduced-motion safe)
- [x] Alex — affirmation, streak (initialized for existing users via migration), morning greeting
- [x] Lucas — onboarding genuinely condensed to 9 steps, progress bar, skips, personalized finish
- [x] Priya — RLS verified, auth audit clean, rate limiting live, grep clean

---

## What was skipped / decided, and why

1. **Weather key via `os.getenv`, not `os.environ[...]`.** A missing key now degrades to "no weather" instead of crashing the whole app at boot. Spirit honored, footgun avoided.
2. **Sleep note merged into one field.** The condensed lifestyle screen has a single optional "anything I should know" note (was two). Sleep *quality* (the selector) is still captured; only the separate free-text sleep prompt folded in.
3. **`calories_range` is calories-only**, not per-macro range. Per-macro ranges would clutter the card and complicate tracking; a single calorie range communicates uncertainty cleanly. Per-item `confidence` covers all macros.
4. **Rate limiting is in-memory.** Correct for the single Render instance today; resets on restart and won't coordinate across multiple workers/instances. Move to Redis before horizontal scaling.
5. **Streak backfill is lazy.** Existing users get `logStreak`/`lastLogDate` initialized on their next login via the state migration, not via a server-side batch job. No data touched server-side.
6. **Pre-existing Supabase advisories untouched** (a `rls_auto_enable` SECURITY DEFINER function; Auth leaked-password protection off). Out of scope for this build; flagged for a future hardening pass.

---

## The single biggest remaining risk

**The "proactive" engine still only runs while the app is open in a browser tab.** Every behavioral win this round — morning breakfast nudge, weather hydration alert, streak, affirmations — depends on a `setInterval` inside a live tab. The moment the user closes the PWA, the Butler goes silent, and the entire "thinks ahead" positioning collapses to "thinks ahead, but only while you're already staring at it." Nothing here is wrong; it's just inert at exactly the moments behavior change actually happens (mid-morning, end of day, when you're *not* in the app).

**Until there is a native shell + push notifications, Nouri is a beautifully honest, fast, secure tracker — not yet the proactive companion the brand sells.** That is the one risk that outranks everything we shipped today.
