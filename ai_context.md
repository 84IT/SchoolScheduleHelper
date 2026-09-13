# ai_context.md — Math Exam Planner (MON, Bulgaria)

Purpose: brief a new AI session before further changes.
Version: v26. Single-file Streamlit app (`app.py`, ~1345 lines) +
`launcher.py` for the exe build.

## What this is
Streamlit app for a Bulgarian math teacher. Reads:
1. Official yearly distributions (`.docx`, one per grade and subject) — files
  are `<grade>_<subject>.docx`; legacy `<grade>.docx` means `MAT`.
  MAT uses the MON numbered lesson table. ИУЧ uses the six-column
  `№ по ред | Учебна седмица | Тема | Очакван резултат | ...` table.
2. Weekly schedule (`schedule_config.json`) — hours and subject per weekday
  for each class.

Produces a per-class calendar mapping every lesson to a real date,
respecting the official MON holiday calendar. "Контрол и оценка" (test)
lessons get a backup date too. Teacher can view a month calendar,
move/swap/compact/undo lessons, and export to CSV/Excel.

## Key files (project root, zipped for user)
- `app.py` — everything.
- `calendar_2026_2027.json` — official school-year calendar (MON order,
  published 31.08.2026, cross-checked against multiple BG outlets).
  Loaded via `CALENDAR_FILE` constant. **Replace file + constant each
  school year** — don't hardcode new calendars into app.py logic.
- `custom_days_off.json` — ad-hoc non-teaching days added by the user
  (e.g. election days), global across all classes/grades.
- `schedule_config.json` — user's weekly schedule, auto-saved.
- `lesson_plans/<class>.json` — persisted per-class plan:
  `{"source_hash", "topics_hash", "lessons": [{seq, topic, vid, theme,
  is_exam, date}, ...]}`. `source_hash` = combined docx+calendar
  signature (see Persistence below); `topics_hash` = docx-only, used to
  tell the two apart when diagnosing a mismatch.
- `lesson_plans/<class>.undo.json` — up to 5 prior full-plan snapshots
  for the Undo feature (see below).
- `install.bat` / `run_app.bat` — Windows launchers. **Must be CRLF**
  (cmd.exe + chcp 65001 + LF-only files corrupts command parsing — hit
  this twice already).
- `launcher.py` / `build_exe.bat` — PyInstaller entry point and build
  script for a portable `.exe` (v25). See "Portable .exe build" below
  before touching either.

## Core data model
Lesson dict: `{seq, topic, hours=1, vid, theme, is_exam, subject}`;
ИУЧ may also contain `expected_result`. `subject` is the normalized short
label (`MAT`, `ИУЧ`, etc.). One row = one 1-hour lesson.

Legacy fallback: old 2-col docx ("Тема | Часове") still parses (no
`vid`) but only feeds the flat table tab — no calendar, sync, undo, or
any of the tooling below. Check `bool(topics[0].get("vid"))` to detect
format.

## Scheduling primitives
- `is_school_day(date, grade_str=None)`: False for weekends, `CUSTOM_DAYS`,
  official calendar vacations/single days, and (if grade_str given) past
  that grade's end-of-year. Grade 12 has its own shorter spring break +
  DZI dates. `grade_str=None` skips the grade-cutoff check only — still
  respects everything else.
