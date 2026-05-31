# Nouri — Design Review

**Panel:** Bongiorno (mobile) · Andersson (type) · Zhuo (states) · Fadell (first-run) · Ive (reduction)
**Scope:** `index.html` only. No frameworks, one file, palette + fonts preserved. Verdicts and direct implementations below.

---

## Round 1 — Bethany Bongiorno · *mobile / thumb zones*
**Verdict:** Good safe-area discipline already (env(safe-area-inset-bottom) on body, nav, hints). Tap targets were the weak point — several sat under 44px, and a key control was hover-only on touch.

Fixes:
- Header icon buttons **36→44px** (were below the 44px floor).
- `.scan-item-del` given a **44×44** hit area (was `0 4px` padding — a 12px target).
- `.ledger-del` **24→32px**, and **revealed at 55% opacity on touch devices** (`@media (hover:none)`) — it was hover-only, invisible/unusable on a phone.
- `.fav-del` **26→32px**.
- `.onb-choice` given **min-height:44px** (was ~36px) and bumped to 15px text.
- `.onb-mic` given a **44×44** target (was an 8px-padded icon).
**Left alone (with reason):** the primary **Scan Meal** button stays in the hero, not a floating FAB — adding a persistent floating control fights Ive's reduction pass, and a truly thumb-anchored quick-scan is exactly what a native home-screen action/widget solves (see closing note). It's full-width, high-contrast, and ≥44px tall.

## Round 2 — Rasmus Andersson · *typography*
**Verdict:** The type scale is mostly disciplined via CSS custom properties, but body leading was too loose and a few one-off sizes broke the scale.

Before → after:
- Body line-height **1.8 → 1.6** (Inter body should sit 1.5–1.6; 1.8 was airy to the point of disconnection).
- `.recipe-meta-cap` **10.5 → 11px**; `.fav-card-meta` **11.5 → 11px**; `.butler-hint-eyebrow` **9 → 10px** (no half-pixels, nothing below the 10px micro floor).
- `.onb-choice` **14px, .06em → 15px, 0** (Inter body wants neutral tracking).
- `.section-title` tracking **−0.01em → +0.01em** (Cormorant headlines read better slightly open).
- Removed a **curly-quote font bug** — the dashboard's "Ask Architect" link declared `font:… ‘Inter’…` with typographic quotes, so the family silently failed to apply. (The element was removed entirely in Round 5, which also resolves it.)
**Left alone:** the giant `.hero-display` keeps its **−0.012em** tracking — negative tracking is correct for display-size italic and is the brand's signature; the "slightly positive" rule applies to mid-size headlines, not 48–72px display.

## Round 3 — Julie Zhuo · *edge & empty states*
**Verdict:** The happy path was polished; the failure paths silently dumped users with zero explanation. This was the biggest gap in the app.

Fixes:
- **Scan failure** no longer calls `renderDashboard()` silently. New `renderScanError()` — a calm, on-brand screen ("Let's try that again." + a Butler-voice line) with **Take another photo** / **Back to dashboard**. It detects offline and says so plainly.
- **Fridge scan failure** now shows an inline calm message (offline-aware) instead of silently repainting an empty list.
- **Recipe generation failure** now shows a calm retry message instead of a blank slot.
- **Recipe with no plan** — `makeRecipe()` referenced `state.targets.calories` and would throw for a user who skipped plan-building; now uses `effectiveCalTarget()`.
- **Empty log** confirmed: Cormorant-italic "Nothing logged yet. Start with a scan." renders correctly.
- **Zero calories** confirmed: ring shows full *remaining*, 0% fill, gentle pulse — no break.
- **Long names** confirmed: ledger ellipsis + tap-to-expand tooltip working.
- German placeholder + a German `aria-label="Schließen"` translated to English (user/screen-reader facing).
**Left alone:** profile panel for a skip-heavy new user already degrades gracefully ("Optional" / "Calculated") — nothing to fix.

## Round 4 — Tony Fadell · *the first 90 seconds*
**Verdict:** Onboarding flow and pacing were strong, but the opening line sold nothing — it could have been any tracker. The dashboard sub-line was generic.

