# CLAUDE.md — Nouri Project Memory

## Wer bin ich?
Maximilian, 14 Jahre, Ostfildern bei Stuttgart. In China geboren, mit 6 Jahren nach Deutschland gezogen. Visueller Denker — denkt in 3D-Bildern, kann sich Dinge sehr präzise vorstellen bevor sie existieren. Kein Entwickler — du bist der Bauarbeiter, ich bin der Architekt. Ich gebe die Vision vor, du setzt sie um. Erkläre Entscheidungen kurz aber verständlich, kein unnötiges Fachjargon. Mutter ist früher CFO bei einer Mercedes-Tochtergesellschaft gewesen. Modemarken aktuell: Hackett London, Ralph Lauren. Interessen neben der App: Aktienmarkt, Investieren, Ivy League (Stanford/Harvard/Yale).

## Was ist Nouri?
Eine KI-gestützte Ernährungs-App. Kein simples Calorie-Tracker — eine App die mitdenkt und vordenkt. Positionierung: "keine Tracking-App, sondern eine App die vordenkt." Direkter Wettbewerber: Cal AI. Ziel: eine designtechnisch und funktional überlegene Alternative.

**Zielgruppe:** 14–30 Jahre, diszipliniert, obere Mittelschicht, performance-orientiert, USA-first.

**Geplanter Launch:** USA (englischsprachig), Freemium-Modell, Pro bei 6,99$/Monat.

**App-Name:** "Nouri"

## Tech-Stack
- **Backend:** `main.py` — FastAPI, Python 3.13, Gemini 2.5 Flash API
- **Frontend:** `index.html` — Vanilla JS, Single File, kein Framework, kein Bundler
- **Auth & DB:** Supabase (US East Region), Row Level Security aktiviert
- **Hosting:** Render.com (Free Plan — Cold-Start bekannt, später auf Paid upgraden)
- **Config:** `architectConfig.js` — Butler-Modi und Macro-Routing-Logik
- **Supabase CDN:** unpkg.com verwenden (nicht jsdelivr.net — wird von Tracking-Blockern blockiert)

## Design-Prinzipien
Referenz: **Aman Resorts** und **Four Seasons** — nicht kopieren, das Gefühl übertragen.