- `_simulate_forward(topics_slice, start_date, cls_schedule, grade_str,
  year_end, hours_used_on_start_date=0)`: THE shared walk-forward
  primitive. Fills remaining hour-slots of start_date first, then walks
  day by day. **On overflow (doesn't fit before year_end), it does NOT
  drop lessons** — continues placing them past year_end (calling
  `is_school_day(cur, None)`, ignoring only the grade cutoff) so the
  output length always equals the input length; `overflow=True` still
  signals the "doesn't fit" warning. (This silent-drop was a real bug,
  fixed once already — don't reintroduce it.)
- `cls_schedule["_subjects"][weekday]` stores the subject for each slot;
  never fill a slot with a different subject. Skip non-matching slots.
- `simulate_plan(topics, ...)` = `_simulate_forward(topics, start_date,
  ..., hours_used_on_start_date=0)` — the "from scratch" case.
- `_resync_from_index(plan, idx, cls_schedule, grade_str, year_end,
  start_date)`: 0-based idx. Keeps `plan[:idx]` untouched, re-simulates
  `plan[idx:]` from the earliest available slot after `plan[idx-1]`'s
  date (or from `start_date` if idx=0). Shared by `reschedule_from`
  (single-lesson move + cascade), `resync_from_invalid` (auto-shift),
  and `compact_from` (manual compact) — all three just pick `idx`
  differently.

## Persistence + change detection
- `compute_plan_signature(topics)` = `compute_topics_hash(topics) + ":" +
  `compute_calendar_signature()`. Hashes include lesson subject, class
  subject/weekday schedule, official/custom calendar, and planning start
  date. Any of these changes invalidates the cache.
- `load_or_generate_plan(cls, topics, cls_schedule, start_date, year_end,
  grade_str, force_regenerate=False, action_desc=None)` → `(plan,
  overflow, hash_mismatch)`. No saved plan → generate + save. Saved plan
  + signature matches → return as-is. Saved plan + signature differs →
  return the OLD plan UNCHANGED + `hash_mismatch=True` (never
  auto-overwrites, so manual edits are never silently lost).
- Calendar tab banner on `hash_mismatch=True` distinguishes causes and
  offers only relevant actions:
  - `docx_changed` (stored `topics_hash` != current) → "🔄
    Синхронизирай" (`sync_plan`: diffs old vs new topic/vid position by
    position, keeps dates before first divergence, re-simulates after).
  - `find_invalid_lessons(plan, cls_schedule, grade)` non-empty (lessons
    sitting on now-invalid dates, e.g. new custom day) → "🔄
    Автопремести засегнатите" (`resync_from_invalid`: anchors at first
    invalid lesson) or "✋ Ще ги преместя ръчно" (dismiss; lesson numbers
    are listed in the warning text).
  - No invalid lessons but plan differs from a fresh simulation (gap
    reclaimed, e.g. custom day removed) → "🗜️ Уплътни освободеното
    място" (`compact_from` at `suggest_compact_anchor`'s suggestion).
  - Always available: "♻️ Нулирай по .docx" (full regenerate) and "➡️
    Продължи както си е" (dismiss this session).
  - Conditions aren't mutually exclusive; all applicable buttons render
    together.
  - Tab2 (flat table) also shows a small `st.info` per class when
    `hash_mismatch=True`, pointing the user to the Calendar tab. It
    checks the SAME `st.session_state[f"dismiss_mismatch_{cls}"]` flag
    as the Calendar tab's dismiss button, so dismissing in one place
    silences both — this wasn't true originally (tab2's info ignored
    the dismiss flag and nagged forever even after the user dismissed
    in Calendar), fixed after user noticed the inconsistency. If you add
    another mismatch-dependent message anywhere, check the same flag.

## Manual editing tools (Calendar tab)
- **Move**: pick lesson + new date → validity-checked (warns + requires
  override checkbox if target isn't a real school day for that class) →
  toggle "auto-adjust": ON cascades via `reschedule_from`, OFF moves
  only that lesson and warns if the target day now exceeds its hour
  capacity (lets teacher deliberately double up to catch up).
- **Swap**: pick two lessons, swap just their `date` fields directly —
  no re-simulation needed (always capacity-safe, day-occupancy counts
  don't change). For "postpone a test, swap with a normal lesson"
  without two separate cascading moves.
- **Compact** (`compact_from`, manual anchor via dropdown): mirror of
  auto-shift — pulls a chosen lesson (and everything after) back to the
  earliest available dates, for reclaiming gaps left by a removed
  vacation/custom day. `suggest_compact_anchor` pre-fills a default by
  diffing current plan against a from-scratch simulation and returning
  the first difference — **heuristic, not authoritative**: it can't
  distinguish "gap from a calendar change" from "lesson I deliberately
  moved for unrelated reasons," so it may suggest an earlier manual edit
  instead of the actual gap. It's just a dropdown default; don't auto-
  apply it without the user confirming/choosing the anchor.
- **Undo** (preferred over Compact when reverting a specific recent
  action — exact, not heuristic): `save_plan(cls, plan, topics,
  action_desc=None)` — when `action_desc` given, calls
  `push_undo_snapshot(cls, action_desc)` first, which copies the
  CURRENT on-disk plan file verbatim into `lesson_plans/<cls>.undo.json`
  (capped at `UNDO_MAX=5`, FIFO). `action_desc=None` (default) = don't
  snapshot — used only for first-ever plan generation. Every real
  mutation site passes a human-readable description (see list below) —
  **any new mutation site must pass one too, or it becomes silently
  non-undoable**. `undo_last_change(cls)` pops the last snapshot and
  writes it back byte-for-byte (not recomputed) — confirmed by test to
  exactly restore the prior plan including its embedded hash, so
  `hash_mismatch` correctly resolves after undoing a change whose
  triggering calendar edit has also been reverted. UI: small "↩️
  Последна промяна: <desc> (<time>) [Отмени]" row at the top of the
  Calendar tab, shown whenever `peek_undo(cls)` returns something.
  Action descriptions in use: auto-shift, banner-compact, sync,
  reset (banner + standalone button), move-with-cascade,
  move-without-cascade, tool-compact, swap, table-editor-edit.
- **Full table editor** (`st.data_editor`): direct per-row date edits,
  no cascading, saved with its own undo entry.
- Flash messages: `st.session_state[f"flash_{cls}"] = (level, msg)` set
  before `st.rerun()`, consumed+shown at top of tab on the next run
  (needed because `st.rerun()` discards same-pass output).

## Custom (ad-hoc) non-teaching days
`CUSTOM_DAYS_FILE = "custom_days_off.json"` — global dict `{date:
reason}` (elections etc.), managed via a sidebar form (add + 🗑️ delete
per entry), sidebar section "🗳️ 3. Извънредни неучебни дни". Checked
first in both `is_school_day()` and `vacation_reason()`. Applies to ALL
classes/grades uniformly.

## Start date
Official school-year start is `CAL["start"]` (15 September for 2026/27).
`planning_start_date` defaults to 16 September because no lessons are placed
on the opening day. It is session-persisted and included in the plan hash.

## Calendar rendering
`render_month_calendar` builds raw HTML via `st.markdown(...,
unsafe_allow_html=True)`. Color convention (deliberate, from user
feedback — don't revert):
- **Vacations/non-teaching = RED** (`VACATION_BG = "#fde2e1"`, 🔴).
- **Tests/exams = GOLD/AMBER** (`EXAM_BG = "#fff3cd"`, 📝,
  `"#a15c00"`) — deliberately not red, stays distinct from vacations.
- **Weekend cells** = `"#d9d9d9"` (darkened from a near-invisible
  `#f4f4f4` after feedback — don't lighten back).
- Lesson-type badges (`VID_BADGES`): 🆕 Нови знания, ✏️ Упражнение,
  🔁 Преговор, 📚 Обобщение, 🛠️ Практически дейности, 📝 Контрол и
  оценка. `render_calendar_legend()` shows these above the calendar —
  keep in sync with `VID_BADGES`.

## Export (tab3)
CSV + `build_xlsx_bytes(df, cal_source)` — in-memory `.xlsx` via
openpyxl (real date cells, per-class row coloring, autofilter, frozen
header, second sheet citing calendar source). `st.download_button`, no
disk write.

## Portable .exe build (v25, path bug fixed in v26)
User wanted the project to be a standalone portable app. Streamlit +
PyInstaller has real, non-obvious failure modes if done naively — don't
regress these if asked to touch the build again:
- **`--onedir`, NEVER `--onefile`.** `--onefile` extracts to a temp dir
  (`sys._MEIPASS`) that's deleted when the process exits — this app
  persists state to disk (`schedule_config.json`, `lesson_plans/`,
  `custom_days_off.json`), so onefile would silently lose all of it
  between runs. `--onedir` keeps a real, permanent folder — correct
  choice for anything that writes its own config/data.
- **v25 attempt (WRONG, superseded): `BASE_DIR =
  os.path.dirname(os.path.abspath(__file__))`.** This persisted data
  correctly (confirmed by user) but wrote it into the hidden
  `_internal/` subfolder that PyInstaller onedir builds place bundled
  data in — because `__file__` for a script Streamlit dynamically
  `exec`s resolves to wherever the bundled `app.py` copy physically
  sits (`_internal/app.py`), not the folder containing the `.exe`. User
  couldn't find their data — reasonably, since `_internal` reads as
  "don't touch, implementation detail."
- **v26 fix (current): `BASE_DIR` uses `sys.frozen`/`sys.executable`
  instead of `__file__` when frozen:**
  ```python
  if getattr(sys, "frozen", False):
      BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
  else:
      BASE_DIR = os.path.dirname(os.path.abspath(__file__))
  ```
  `sys.frozen`/`sys.executable` are process-level attributes set by the
  PyInstaller bootloader — reliable regardless of how Streamlit executes
  the script internally, unlike `__file__` which depends on Streamlit's
  own script-running internals. For onedir, `sys.executable` = the path
  to the actual `.exe`, so `BASE_DIR` = the folder the user actually
  sees and opens. Verified via a simulated frozen environment (faked
  `sys.frozen`/`sys.executable`/`sys._MEIPASS` pointing at a fake
  `_internal` layout) — `BASE_DIR` resolves next to the fake exe, and
  the plain non-frozen path still resolves to the script's own folder,
  unchanged.
- **`_seed_bundled_file(filename)`**: on first frozen run, if the target
  file doesn't already exist in `BASE_DIR`, copies it there from
  `sys._MEIPASS` (where `--add-data` actually placed it). Used for
  `CALENDAR_FILE` specifically — makes `calendar_2026_2027.json` appear
  next to the `.exe` on first launch (so the user can find/replace it
  next school year), rather than staying buried in `_internal` forever.
  Verified by test: after simulating a first run, the calendar file
  lands next to the fake exe as expected. Not needed for
  `CUSTOM_DAYS_FILE`/`CONFIG_FILE`/`PLANS_DIR` — those have no bundled
  "factory" version, the app creates them fresh regardless of mode.
- **If a user already built an exe under the v25 scheme**: their data
  isn't lost, just sitting in `<exe folder>/_internal/` — tell them to
  copy it out (or just accept a fresh start) after rebuilding with
  `build_exe.bat` from this version.
- **All of app.py's persisted-file constants** (`CALENDAR_FILE`,
  `CUSTOM_DAYS_FILE`, `CONFIG_FILE`, `RAZPREDELENIA_DIR`, `PLANS_DIR`)
  are `os.path.join(BASE_DIR, ...)`. **Any new persisted-file path added
  to app.py must follow the same pattern** — a bare relative string is a
  regression waiting to happen for this specific app, now doubly so
  since there are two different correct BASE_DIR computations depending
  on frozen state.
- Streamlit dynamically `exec`s the target script rather than being
  statically imported, so PyInstaller's dependency analysis can't see
  app.py's own imports (`docx`, `pandas`, `openpyxl`). The build command
  must explicitly `--collect-all` each of them (plus `streamlit` itself)
  and `--add-data` both `app.py` and `calendar_2026_2027.json` into the
  bundle root — otherwise the frozen exe fails at runtime with import
  errors that don't reproduce when just running the .py directly.
- `launcher.py` is the actual PyInstaller entry point (not `app.py`) —
  it calls `streamlit.web.cli.main()` programmatically with `sys.argv`
  set to simulate `streamlit run <bundled app.py path>`. Its own
  `resource_path()` helper (separate from app.py's `BASE_DIR`/
  `_seed_bundled_file`) resolves via `sys._MEIPASS` when frozen — this
  is correct as-is, it's about finding the bundled *source* app.py to
  hand to Streamlit, not about where app.py itself later writes *data*.
  Don't conflate the two concerns if editing either file.
- `build_exe.bat` installs PyInstaller into the existing project venv
  (assumes `install.bat` has already been run) and calls the above
  build command. Same CRLF requirement, same goto-based structure
  (no parenthesized if/else blocks) as the other .bat files here — see
  gotcha #2 below for why.
- **Could not run the actual PyInstaller build/exe in this sandbox** —
  no Windows, no internet access here to `pip install pyinstaller`. What
  WAS verified: all path-resolution logic, via simulated frozen
  environments constructed by hand (faking `sys.frozen`/`sys.executable`/
  `sys._MEIPASS` and a matching fake directory layout) and running
  app.py's actual code against them — this caught the v25 bug and
  confirmed the v26 fix. The PyInstaller invocation itself (whether the
  exact `--collect-all`/`--add-data` flags produce a working build) is
  correct-by-construction from documented PyInstaller+Streamlit patterns
  but not independently verified. If a user reports a build failure,
  don't assume the fix is obvious — Streamlit+PyInstaller packaging is
  known to be finicky in general, not just for this app.

## Known gotchas (already fixed — don't reintroduce)
1. Use `width='stretch'` not deprecated `use_container_width=True`.
2. `.bat` files MUST be CRLF, not LF — verify with
   `data.count(b'\r\n') == data.count(b'\n')` before shipping.
3. `parse_docx_distribution` must handle the REAL MON table: columns
   [№ на урок, № на седмица, Тема, Вид на урочната единица, ...],
   section-divider rows (all cells identical), lesson rows identified by
   `cells[0].isdigit()`. Don't assume the naive 2-column format.
4. `save_plan` requires `topics` (for the hash) on every call — and now
   also needs `action_desc` for undo tracking on every real mutation.

## Open items / possible future asks
- True drag-and-drop calendar: would need a 3rd-party component
  (`streamlit-calendar` or similar), not verified in this sandbox (no
  internet access here to test it) — deliberately avoided so far in
  favor of dependency-free select+date_input UI. User has asked
  opinion-wise twice, not yet as a real task; needs real testing if it
  ever is.
- `resync_from_invalid` / `compact_from` only anchor on ONE index and
  re-flow everything after in a single pass — no per-gap patching for
  multiple non-contiguous issues. Manual move/swap is the escape hatch
  for finer control.
- Calendar/sync/undo/compact features only work with the new MON docx
  format. Legacy-format users get only the flat table tab.
- `calendar_2026_2027.json` is specific to that school year — next year
  needs a new JSON (same schema) + updated `CALENDAR_FILE` constant.