Fixes:
- **Onboarding opener rewritten** to state the difference immediately, in the Butler's voice:
  > "Most apps wait for you to log, then add it up. I work the other way — I look ahead and say something before the day gets away from you."
  This is the MyFitnessPal contrast, said in one breath, before any form field.
- **Dashboard sub-line:** "Scan a meal or your fridge — we take care of the rest." → **"Scan a meal. I'll handle the math — and tell you what matters."** (active, first-person, promises the *thinking*, not the *tallying*).
- **Chat opening** now greets by name and references the day ("I have today's numbers in front of me…") so the very first message feels like a companion who already knows you, not an empty box.
**Confirmed working:** the post-onboarding waiting bubble ("All set. Scan your first meal when you're ready.") and the full-width gold Scan button as the obvious first action.

## Round 5 — Jony Ive · *reduction*
**Verdict:** Two navigation systems ran at once and several screens had competing primaries. The fridge screen was the one room that didn't feel like the rest of the house.

Removed:
- **Duplicate navigation.** The header carried Home + Profile icon buttons *and* the bottom bar carried Home/Butler/Profile. Removed the header icons entirely — the header is now a quiet brand mark; navigation lives in one place.
- **"Ask Architect →"** link in the dashboard hero — the bottom-nav Butler tab already does this. Gone.
- **"Dashboard" button** at the bottom of the chat — bottom-nav Home covers it. Gone.
- **"% consumed"** line under the ring — the ring fill shows proportion and the line below states "X of Y kcal today." Redundant. Gone (plus its dead CSS).
- **"Performance Intelligence Core"** eyebrow on the chat header — corporate jargon that states nothing. Gone; the header is now just the Butler's name + mode.

Redesigned — **the fridge / Sous-Chef screen:**
- Was: a borrowed onboarding title, a centered row of **three equal buttons** (Scan / Add / Inspiration) and a second row of three more (Back / Clear / Create) — six competing actions.
- Now: an editorial header matching the app (eyebrow + italic Cormorant "Your kitchen."), and **one primary action at a time** — *Scan Fridge* when the basket is empty, *Create Recipe* once it has contents. Everything else (Add by hand, Scan more, Inspiration, Clear, Back) demoted to quiet underline-on-hover links. German placeholder fixed. It now reads like a suite, not a control panel.
**Butler chat — companion vs. form:** softened by dropping the corporate eyebrow, greeting by name, and removing the redundant exit button so the screen is just *conversation* — header, thread, input.
**Left alone:** the calorie ring, the Ledger/Journal, and the charcoal hero curtain — these are the app's strongest, most "Aman" moments and needed nothing removed.

---

## Sign-off
- Bethany — thumb zones & tap targets verified ✓ (all interactive elements ≥44px; touch-reachable delete)
- Rasmus — type scale consistent, leading corrected, no half-pixels, font bug killed ✓
- Julie — every failure path now has a calm, explained state; no silent dumps ✓
- Tony — first line communicates the difference; dashboard & chat feel useful on arrival ✓
- Jony — one navigation system, one primary per screen, fridge redesigned, dead UI removed ✓

## What was intentionally left alone
- **Display-italic tracking, the ring, the Ledger, the charcoal hero** — already at the quality bar; reduction means *not* touching them.
- **The two scan-related `isProUser()` guards** — harmless today (gate returns true pre-Stripe); they're scaffolding for the paywall, not dead UI.
- **The word-by-word typewriter** — kept; it reads as alive and is disabled under `prefers-reduced-motion`.

## The one weakness only a native app can solve
**The most-reachable real estate on a tall iPhone — the bottom thumb arc — can hold navigation or a primary action, but not both, inside a web page.** We chose navigation there (correct for a browser tab). But the single highest-value action, *scan*, therefore lives in the hero, which on a 6.7″ screen is a reach. The right answer isn't a floating button bolted into the page — it's a **native Home-Screen quick action, a Lock-Screen/Control-Center widget, and a share-sheet "scan this" target** that put the camera one thumb-tap away from anywhere, even with the app closed. That — and the push that makes the Butler's "look ahead" promise work when the app isn't open — is the ceiling a single `index.html` cannot break through.