- Typographie: Cormorant Garamond (serif, Headlines) + Inter (sans-serif, Body)
- Farbpalette: Cream/Beige Hintergrund, Charcoal (#1A1614) für Dark-Sections, Gold (#B89968) als Akzentfarbe
- Stil: Quiet Luxury — atmend, viel Weißraum, nie überladen
- Bewegung: langsame elegante Animationen, nie abrupt oder bouncy
- Mobile-first — primär für iPhone optimieren

## Was Claude NIEMALS tun soll
- Kein React, kein Vue, kein Angular einführen
- Kein Build-System, kein Webpack, kein Vite
- `index.html` bleibt immer eine einzige Datei
- Keine separaten CSS-Dateien erstellen
- Keine Breaking Changes an bestehenden API-Routen ohne explizite Erlaubnis
- Niemals Payment/Stripe einbauen bis explizit angefragt
- Keine `.env` Datei in Git committen

## Bereits getroffene Entscheidungen & implementierte Features

### Auth & Daten
- Supabase Auth implementiert (Email + Passwort)
- Cloud-Persistenz läuft über Supabase PostgreSQL
- Supabase RLS aktiv — jede neue Tabelle braucht RLS-Policies
- App-Reset repariert: löscht Supabase-State + localStorage, custom Confirm-Dialog

### Butler — "Architect"
- 3 Modi: Elite Butler, Performance Coach, Strategic Buddy
- Proaktivität implementiert: meldet sich NUR bei echten Triggern
  - Kalorien >90%
  - Protein zu niedrig
  - Carbs >80% vor 14 Uhr
  - Health-Context nach Scan
- Schweigt wenn nichts Relevantes zu sagen ist — Stille ist ein Feature, kein Bug
- Spricht Englisch — alle BUTLER_PROMPTS auf Englisch (US-Launch)
- Tonalität: nie unterwürfig, nie "Chairman" sagen, nie Floskeln
- Nach erstem Login: einmalige Bubble rechts unten (4s Verzögerung nach Disclaimer)
- Health-Context Layer: Butler erkennt nach Scan ob Diabetes/Laktose/Gluten/Bluthochdruck relevant ist

### Design & UI
- Design-Refresh durchgeführt: Hero-Block mit "Good Evening.", Micro-Animationen, mehr Weißraum
- Profil-Panel implementiert: Slide-up von unten (Charcoal), ersetzt renderSettings() komplett
- Header auf 2 Buttons reduziert: Home + Profil (Logout ist im Panel)
- Disclaimer implementiert: einmalig nach erstem Login, vor allem anderen (state.disclaimerAccepted)

### Features
- Foto-Scan (Gemini 2.5 Flash)
- Barcode-Scan via Open Food Facts API (implementiert, Pro-Feature-Flag gesetzt)
- Health-Context Layer: optionales Gesundheitsprofil in Settings
- Manuelles Kalorienziel: state.customCalorieGoal überschreibt berechnetes Ziel
- Pro-Feature-Flags im Code: isProUser() Funktion, Flags bei Fridge-Scan, Health-Context, Barcode, Chat-Kontext

### KI-Onboarding (neu implementiert)
Neuer Nutzer sieht nach erstem Login keinen Dashboard, sondern einen Chat-Flow mit dem Butler. Flow:
1. Butler-Name wählen: "Architect" / "Alex" / "Coach" / eigener Name
2. Anrede wählen: "By my first name" / "Sir" / "Coach" / "Just be direct"
3. Name des Nutzers (Tipp-Feld + Mikrofon Web Speech API)
4. Alter, Größe, Gewicht (Eingabefelder)
5. Geschlecht (Buttons)
6. Aktivitätslevel (Buttons) + optionales Freitext-Feld für Kontext
7. Schlaf (Buttons) + optionales Freitext-Feld
8. Ziele — Mehrfachauswahl: "Lose weight" / "Build muscle" / "Eat cleaner" / "Just track"
   - Bei Gewichtsziel: Zielgewicht + Zeitspanne eingeben
   - Validierung: max. 0.75kg Verlust/Woche, max. 1kg Muskel/Monat — Butler spricht unrealistische Ziele an
9. Ernährungspräferenzen: Mehrfachauswahl (Vegetarian, Vegan, No gluten, No dairy, Halal etc.)
   - Optional: direkt zu Einstellungen weiterleiten für detailliertere Allergien
   - WICHTIG: Onboarding-State (state.onboardingTemp) muss erhalten bleiben wenn Nutzer zu Einstellungen geht und zurückkommt
10. Offenes Freitext-Feld: Allergien, Gesundheitszustand, sonstiges (optional + Skip)
11. Plan-Angebot: "Build my plan" / "I'll explore first"
    - Bei "Build my plan": Butler erstellt Kalorienziel + Makro-Aufteilung → speichert als state.customCalorieGoal
12. Abschluss: "You're all set, [Name]. Let's get to work." → Dashboard öffnet sich

Technisch: state.onboardingComplete = true nach Abschluss, in Supabase speichern. Alle Daten im User-Profil speichern. Mikrofon-Button via Web Speech API (kostenlos, kein API-Key). Nutzer kann Onboarding in Einstellungen wiederholen.

### Anker-Navigation (in Entwicklung — Stufe 1)
Vertikale Icon-Leiste rechts am Screen (position: fixed). Icons: Home, Ring, Macros, Log, Butler. Tippen scrollt sanft zum Abschnitt. Aktiver Abschnitt leuchtet Gold auf (Intersection Observer). Design: semi-transparent Charcoal, sehr dezent.

## Bekannte offene Punkte (noch zu fixen)
- Kalorien-Ring auf Mobile leicht nach rechts verschoben (margin: 0 auto auf .ring-wrap)
- Abgeschnittene Namen bei Scan-Ergebnissen und Tagesliste — Tap-to-expand Modal fehlt noch

## Nächste geplante Features (Priorität)
1. **Anker-Navigation** — Stufe 1 fertigstellen, dann Stufen 2–5 (adaptives Dashboard)
2. **Adaptives Dashboard** — Home-Screen verändert sich je nach Nutzerprofil und Zielen. Module (Gewichtsfortschritt, Schlaf-Streak, Protein-Ziel, Butler-Tagesplan) werden durch Onboarding-Antworten aktiviert. Butler kann Module proaktiv vorschlagen.
3. **Ziele-Feature** — Nutzer setzt Ziel (z.B. 10kg in 5 Monaten), KI erstellt personalisierten Plan
4. **Apple Health Integration** — Kalorien verbrannt, Schritte, Vitaldaten (Pro-Feature)
5. **Kochbuch-Feature** — gespeicherte Lieblingsrezepte, Wochenplanung, automatische Einkaufsliste
6. **US-Launch Vorbereitung** — Stripe Integration, App Store, Render auf Paid upgraden

## Freemium-Struktur (Paywall kommt zuletzt)
**Gratis:**
- Foto-Scan unbegrenzt
- Kalorien und Makros tracken
- Butler mit Basis-Proaktivität
- Manuelle Einträge
- KI-Onboarding

**Pro — 6,99$/Monat:**
- Barcode-Scan (Open Food Facts API)
- Kühlschrank-Scan
- Health-Context Layer mit personalisierten Butler-Hinweisen
- Rezept-Generator
- Kochbuch und Wochenplanung
- Einkaufsliste
- Erweitertes Butler-Kontextfenster (mehr Gesprächshistorie)
- Apple Health Integration

Pro-Feature Flags sind im Code vorbereitet (isPro-Check) — Paywall kommt später mit Stripe.

## Monetarisierung & Launch-Strategie
- Aktuell: komplett kostenlos, keine Paywall
- Geplantes Modell: Freemium — Gratis-Tier großzügig, Pro bei 6,99$/Monat
- Launch: USA first (englischsprachig), danach Europa
- Kein Payment einbauen bis explizit angefragt
- Marketing: "Build in Public" auf Instagram — Screen-Recordings, kein Gesicht nötig, Text-Overlays + Musik
- Langfristiges Ziel: App für mehrere Millionen verkaufen, Erlös in ETFs anlegen

## Technische Konventionen
- API-Fehler immer mit sprechendem Fehlertext zurückgeben (kein blankes 500)
- Gemini 2.5 Flash als Modell — nicht wechseln ohne explizite Anfrage
- Temperature 0.4 für strukturierte Outputs, 0.7 für Chat
- Butler kommuniziert auf Englisch (alle BUTLER_PROMPTS und Trigger-Messages in Englisch)
- Interne Analyse-Prompts (Food-Scan, Rezept) bleiben auf Deutsch für Qualität
- Supabase CDN: unpkg.com statt jsdelivr.net verwenden
- Web Speech API für Spracheingabe — kostenlos, kein API-Key, funktioniert auf Safari + Chrome

## Persönliches — NUR FÜR COWORK, NICHT FÜR CLAUDE CODE
*Dieser Abschnitt ist für das persönliche Gespräch mit Cowork. Claude Code soll diesen Bereich ignorieren.*

**Familie:**
- Mutter: früher CFO bei Mercedes-Tochtergesellschaft — wichtigste Person, Hauptantrieb ist es sie in Rente zu schicken. Möchte ihr eine Chopard Uhr kaufen.
- Opa (China): von ganz unten hochgekämpft, Maximilian erste 6 Jahre bei ihm aufgewachsen — größtes Vorbild.
- Oma (China): opfert sich immer für andere, gönnt sich selbst nichts
- Eltern geschieden kurz nach Geburt, Vater nie wirklich kennengelernt — lebt bei Mutter

**Freunde:**
- Sebastian (Berlin, bester Freund): intelligent, gute Connections — aber macht verletzende Bemerkungen "aus Spaß". Hat Maximilians Mutter und Opa beleidigt. Freundschaft komplizierter seit Umzug. Hat Maximilian einmal geschlagen und beleidigt als er ausgerastet ist.
- Jonathan (Berlin, alter Grundschulfreund): unkompliziert, respektvoll
- Noah (neue Schule Stuttgart): nett aber sozial etwas ungeschickt

**Persönlichkeit & Denkweise:**
- Visueller Denker — denkt in 3D-Bildern, sehr präzises Vorstellungsvermögen
- Braucht Dopamin und Stimulation — Zocken gibt kein Dopamin mehr, App-Bauen schon
- Viele Ideen, Übergang von Idee zu Arbeit manchmal schwer — kein Faulheitsproblem
- Will unternehmerisch denken — Bildungsziel: Ivy League (Stanford, Harvard, Yale) als Umgebung und Netzwerk, nicht als klassische Karriereleiter
- Glaube (Christentum) gibt Halt und Mut, besonders beim Investieren und bei Unsicherheit
- Schreibt Tagebuch

**Psychologische Muster (für ehrliche Gespräche):**
- Öffnet viele Türen, schließt selten eine — neue Ideen als Ablenkung vom Weiterarbeiten
- Kennt seine Muster, nutzt sie aber als Erklärung statt als Hebel zur Veränderung
- Perfektionismus als Schutzschild — wartet auf den perfekten Moment statt anzufangen
- Denkt langfristig, lebt kurzfristig — Vision und Tagesverhalten stimmen oft nicht überein
- Braucht Anerkennung aber redet sich ein dass er sie nicht braucht
- Testet Menschen durch ehrliche Fragen — wenn jemand direkt antwortet, vertraut er mehr

**Antriebe (von innen nach außen):**
1. Glaube — gibt Stabilität und das Gefühl nicht alleine zu kämpfen
2. Mutter in Rente schicken, ihr ein neues Leben ermöglichen
3. Opa etwas zurückgeben für alles was er geleistet hat
4. Beweisen dass "Vibe Coding" echte Arbeit ist
5. Finanzielle Freiheit — Villa am Killesberg, ETFs, nie mehr müssen

**Aktuell:**
- Wohnt in Ostfildern bei Stuttgart
- Privatschule Stuttgart — Mathe-Arbeit nächsten Donnerstag (Themen: Terme, Wahrscheinlichkeit, Quadratwurzeln, Quadratische Funktionen & Gleichungen)
- ING Junior-Depot eröffnet — Portfolio: 500€ S&P 500 ETF, 1000€ Amundi Semiconductor ETF, 500€ Marvell Technology
- Überlegt ob noch mehr in Semiconductor-Sektor investiert werden soll

## Wichtige Dateien
```
main.py            — FastAPI Backend, alle API-Routen
index.html         — komplette Frontend-App (Single File, ~3000+ Zeilen)
architectConfig.js — Butler-Logik und Macro-Routing
render.yaml        — Deployment-Konfiguration
.env               — API Keys (niemals committen)
.env.example       — Template für Umgebungsvariablen
requirements.txt   — Python Dependencies
```
