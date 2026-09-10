# ai_context.md — Math Exam Planner (MON, Bulgaria)

Purpose: brief this to any new AI session before making further changes.
Current version: v16. Single-file Streamlit app (`app.py`, ~940 lines).

## What this is
A Streamlit app for a Bulgarian math teacher. Reads:
1. Official MON yearly thematic distribution (`.docx`, one per grade, e.g.
   grade 7) — lists every lesson (numbered 1..180) with topic + lesson type.
2. Weekly schedule (`schedule_config.json`) — which weekday(s)/hours each
   class (7.А, 7.Б, 7.В, 7.Г...) has math.

Produces: a per-class calendar mapping every lesson to a real date,
respecting the official MON holiday calendar, with special handling for
"Контрол и оценка" (test/exam) lessons — main tab shows those with a
backup date. Teacher can view a month calendar, manually move lessons
(delays), and export.

## Key files (all in project root, zipped for user)
- `app.py` — everything (calendar logic, parsing, UI, export).
- `calendar_2026_2027.json` — official school-year calendar, sourced from
  MON's official order (published 31.08.2026), cross-checked against
  multiple BG news outlets. Loaded via `CALENDAR_FILE` constant at top of
  app.py. **Replace this file + constant each school year** — do not
  hardcode new calendars into app.py logic.
- `schedule_config.json` — user's weekly schedule, auto-saved by app.
- `lesson_plans/<class>.json` — persisted per-class plan. Format:
  `{"source_hash": "<md5>", "lessons": [{seq, topic, vid, theme, is_exam, date}, ...]}`.
  `source_hash` = md5 of `[(seq,topic,vid), ...]` from the parsed docx —
  used to detect when the user re-uploads an edited docx (see Sync below).
- `install.bat` / `run_app.bat` — Windows launchers. **Must use CRLF line
  endings** (cmd.exe bug: LF-only .bat files + chcp 65001 + Cyrillic can
  corrupt command parsing — this bit us once, see Known Gotchas).

## Core data model
A "topic"/"lesson" dict (from `parse_docx_distribution`):
`{seq, topic, hours=1, vid, theme, is_exam}`. `vid` = МОН lesson-type
label: "Нови знания" | "Упражнение" | "Преговор" | "Обобщение" |
"Практически дейности" | "Контрол и оценка". `is_exam` = True iff vid
contains "онтрол". One row = one 1-hour lesson (real docx numbers every
lesson individually, 180 total for grade 7).

Legacy fallback: old simple 2-col docx format ("Тема | Часове") is still
parsed (topics have no `vid`) but does NOT support the calendar/sync
features — only the flat table tab. Check `bool(topics[0].get("vid"))` to
detect which format is active.

## Scheduling algorithm
`simulate_plan(topics, cls_schedule, start_date, year_end, grade_str)`:
walks day-by-day from start_date, and for each school day (per
`is_school_day`) consumes that class's hours-per-day from `cls_schedule`,
assigning consecutive lessons to that date. 1 lesson = 1 hour slot.

`is_school_day(date, grade_str)`: False for weekends, official
vacations/single non-teaching days (from calendar JSON), and past that
grade's end-of-year date. Grade 12 has separate (shorter) spring break +
DZI dates — handled via `grade_str == "12"` branches in `load_calendar`
and `is_school_day`.

`_simulate_forward(topics_slice, start_date, ..., hours_used_on_start_date)`
= shared primitive: continues filling remaining hour-slots of start_date
first, then walks forward day by day. Used by both `reschedule_from`
(single-lesson delay -> cascade) and `sync_plan` (docx edit sync).

## Persistence + sync (added late, this is the trickiest part)
- `load_or_generate_plan(...)` returns `(plan, overflow, hash_mismatch)`.
  - If no saved plan exists (or `force_regenerate=True`): runs
    `simulate_plan` fresh and saves it.
  - If a saved plan exists and its `source_hash` matches the current
    docx's hash: returns it as-is (fast path, respects manual edits).
  - If a saved plan exists but hash differs (docx changed): returns the
    OLD plan UNCHANGED plus `hash_mismatch=True` — **does NOT
    auto-overwrite**, so manual edits are never silently lost.
- UI (Calendar tab) checks `hash_mismatch` and shows 3 buttons:
  "Sync" (`sync_plan`), "Reset to docx" (`force_regenerate=True`), or
  "Keep old / dismiss for this session" (session_state flag).
- `sync_plan(old_plan, new_topics, ...)`: finds the first index where
  old_plan[i] and new_topics[i] differ in (topic, vid); keeps all dates
  before that index; re-simulates everything from that index onward
  (anchored at the last-kept lesson's date, continuing same-day capacity
  first). Preserves manual edits made before the divergence point.

## Manual editing (Calendar tab)
- Select a lesson + new date -> "Приложи преместването".
- Validity check: warns (and disables Apply unless overridden via
  checkbox) if the target date has no class hour scheduled or is a
  vacation/non-teaching day for that grade.
- Toggle "auto-adjust" (default ON): ON = `reschedule_from` cascades all
  following lessons. OFF = only the selected lesson moves; if this causes
  more lessons on one day than `cls_schedule` capacity, shows a warning
  instead of blocking (lets teacher deliberately double up to catch up).
- Full table editor (`st.data_editor`) also available for direct date
  edits without cascading.
- Flash messages use `st.session_state[f"flash_{cls}"]` pattern (set
  before `st.rerun()`, consumed+displayed at top of tab on next run) —
  needed because `st.rerun()` discards anything printed in the same pass.

## Calendar rendering
`render_month_calendar` builds raw HTML (via `st.markdown(...,
unsafe_allow_html=True)`) — a month grid, one lesson badge per day
(emoji + color per `vid`, see `VID_BADGES`). Color convention (deliberately
chosen after user feedback — do not swap back):
- **Vacations/non-teaching days = RED** (`VACATION_BG = "#fde2e1"`,
  🔴 badge) — matches normal calendar-app conventions.
- **Tests/exams = GOLD/AMBER** (`EXAM_BG = "#fff3cd"`, 📝 badge,
  `"Контрол и оценка": ("📝", "#a15c00")`) — deliberately NOT red, to stay
  visually distinct from vacation days.
`render_calendar_legend()` renders the badge legend shown above the
calendar — keep in sync with `VID_BADGES` if lesson types change.

## Export (tab3)
CSV (existing) + Excel via `build_xlsx_bytes(df, cal_source)` — builds an
in-memory `.xlsx` with openpyxl (real date cells, per-class row coloring,
autofilter, frozen header, second sheet citing the calendar source).
Uses `st.download_button` with the xlsx mimetype, no disk write needed.

## Known gotchas (already fixed, don't reintroduce)
1. `use_container_width=True` is deprecated in current Streamlit — use
   `width='stretch'` instead everywhere.
2. `.bat` files MUST be saved with CRLF line endings, not LF. Verify with
   `data.count(b'\r\n') == data.count(b'\n')` before shipping.
3. Avoid non-ASCII (Cyrillic) as the literal last character before a
   newline in `.bat` files if possible (defensive; CRLF fix was the real
   root cause, but doesn't hurt to keep this in mind for `title` lines).
4. `parse_docx_distribution` must handle the REAL MON format: a table
   with columns [№ на урок, № на седмица, Тема, Вид на урочната единица,
   ...], section-divider rows where all cells are identical text, and
   lesson rows identified by `cells[0].isdigit()`. Don't assume the naive
   2-column "Тема | Часове" format is what users will upload.
5. `save_plan` signature requires `topics` (for hash) — every call site
   must pass it. If you add a new call site, don't forget this.

## Open items / possible future asks
- True drag-and-drop calendar (needs a 3rd-party component like
  `streamlit-calendar`; not verified in this sandbox — no internet
  access here to test it, so it was deliberately avoided in favor of a
  dependency-free select+date_input UI). Worth revisiting if the user
  wants it badly enough to test it themselves.
- Calendar/sync features only work with the new MON docx format
  (numbered lessons). Legacy format users are limited to the flat table
  tab — no calendar, no sync, no delay-cascade tooling.
- `calendar_2026_2027.json` is specific to school year 2026/2027 — next
  year needs a new JSON (same schema) + update `CALENDAR_FILE` constant.
