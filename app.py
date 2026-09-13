import datetime
import json
import os
import sys
import docx
import pandas as pd
import streamlit as st

# Всички запазвани файлове (график, планове, извънредни дни, календар) се
# пишат/четат спрямо тази папка - НЕ спрямо текущата работна директория
# (CWD) и НЕ спрямо __file__ на самия app.py (последното е ненадеждно при
# .exe, защото __file__ вътре в скрипт, изпълняван динамично от Streamlit,
# не е гарантирано да сочи предвидимо място в PyInstaller бъндъла).
#
# Вместо това:
# - Обикновен .py скрипт (streamlit run app.py): папката на самия app.py.
# - PyInstaller .exe (--onedir): папката ДО самото .exe (sys.executable),
#   а НЕ вътрешната _internal бъндъл-папка - предвидимо място, което
#   потребителят вижда и може лесно да бекъпва/премества.
# sys.frozen/sys.executable са процесни атрибути, зададени от PyInstaller
# bootloader-а и валидни навсякъде в процеса, включително в код, изпълнен
# динамично от Streamlit - за разлика от __file__, което зависи от това
# как точно Streamlit подава скрипта за изпълнение.
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def _seed_bundled_file(filename):
    """При първо стартиране на .exe копира 'фабричния' файл, пакетиран В
    бъндъла (_MEIPASS), до самото .exe (BASE_DIR) - за да го вижда
    потребителят там и да може да го сменя занапред (напр. нов
    calendar_<година>.json всяка следваща учебна година). При обикновен
    .py скрипт (не е frozen) просто връща пътя в BASE_DIR непроменен."""
    dest = os.path.join(BASE_DIR, filename)
    if getattr(sys, "frozen", False) and not os.path.exists(dest):
        bundled_dir = getattr(sys, "_MEIPASS", BASE_DIR)
        src = os.path.join(bundled_dir, filename)
        if os.path.exists(src) and os.path.abspath(src) != os.path.abspath(dest):
            try:
                import shutil
                shutil.copyfile(src, dest)
            except Exception:
                pass
    return dest

# --- 1. Календар на МОН (Ваканции и неучебни дни) ---
# Зарежда се от calendar_<school_year>.json, за да може лесно да се
# замени с актуалната заповед на МОН всяка следваща учебна година,
# без да се пипа кодът на приложението.
CALENDAR_FILE = _seed_bundled_file("calendar_2026_2027.json")

def _d(s):
    return datetime.date.fromisoformat(s)

def load_calendar(path=CALENDAR_FILE):
    with open(path, "r", encoding="utf-8") as f:
        cal = json.load(f)
    vacations = [(_d(v["start"]), _d(v["end"]), v["name"]) for v in cal["vacations_all_grades_except_note"]]
    g12 = cal["grade_12_spring_break_override"]
    # XII клас няма отделна "Пролетна ваканция (I-XI клас)" / "Великденска..." почивка -
    # замества се с по-кратката му собствена пролетна ваканция.
    vacations_12 = [v for v in vacations if "I-XI" not in v[2] and "Великденска" not in v[2]]
    vacations_12.append((_d(g12["start"]), _d(g12["end"]), g12["name"]))

    single_all = {_d(x["date"]): x["reason"] for x in cal["single_non_teaching_days_whole_school"]}
    single_12 = {_d(x["date"]): x["reason"] for x in cal["single_non_teaching_days_grade_12_only"]}

    end_of_year = {k: _d(v) for k, v in cal["end_of_school_year_by_grade"].items()}
    return {
        "start": _d(cal["start_of_school_year"]),
        "vacations": vacations,
        "vacations_12": vacations_12,
        "single_all": single_all,
        "single_12": single_12,
        "end_of_year": end_of_year,
        "source": cal.get("source", ""),
    }

CAL = load_calendar()

# --- 1б. Извънредни неучебни дни (избори и др.) ---
# За случаи извън официалната заповед на МОН - напр. изборен ден, в който
# училищната сграда се ползва за избирателна секция. Важат за ВСИЧКИ класове.
CUSTOM_DAYS_FILE = os.path.join(BASE_DIR, "custom_days_off.json")

def load_custom_days():
    if os.path.exists(CUSTOM_DAYS_FILE):
        try:
            with open(CUSTOM_DAYS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return {_d(x["date"]): x["reason"] for x in raw}
        except Exception:
            return {}
    return {}

def save_custom_days(days_dict):
    payload = [{"date": d.isoformat(), "reason": r} for d, r in sorted(days_dict.items())]
    with open(CUSTOM_DAYS_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

CUSTOM_DAYS = load_custom_days()

def end_of_year_for_grade(grade_str):
    """grade_str e.g. '7' -> връща крайната дата за съответния клас."""
    try:
        g = int(grade_str)
    except (TypeError, ValueError):
        return CAL["end_of_year"]["VII-XI"]
    if g <= 3:
        return CAL["end_of_year"]["I-III"]
    if g <= 6:
        return CAL["end_of_year"]["IV-VI"]
    if g == 12:
        return CAL["end_of_year"]["XII"]
    return CAL["end_of_year"]["VII-XI"]

def is_school_day(date_obj, grade_str=None):
    if date_obj.weekday() in [5, 6]:
        return False
    if date_obj in CUSTOM_DAYS:
        return False
    is_12 = (str(grade_str) == "12")
    single = CAL["single_12"] if is_12 else CAL["single_all"]
    # 12th grade also observes the whole-school NVO non-teaching days
    if date_obj in CAL["single_all"] or date_obj in single:
        return False
    vac = CAL["vacations_12"] if is_12 else CAL["vacations"]
    for start, end, _name in vac:
        if start <= date_obj <= end:
            return False
    if grade_str is not None and date_obj > end_of_year_for_grade(grade_str):
        return False
    return True

CONFIG_FILE = os.path.join(BASE_DIR, "schedule_config.json")
RAZPREDELENIA_DIR = os.path.join(BASE_DIR, "razpredelenia")
DEFAULT_SUBJECT = "MAT"

def normalize_subject(value):
    value = str(value or DEFAULT_SUBJECT).strip().upper()
    return value or DEFAULT_SUBJECT

def lesson_seq_from_label(label):
    number_part = label.split("—", 1)[0].replace("№", "").split("[", 1)[0].strip()
    return int(number_part)

def parse_docx_distribution(file_path_or_bytes, subject=DEFAULT_SUBJECT):
    """
    Поддържа официалния формат на годишно тематично разпределение на МОН:
    таблица с колони № на урок | № на седмица | Тема | Вид на урочната
    единица | ... , с раздел-разделители (напр. "Тема 1. Цели изрази")
    като редове, в които всички клетки съвпадат.
    Всеки номериран ред = 1 учебен час. Пада се обратно към опростения
    формат "Тема | Часове" за съвместимост със стари файлове.
    """
    try:
        doc = docx.Document(file_path_or_bytes)
    except Exception:
        return []

    subject = normalize_subject(subject)
    topics = []
    for table in doc.tables:
        # ИУЧ files use a six-column table without MON lesson-type labels:
        # № по ред | Учебна седмица | Тема | Очакван резултат | ...
        if len(table.columns) >= 6 and len(table.rows) > 2:
            header = [cell.text.strip().lower() for cell in table.rows[1].cells]
            if "№ по ред" in header and any("тема" in cell for cell in header):
                for row in table.rows[2:]:
                    cells = [cell.text.strip() for cell in row.cells]
                    if len(cells) < 4 or not cells[0].isdigit():
                        continue
                    topics.append({
                        "seq": int(cells[0]),
                        "topic": cells[2],
                        "hours": 1,
                        "vid": "Нови знания",
                        "theme": "",
                        "expected_result": cells[3],
                        "is_exam": False,
                        "subject": subject,
                    })
                if topics:
                    return topics

        if len(table.columns) < 4:
            continue  # не е основната таблица с разпределението
        current_theme = ""
        found_any = False
        seq = 0
        for row in table.rows[2:]:
            cells = [c.text.strip() for c in row.cells]
            if len(set(cells)) == 1:
                current_theme = cells[0]
                continue
            if len(cells) < 4:
                continue
            lesson_no, _week_no, topic_name, vid = cells[0], cells[1], cells[2], cells[3]
            if lesson_no.isdigit():
                found_any = True
                seq += 1
                topics.append({
                    "seq": seq,
                    "topic": topic_name,
                    "hours": 1,
                    "vid": vid,
                    "theme": current_theme,
                    "is_exam": "онтрол" in vid,
                    "subject": subject,
                })
        if found_any:
            return topics  # взимаме първата разпознаваема голяма таблица

    # Фолбек: стар прост формат "Тема | Часове" в 2-колонна таблица
    for table in doc.tables:
        seq = 0
        for row in table.rows[1:]:
            cells = [cell.text.strip() for cell in row.cells]
            if len(cells) >= 2:
                try:
                    hours = int(cells[1])
                    seq += 1
                    topics.append({"seq": seq, "topic": cells[0], "hours": hours, "vid": "", "theme": "", "is_exam": True, "subject": subject})
                except ValueError:
                    continue
    return topics

def save_schedule_config(schedule_data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(schedule_data, f, ensure_ascii=False, indent=2)

def load_schedule_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {day: [] for day in ["Понеделник", "Вторник", "Сряда", "Четвъртък", "Петък"]}

# --- 3. Индивидуален план по дати (за календара и редактора) ---
# Всеки клас си има собствен план, записан в lesson_plans/<клас>.json, така
# че ръчните премествания (забавяне, размяна и т.н.) да се пазят между
# отварянията на приложението и да не се презаписват при всяко презареждане.
# Заедно с плана се пази и "отпечатък" (хеш) на съдържанието на .docx, за да
# може приложението да разпознае кога качен нов/редактиран .docx файл се
# различава от последно запазения план и да предложи синхронизация.
import hashlib

PLANS_DIR = os.path.join(BASE_DIR, "lesson_plans")
PLANNING_START_DATE = None

def compute_topics_hash(topics):
    sig = [[t.get("seq"), t.get("topic"), t.get("vid"), normalize_subject(t.get("subject")), t.get("_schedule_signature")] for t in topics]
    raw = json.dumps(sig, ensure_ascii=False)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()

def compute_calendar_signature():
    """Отпечатък на всичко календарно, което влияе върху разпределението:
    официалния календар (CAL) + извънредните неучебни дни (CUSTOM_DAYS).
    Включен е в плановия подпис, така че редактиране на calendar_*.json
    или добавяне на извънреден неучебен ден да задейства банера за
    синхронизация в таб 'Календар', както при промяна на .docx."""
    sig = {
        "start": str(CAL["start"]),
        "vacations": [[str(s), str(e), n] for s, e, n in CAL["vacations"]],
        "vacations_12": [[str(s), str(e), n] for s, e, n in CAL["vacations_12"]],
        "single_all": {str(k): v for k, v in CAL["single_all"].items()},
        "single_12": {str(k): v for k, v in CAL["single_12"].items()},
        "end_of_year": {k: str(v) for k, v in CAL["end_of_year"].items()},
        "custom": {str(k): v for k, v in CUSTOM_DAYS.items()},
        "planning_start": str(PLANNING_START_DATE or CAL["start"]),
    }
    raw = json.dumps(sig, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()

def compute_plan_signature(topics):
    return compute_topics_hash(topics) + ":" + compute_calendar_signature()

def simulate_plan(topics, cls_schedule, start_date, year_end, grade_str):
    """Разпределя урок по урок (1 урок = 1 астрономически час) по реалните
    учебни дни на класа, като спазва ваканциите и неучебните дни.
    Реализирано чрез _simulate_forward (виж там overflow поведението)."""
    return _simulate_forward(topics, start_date, cls_schedule, grade_str, year_end, hours_used_on_start_date=0)

def _simulate_forward(topics_slice, start_date, cls_schedule, grade_str, year_end, hours_used_on_start_date=0):
    """Попълва дати за topics_slice, продължавайки от start_date: първо
    остатъчните свободни часове на самия start_date (ако има), после ден
    по ден напред. Използва се за начална генерация, преместване и
    синхронизация.

    Ако всички уроци не се съберат преди year_end (overflow), НЕ ги
    изхвърля - продължава да им търси дати отвъд края на годината на
    класа (пак спазвайки уикенди/ваканции/извънредни неучебни дни), за да
    остане списъкът винаги с толкова елементи, колкото топиците на входа.
    overflow=True вече е сигналът към потребителя, че нещо не се събира."""
    result = []
    remaining_topics = list(topics_slice)

    def add_slots(date_obj, subjects):
        for subject in subjects:
            if not remaining_topics:
                break
            selected_index = next(
                (i for i, topic in enumerate(remaining_topics)
                 if subject is None or normalize_subject(topic.get("subject")) == subject),
                None,
            )
            if selected_index is None:
                continue
            result.append({**remaining_topics.pop(selected_index), "date": date_obj})

    def subjects_for_weekday(weekday):
        subjects = cls_schedule.get("_subjects", {}).get(weekday)
        return subjects if subjects is not None else [None] * cls_schedule[weekday]

    cur = start_date
    if is_school_day(cur, grade_str) and cur.weekday() in cls_schedule:
        add_slots(cur, subjects_for_weekday(cur.weekday())[hours_used_on_start_date:])
    cur += datetime.timedelta(days=1)
    hard_stop = year_end + datetime.timedelta(days=60)
    while remaining_topics and cur <= hard_stop:
        if is_school_day(cur, grade_str) and cur.weekday() in cls_schedule:
            add_slots(cur, subjects_for_weekday(cur.weekday()))
        cur += datetime.timedelta(days=1)

    overflow = bool(remaining_topics)
    if overflow:
        # Продължаваме без граничната дата на класа (само уикенди/
        # ваканции/извънредни дни все още важат), за да не изчезват уроци.
        far_stop = year_end + datetime.timedelta(days=730)
        while remaining_topics and cur <= far_stop:
            if is_school_day(cur, None) and cur.weekday() in cls_schedule:
                add_slots(cur, subjects_for_weekday(cur.weekday()))
            cur += datetime.timedelta(days=1)

    return result, overflow

UNDO_MAX = 5  # колко последни промени пазим за отмяна (на клас)

def _undo_path(cls):
    return os.path.join(PLANS_DIR, f"{cls}.undo.json")

def push_undo_snapshot(cls, action_desc):
    """Записва ТЕКУЩОТО (преди промяната) съдържание на плана в стека за
    отмяна, преди то да бъде презаписано от save_plan. Ако файлът все още
    не съществува (първо генериране), няма какво да пазим - прескача се."""
    path = os.path.join(PLANS_DIR, f"{cls}.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            current_raw = f.read()
    except Exception:
        return
    upath = _undo_path(cls)
    stack = []
    if os.path.exists(upath):
        try:
            with open(upath, "r", encoding="utf-8") as f:
                stack = json.load(f)
        except Exception:
            stack = []
    stack.append({
        "description": action_desc,
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "raw": current_raw,
    })
    stack = stack[-UNDO_MAX:]
    os.makedirs(PLANS_DIR, exist_ok=True)
    with open(upath, "w", encoding="utf-8") as f:
        json.dump(stack, f, ensure_ascii=False, indent=2)

def peek_undo(cls):
    """Връща {'description', 'timestamp'} за последната отменима промяна,
    или None ако няма такава."""
    upath = _undo_path(cls)
    if not os.path.exists(upath):
        return None
    try:
        with open(upath, "r", encoding="utf-8") as f:
            stack = json.load(f)
    except Exception:
        return None
    return stack[-1] if stack else None

def undo_last_change(cls):
    """Връща плана към състоянието му ТОЧНО преди последната промяна -
    без преизчисляване, директно от записан снапшот. Затова е по-сигурно
    от 'уплътняване': ако последното действие е било 'автопремести заради
    нов неучебен ден' и после отмените (изтриете) същия неучебен ден,
    отмяната връща урока обратно на оригиналната му дата на 100%, без
    евристики. True при успех, False ако няма какво да се отменя."""
    upath = _undo_path(cls)
    if not os.path.exists(upath):
        return False
    try:
        with open(upath, "r", encoding="utf-8") as f:
            stack = json.load(f)
    except Exception:
        return False
    if not stack:
        return False
    last = stack.pop()
    path = os.path.join(PLANS_DIR, f"{cls}.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(last["raw"])
    with open(upath, "w", encoding="utf-8") as f:
        json.dump(stack, f, ensure_ascii=False, indent=2)
    return True

def save_plan(cls, plan, topics, action_desc=None):
    """action_desc: ако е подадено, преди записа се пази снапшот на СТАРОТО
    съдържание в стека за отмяна (виж push_undo_snapshot) с това описание.
    None означава 'не пази отмяна за това записване' - използва се само за
    самото първо генериране на плана, когато няма предишно състояние."""
    if action_desc:
        push_undo_snapshot(cls, action_desc)
    os.makedirs(PLANS_DIR, exist_ok=True)
    path = os.path.join(PLANS_DIR, f"{cls}.json")
    serial = []
    for l in plan:
        row = {k: v for k, v in l.items() if k != "date"}
        row["date"] = l["date"].isoformat()
        serial.append(row)
    payload = {
        "source_hash": compute_plan_signature(topics),
        "topics_hash": compute_topics_hash(topics),
        "lessons": serial,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def load_plan_raw(cls):
    """Чете запазения план от диска без да го сверява с текущия .docx.
    Връща (lessons_или_None, stored_hash_или_None, stored_topics_hash_или_None).
    Хешовете са None за файлове от по-стара версия на приложението."""
    path = os.path.join(PLANS_DIR, f"{cls}.json")
    if not os.path.exists(path):
        return None, None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return None, None, None
    if isinstance(raw, dict) and "lessons" in raw:
        lessons = raw["lessons"]
        stored_hash = raw.get("source_hash")
        stored_topics_hash = raw.get("topics_hash")
    else:
        lessons, stored_hash, stored_topics_hash = raw, None, None  # стар формат
    for r in lessons:
        r["date"] = _d(r["date"])
    return lessons, stored_hash, stored_topics_hash

def load_or_generate_plan(cls, topics, cls_schedule, start_date, year_end, grade_str, force_regenerate=False, action_desc=None):
    """Зарежда персистирания план. Ако липсва, генерира го от .docx.
    Ако вече съществува, но .docx или календарът междувременно са се
    променили (различен подпис), НЕ презаписва автоматично — връща стария
    план с hash_mismatch=True, за да може потребителят съзнателно да
    избере действие (вижте таб 'Календар'). За да не губи ръчни редакции
    без предупреждение. action_desc се препраща на save_plan (за отмяна)
    само при force_regenerate - обикновеното генериране при липсващ файл
    не създава запис за отмяна (няма предишно състояние)."""
    if not force_regenerate:
        lessons, stored_hash, _stored_topics_hash = load_plan_raw(cls)
        if lessons is not None:
            if stored_hash == compute_plan_signature(topics):
                return lessons, False, False
            return lessons, False, True
    plan, overflow = simulate_plan(topics, cls_schedule, start_date, year_end, grade_str)
    save_plan(cls, plan, topics, action_desc=action_desc if force_regenerate else None)
    return plan, overflow, False

def find_invalid_lessons(plan, cls_schedule, grade_str):
    """Връща уроците, чиято запазена дата вече НЕ е валиден учебен ден за
    класа (напр. заради нов извънреден неучебен ден или редактиран
    календар) - т.е. уроци, които сега 'висят' на несъществуващ учебен час."""
    invalid = []
    for l in plan:
        d = l["date"]
        if not (is_school_day(d, grade_str) and d.weekday() in cls_schedule):
            invalid.append(l)
    return invalid

def _resync_from_index(plan, idx, cls_schedule, grade_str, year_end, start_date):
    """0-базиран idx: урокът на тази позиция и всичко след него се
    пренарежда наново от най-ранната възможна свободна дата (продължавайки
    от датата на предходния урок, или от start_date ако idx=0), запазвайки
    всичко ПРЕДИ idx непроменено. Споделена основа за 'избутване напред'
    (при нов неучебен ден) и 'уплътняване назад' (при освободен ден)."""
    if idx <= 0:
        anchor_date = start_date
        hours_used = 0
    else:
        anchor_date = plan[idx - 1]["date"]
        hours_used = sum(1 for l in plan[:idx] if l["date"] == anchor_date)
    remaining = plan[idx:]
    simulated, overflow = _simulate_forward(remaining, anchor_date, cls_schedule, grade_str, year_end, hours_used)
    return plan[:idx] + simulated, overflow

def resync_from_invalid(plan, cls_schedule, grade_str, year_end, start_date):
    """Автоматичен вариант за 'Option 1': намира ПЪРВИЯ урок с вече
    невалидна дата и пренарежда него и всичко след него от там нататък
    (следващия свободен учебен час за класа), без да пипа по-ранните
    уроци. Ако урокът е бил единствен по-рано в реда с невалидна дата,
    ефективно го 'изтласква' на следващия наличен час, а следващите
    уроци се приплъзват след него - точно поведението поискано от
    потребителя за 'auto shift'."""
    first_bad_idx = None
    for i, l in enumerate(plan):
        d = l["date"]
        if not (is_school_day(d, grade_str) and d.weekday() in cls_schedule):
            first_bad_idx = i
            break
    if first_bad_idx is None:
        return plan, False
    return _resync_from_index(plan, first_bad_idx, cls_schedule, grade_str, year_end, start_date)

def compact_from(plan, seq, cls_schedule, grade_str, year_end, start_date):
    """'Уплътнява' графика от урок № seq (1-базиран) нататък: премества
    урок seq и всичко след него на най-ранните свободни дати според
    ТЕКУЩИЯ календар, без да пипа нищо преди seq. Обратното на 'избутване'
    - използва се когато вече неучебен ден е бил премахнат (грешно добавен
    извънреден ден, отменени избори и т.н.) и е останала ненужна дупка."""
    return _resync_from_index(plan, seq - 1, cls_schedule, grade_str, year_end, start_date)

def suggest_compact_anchor(plan, topics, cls_schedule, grade_str, year_end, start_date):
    """Подсказва откъде има смисъл да се уплътни: сравнява текущия план с
    'чиста' симулация на същите теми при ТЕКУЩИЯ календар и връща номера
    (1-базиран) на първия урок, чиято дата се различава - т.е. първия
    признак за ненужна дупка. None ако няма разлика (графикът вече е
    максимално уплътнен)."""
    fresh, _overflow = simulate_plan(topics, cls_schedule, start_date, year_end, grade_str)
    for i in range(min(len(fresh), len(plan))):
        if fresh[i]["date"] != plan[i]["date"]:
            return i + 1
    return None

def sync_plan(old_plan, new_topics, cls_schedule, start_date, year_end, grade_str):
    """Синхронизира персистирания план с редактиран .docx: пази датите на
    уроците, които не са се променили (по тема+вид, позиция по позиция от
    началото), а всичко след първата разлика се преразпределя наново от
    датата на последния запазен урок."""
    divergence = 0
    for i in range(min(len(old_plan), len(new_topics))):
        if (old_plan[i]["topic"] == new_topics[i]["topic"]
            and old_plan[i]["vid"] == new_topics[i]["vid"]
            and normalize_subject(old_plan[i].get("subject")) == normalize_subject(new_topics[i].get("subject"))):
            divergence = i + 1
        else:
            break

    kept = [dict(new_topics[i], date=old_plan[i]["date"]) for i in range(divergence)]
    remaining_new = new_topics[divergence:]

    if divergence > 0:
        anchor_date = kept[-1]["date"]
        hours_used = sum(1 for l in kept if l["date"] == anchor_date)
        simulated, overflow = _simulate_forward(remaining_new, anchor_date, cls_schedule, grade_str, year_end, hours_used)
    else:
        simulated, overflow = _simulate_forward(remaining_new, start_date, cls_schedule, grade_str, year_end, 0)

    return kept + simulated, overflow

def reschedule_from(plan, seq, new_date, cls_schedule, grade_str, year_end):
    """Премества урок № seq на new_date и пренарежда всички следващи уроци
    около останалите свободни часове на класа от този ден нататък."""
    plan = [dict(l) for l in plan]
    plan[seq - 1]["date"] = new_date

    hours_used_today = sum(1 for l in plan[:seq - 1] if l["date"] == new_date) + 1
    remaining = plan[seq:]
    simulated, _overflow = _simulate_forward(remaining, new_date, cls_schedule, grade_str, year_end, hours_used_today)
    return plan[:seq] + simulated

def vacation_reason(day, grade_str):
    if day in CUSTOM_DAYS:
        return CUSTOM_DAYS[day]
    is_12 = (str(grade_str) == "12")
    if day in CAL["single_all"]:
        return CAL["single_all"][day]
    if is_12 and day in CAL["single_12"]:
        return CAL["single_12"][day]
    vac = CAL["vacations_12"] if is_12 else CAL["vacations"]
    for start, end, name in vac:
        if start <= day <= end:
            return name
    return None

MONTHS_BG = ["", "Януари", "Февруари", "Март", "Април", "Май", "Юни",
             "Юли", "Август", "Септември", "Октомври", "Ноември", "Декември"]

# Малка визуална индикация за вида на урока — служи и за легендата под
# календара, и за значката пред темата във всяка клетка.
VID_BADGES = {
    "Нови знания": ("🆕", "#1a5fb4"),
    "Упражнение": ("✏️", "#946100"),
    "Преговор": ("🔁", "#4a5568"),
    "Обобщение": ("📚", "#276749"),
    "Практически дейности": ("🛠️", "#6b46c1"),
    "Контрол и оценка": ("📝", "#a15c00"),
}
DEFAULT_BADGE = ("•", "#555")
EXAM_BG = "#fff3cd"       # златисто/жълто - тест, контролно, класна работа
VACATION_BG = "#fde2e1"   # червеникаво - ваканция / неучебен ден (както в обикновен календар)
VACATION_COLOR = "#b00020"

def vid_badge(vid):
    return VID_BADGES.get(vid, DEFAULT_BADGE)

def render_calendar_legend():
    import html as _html
    items = list(VID_BADGES.items()) + [("Ваканция / неучебен ден", ("🔴", VACATION_COLOR))]
    chips = []
    for label, (emoji, color) in items:
        chips.append(
            f"<span style='display:inline-flex; align-items:center; gap:4px; "
            f"margin-right:16px; margin-bottom:4px; font-size:13px; color:{color};'>"
            f"<span style='font-size:15px;'>{emoji}</span>{_html.escape(label)}</span>"
        )
    return f"<div style='margin:6px 0 10px 0;'>{''.join(chips)}</div>"

def render_month_calendar(plan_by_date, year, month, grade_str):
    import calendar as _pycal
    import html as _html
    cal = _pycal.Calendar(firstweekday=0)
    weeks = cal.monthdatescalendar(year, month)
    weekday_names = ["Пон", "Вт", "Ср", "Чет", "Пет", "Съб", "Нед"]
    rows = ["<table style='width:100%; border-collapse:collapse; table-layout:fixed;'>"]
    rows.append("<tr>" + "".join(
        f"<th style='padding:6px;font-size:14px;color:#666;border-bottom:1px solid #ddd;'>{d}</th>"
        for d in weekday_names) + "</tr>")
    for week in weeks:
        rows.append("<tr>")
        for day in week:
            in_month = day.month == month
            base_style = "vertical-align:top; height:118px; border:1px solid #e8e8e8; padding:6px; font-size:14px; overflow:hidden;"
            if not in_month:
                rows.append(f"<td style='{base_style} background:#fafafa; color:#ccc;'>{day.day}</td>")
                continue
            bg = "#ffffff"
            content = [f"<div style='font-weight:700; font-size:15px; color:#333;'>{day.day}</div>"]
            reason = vacation_reason(day, grade_str)
            if day.weekday() >= 5 and not reason:
                bg = "#d9d9d9"
            if reason:
                bg = VACATION_BG
                content.append(
                    f"<div style='color:{VACATION_COLOR}; font-weight:600; font-size:12px; margin-top:2px;'>🔴 {_html.escape(reason)}</div>"
                )
            else:
                lessons = plan_by_date.get(day, [])
                for l in lessons:
                    label = _html.escape(l["topic"][:36])
                    subject = _html.escape(normalize_subject(l.get("subject")))
                    emoji, color = vid_badge(l.get("vid", ""))
                    if l.get("is_exam"):
                        bg = EXAM_BG
                        content.append(
                            f"<div style='color:{color}; font-weight:700; font-size:13px; margin-top:2px;'>{subject} {emoji} {label}</div>"
                        )
                    else:
                        content.append(
                            f"<div style='color:#333; font-size:13px; margin-top:2px;'>{subject} {emoji} {label}</div>"
                        )
            rows.append(f"<td style='{base_style} background:{bg};'>{''.join(content)}</td>")
        rows.append("</tr>")
    rows.append("</table>")
    return "".join(rows)

# --- 4. Excel износ ---
def build_xlsx_bytes(df, cal_source=""):
    """Гради форматиран .xlsx (в паметта) от таблицата с контролни -
    оцветен по клас, с истински дати (за сортиране/филтриране в Excel)."""
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "График контролни"

    cols = [c for c in ["Клас", "Предмет", "Раздел", "Раздел / Тема", "Основна дата", "Ден", "Резервна дата"] if c in df.columns]
    ws.append(cols)

    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(name="Arial", bold=True, color="FFFFFF", size=11)
    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for c in range(1, len(cols) + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border

    palette = ["DDEBF7", "E2EFDA", "FFF2CC", "FCE4D6", "E4DFEC", "D9E1F2"]
    class_fill = {}
    date_col_idx = {name: i + 1 for i, name in enumerate(cols) if name in ("Основна дата", "Резервна дата")}

    row_i = 2
    for _, row in df.iterrows():
        cls_name = row.get("Клас", "")
        if cls_name not in class_fill:
            class_fill[cls_name] = PatternFill(
                start_color=palette[len(class_fill) % len(palette)],
                end_color=palette[len(class_fill) % len(palette)],
                fill_type="solid",
            )
        vals = []
        for c in cols:
            v = row.get(c, "")
            if c in date_col_idx and isinstance(v, str) and v:
                try:
                    v = datetime.datetime.strptime(v, "%d.%m.%Y").date()
                except ValueError:
                    pass
            vals.append(v)
        ws.append(vals)
        for c in range(1, len(cols) + 1):
            cell = ws.cell(row=row_i, column=c)
            cell.font = Font(name="Arial", size=10)
            cell.border = border
            cell.fill = class_fill[cls_name]
            if c in date_col_idx.values():
                cell.number_format = "dd.mm.yyyy"
                cell.alignment = Alignment(horizontal="center")
            elif c == 1:
                cell.alignment = Alignment(horizontal="center")
        row_i += 1

    widths = {"Клас": 7, "Предмет": 10, "Раздел": 30, "Раздел / Тема": 42, "Основна дата": 14, "Ден": 10, "Резервна дата": 14}
    for i, c in enumerate(cols, start=1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(c, 16)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{row_i - 1}"

    if cal_source:
        ws2 = wb.create_sheet("Източник на календара")
        ws2["A1"] = "Източник на официалния учебен календар:"
        ws2["A1"].font = Font(name="Arial", bold=True)
        ws2["A2"] = cal_source
        ws2["A2"].alignment = Alignment(wrap_text=True)
        ws2.column_dimensions["A"].width = 100

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()

# Инициализация
st.set_page_config(page_title="Планиране на контролни по Математика", layout="wide")

if "weekly_schedule" not in st.session_state:
    st.session_state.weekly_schedule = load_schedule_config()

if "grade_files" not in st.session_state:
    st.session_state.grade_files = {} # {"7": topics, "8": topics}
if "grade_distributions" not in st.session_state:
    st.session_state.grade_distributions = {}

def combined_topics_for_grade(grade):
    distributions = st.session_state.grade_distributions.get(str(grade), {})
    if distributions:
        combined = []
        for subject in sorted(distributions):
            combined.extend(distributions[subject])
        return combined
    return [dict(topic, subject=normalize_subject(topic.get("subject")))
            for topic in st.session_state.grade_files.get(str(grade), [])]

def schedule_entry_subject(item):
    return normalize_subject(item.get("subject"))

def class_schedule_for(cls):
    day_map = {"Понеделник": 0, "Вторник": 1, "Сряда": 2, "Четвъртък": 3, "Петък": 4}
    schedule = {}
    subject_slots = {}
    for day, items in st.session_state.weekly_schedule.items():
        for item in items:
            if item["class"] == cls:
                weekday = day_map[day]
                hours = int(item["hours"])
                schedule[weekday] = schedule.get(weekday, 0) + hours
                subject_slots.setdefault(weekday, []).extend([schedule_entry_subject(item)] * hours)
    schedule["_subjects"] = subject_slots
    return schedule

def topics_for_class(cls, grade):
    distributions = st.session_state.grade_distributions.get(str(grade), {})
    schedule_signature = json.dumps(
        sorted([
            [day, int(item.get("hours", 0)), schedule_entry_subject(item)]
            for day, items in st.session_state.weekly_schedule.items()
            for item in items if item["class"] == cls
        ]), ensure_ascii=False,
    )
    if not distributions:
        return [dict(topic, _schedule_signature=schedule_signature)
                for topic in combined_topics_for_grade(grade)]

    subject_hours = {}
    for day_items in st.session_state.weekly_schedule.values():
        for item in day_items:
            if item["class"] == cls:
                subject = schedule_entry_subject(item)
                subject_hours[subject] = subject_hours.get(subject, 0) + int(item["hours"])

    queues = {subject: list(topics) for subject, topics in distributions.items()}
    subjects = sorted(queues, key=lambda subject: (-subject_hours.get(subject, 0), subject))
    weighted_order = [subject for subject in subjects for _ in range(max(1, subject_hours.get(subject, 0)))]
    combined = []
    while any(queues.values()):
        for subject in weighted_order or subjects:
            if queues.get(subject):
                combined.append(queues[subject].pop(0))

    return [dict(topic, seq=index, _schedule_signature=schedule_signature)
            for index, topic in enumerate(combined, start=1)]

# Автоматично зареждане на файлове от папката razpredelenia по випуск
if not os.path.exists(RAZPREDELENIA_DIR):
    os.makedirs(RAZPREDELENIA_DIR)

for f_name in os.listdir(RAZPREDELENIA_DIR):
    if f_name.endswith(".docx"):
        # Поддържа старото '7.docx' и новото '7_MAT.docx'/'7_ИУЧ.docx'.
        file_stem = f_name.rsplit(".", 1)[0]
        file_parts = file_stem.split("_")
        grade_key = file_parts[0].replace("klas", "").strip()
        subject_key = normalize_subject(
            file_parts[1] if len(file_parts) > 1 and file_parts[1].lower() != "klas" else DEFAULT_SUBJECT
        )
        f_path = os.path.join(RAZPREDELENIA_DIR, f_name)
        parsed = parse_docx_distribution(f_path, subject_key)
        if parsed:
            st.session_state.grade_distributions.setdefault(grade_key, {})[subject_key] = parsed

for grade_key in list(st.session_state.grade_distributions):
    st.session_state.grade_files[grade_key] = combined_topics_for_grade(grade_key)

st.title("📐 Генератор на график за Контролни работи по Математика")

# --- Страничен панел: Седмична Програма ---
st.sidebar.header("🗓️ 1. Седмична Програма")
selected_day = st.sidebar.selectbox("Ден от седмицата:", options=["Понеделник", "Вторник", "Сряда", "Четвъртък", "Петък"])

col_g, col_l, col_h = st.sidebar.columns([2, 2, 2])
with col_g:
    s_grade = st.selectbox("Клас", options=[f"{i}" for i in range(5, 13)], index=2, key="sg")
with col_l:
    s_letter = st.selectbox("Паралелка", options=["А", "Б", "В", "Г", "Д", "Е", "Ж", "З"], index=0, key="sl")
with col_h:
    s_hours = st.number_input("Часове", min_value=1, max_value=4, value=1, key="sh")
st.sidebar.text_input("Кратко име на предмета", value=DEFAULT_SUBJECT, max_chars=8, key="schedule_subject")

cls_name = f"{s_grade}.{s_letter}"

col_b1, col_b2 = st.sidebar.columns(2)
with col_b1:
    if st.button(f"➕ Добави", width='stretch'):
        st.session_state.weekly_schedule[selected_day] = [
            item for item in st.session_state.weekly_schedule[selected_day]
            if not (item["class"] == cls_name and schedule_entry_subject(item) == normalize_subject(st.session_state.schedule_subject))
        ]
        st.session_state.weekly_schedule[selected_day].append({
            "class": cls_name, "hours": s_hours, "grade": s_grade,
            "subject": normalize_subject(st.session_state.schedule_subject),
        })
        save_schedule_config(st.session_state.weekly_schedule)
        st.success(f"Запазено!")

with col_b2:
    if st.button("🗑️ Изчисти деня", width='stretch'):
        st.session_state.weekly_schedule[selected_day] = []
        save_schedule_config(st.session_state.weekly_schedule)
        st.warning("Денят е изчистен!")

st.sidebar.markdown("---")
st.sidebar.header("📁 2. Разпределения по Випуск (.docx)")

# Извличане на всички уникални випускове (напр. "7", "8")
all_grades = sorted(list({item.get("grade", item["class"].split(".")[0]) for day_items in st.session_state.weekly_schedule.values() for item in day_items}))

if all_grades:
    target_grade = st.sidebar.selectbox("Качи 1 разпределение за ЦЕЛИЯ випуск:", options=all_grades)
    upload_subject = st.sidebar.text_input(
        "Кратко име на предмета", value=DEFAULT_SUBJECT, max_chars=8,
        key=f"upload_subject_{target_grade}", placeholder="напр. MAT или ИУЧ"
    )
    upload_subject = normalize_subject(upload_subject)
    uploaded_f = st.sidebar.file_uploader(f"Файл за {target_grade} клас", type=["docx"], key=f"file_{target_grade}")
    
    if uploaded_f:
        parsed_topics = parse_docx_distribution(uploaded_f, upload_subject)
        if parsed_topics:
            st.session_state.grade_distributions.setdefault(target_grade, {})[upload_subject] = parsed_topics
            st.session_state.grade_files[target_grade] = combined_topics_for_grade(target_grade)
            file_save_name = f"{target_grade}_{upload_subject}.docx"
            with open(os.path.join(RAZPREDELENIA_DIR, file_save_name), "wb") as f:
                f.write(uploaded_f.getbuffer())
            st.sidebar.success(f"Запазено за {target_grade} клас, предмет {upload_subject}!")

if "planning_start_date" not in st.session_state:
    st.session_state.planning_start_date = CAL["start"] + datetime.timedelta(days=1)
school_year_start = st.sidebar.date_input(
    "Първа дата с учебни часове",
    key="planning_start_date",
    help="15 септември остава официално начало на учебната година. По подразбиране часовете започват от 16 септември.",
)
PLANNING_START_DATE = school_year_start

st.sidebar.markdown("---")
st.sidebar.header("🗳️ 3. Извънредни неучебни дни")
st.sidebar.caption("Напр. избори, аварийно бедствено положение и др. — важат за всички класове.")
with st.sidebar.form("add_custom_day_form", clear_on_submit=True):
    new_custom_date = st.date_input("Дата:", key="new_custom_date")
    new_custom_reason = st.text_input("Причина:", key="new_custom_reason", placeholder="напр. Избори за...")
    add_custom_submitted = st.form_submit_button("➕ Добави")
    if add_custom_submitted:
        CUSTOM_DAYS[new_custom_date] = new_custom_reason or "Извънреден неучебен ден"
        save_custom_days(CUSTOM_DAYS)
        st.sidebar.success(f"Добавено: {new_custom_date.strftime('%d.%m.%Y')}")
        st.rerun()

if CUSTOM_DAYS:
    for cd, reason in sorted(CUSTOM_DAYS.items()):
        cdc1, cdc2 = st.sidebar.columns([4, 1])
        cdc1.caption(f"{cd.strftime('%d.%m.%Y')} — {reason}")
        if cdc2.button("🗑️", key=f"del_custom_{cd.isoformat()}"):
            del CUSTOM_DAYS[cd]
            save_custom_days(CUSTOM_DAYS)
            st.rerun()

# --- Главен екран ---
tab1, tab2, tab_cal, tab3 = st.tabs(["📊 Седмично разписание", "📅 График за Контролни", "🗓️ Календар", "📥 Експорт"])

with tab1:
    st.header("📚 Вашето седмично разписание")
    day_cols = st.columns(5)
    days_list = ["Понеделник", "Вторник", "Сряда", "Четвъртък", "Петък"]
    
    for idx, day in enumerate(days_list):
        with day_cols[idx]:
            st.markdown(f"### {day}")
            entries = st.session_state.weekly_schedule[day]
            if entries:
                for e in entries:
                    st.info(f"🏫 **{e['class']}** — {e['hours']} ч. ({schedule_entry_subject(e)})")
            else:
                st.caption("Няма часове")

all_classes = sorted(list({item["class"] for day_items in st.session_state.weekly_schedule.values() for item in day_items}))

with tab2:
    st.header("📅 Изчислени дати за контролни")
    
    if not all_classes:
        st.info("Добавете часове в програмата от менюто вляво.")
    else:
        missing_grades = []
        for cls in all_classes:
            grd = cls.split(".")[0]
            if grd not in st.session_state.grade_files:
                missing_grades.append(f"{grd} клас")
        
        missing_grades = sorted(list(set(missing_grades)))
        
        if missing_grades:
            st.warning(f"⚠️ Липсва `.docx` разпределение за: {', '.join(missing_grades)}. Качете един файл за съответния випуск отляво.")
        else:
            day_map = {"Понеделник": 0, "Вторник": 1, "Сряда": 2, "Четвъртък": 3, "Петък": 4}
            results = []

            for cls in all_classes:
                cls_grade = cls.split(".")[0]
                cls_schedule = class_schedule_for(cls)

                topics = topics_for_class(cls, cls_grade)
                year_end = end_of_year_for_grade(cls_grade)
                is_new_format = bool(topics) and bool(topics[0].get("vid"))

                if is_new_format:
                    # Използва (или генерира при първо отваряне) персистиран
                    # план по дати - същият, който се вижда и редактира в
                    # таб "Календар", за да са двата изгледа винаги в синхрон.
                    plan, overflow, hash_mismatch = load_or_generate_plan(cls, topics, cls_schedule, school_year_start, year_end, cls_grade)
                    for i, l in enumerate(plan):
                        if not l.get("is_exam"):
                            continue
                        exam_date = l["date"]
                        backup_date = exam_date + datetime.timedelta(days=1)
                        tries = 0
                        while not (is_school_day(backup_date, cls_grade) and backup_date.weekday() in cls_schedule):
                            backup_date += datetime.timedelta(days=1)
                            tries += 1
                            if tries > 60:
                                break
                        results.append({
                            "Клас": cls,
                            "Предмет": normalize_subject(l.get("subject")),
                            "Раздел": l.get("theme", ""),
                            "Раздел / Тема": l["topic"],
                            "Основна дата": exam_date.strftime("%d.%m.%Y"),
                            "Ден": exam_date.strftime("%a"),
                            "Резервна дата": backup_date.strftime("%d.%m.%Y"),
                            "raw_date": exam_date
                        })
                    if overflow:
                        st.warning(
                            f"⚠️ Разпределението за {cls} не се събира преди края на учебната "
                            f"година ({year_end.strftime('%d.%m.%Y')}) при текущата седмична натовареност. "
                            "Проверете часовете по математика за този клас."
                        )
                    if hash_mismatch and not st.session_state.get(f"dismiss_mismatch_{cls}", False):
                        st.info(
                            f"ℹ️ Качен е различен .docx за {cls_grade} клас спрямо последно запазения "
                            f"график на {cls} — датите по-долу може да не отразяват последните промени. "
                            "Отворете таб '🗓️ Календар' за синхронизация."
                        )
                    continue

                # --- Стар опростен формат (Тема | Часове), без номериране по
                # урок - пресмята се "на място", без персистиран план. ---
                current_date = school_year_start
                overflow = False

                for idx, t in enumerate(topics):
                    hours_needed = t["hours"]
                    hours_accumulated = 0

                    while hours_accumulated < hours_needed:
                        if current_date > year_end:
                            overflow = True
                            break
                        if is_school_day(current_date, cls_grade):
                            w_day = current_date.weekday()
                            if w_day in cls_schedule:
                                hours_accumulated += cls_schedule[w_day]
                        if hours_accumulated < hours_needed:
                            current_date += datetime.timedelta(days=1)
                    if overflow:
                        break

                    exam_date = current_date + datetime.timedelta(days=1)
                    while not is_school_day(exam_date, cls_grade) or exam_date.weekday() not in cls_schedule:
                        exam_date += datetime.timedelta(days=1)

                    backup_date = exam_date + datetime.timedelta(days=1)
                    while not is_school_day(backup_date, cls_grade) or backup_date.weekday() not in cls_schedule:
                        backup_date += datetime.timedelta(days=1)
                        if backup_date > year_end + datetime.timedelta(days=14):
                            break

                    results.append({
                        "Клас": cls,
                        "Предмет": normalize_subject(t.get("subject")),
                        "Раздел": t.get("theme", ""),
                        "Раздел / Тема": t["topic"],
                        "Основна дата": exam_date.strftime("%d.%m.%Y"),
                        "Ден": exam_date.strftime("%a"),
                        "Резервна дата": backup_date.strftime("%d.%m.%Y"),
                        "raw_date": exam_date
                    })
                    current_date = exam_date

                if overflow:
                    st.warning(
                        f"⚠️ Разпределението за {cls} не се събира преди края на учебната "
                        f"година ({year_end.strftime('%d.%m.%Y')}) при текущата седмична натовареност. "
                        "Проверете часовете по математика за този клас."
                    )

            result_columns = [
                "Клас", "Предмет", "Раздел", "Раздел / Тема",
                "Основна дата", "Ден", "Резервна дата", "raw_date",
            ]
            df_res = pd.DataFrame(results, columns=result_columns)
            if not df_res.empty:
                df_res = df_res.sort_values(by=["raw_date", "Клас"])
            else:
                st.info("Няма намерени контролни уроци за текущите разпределения.")

            st.subheader("🔍 Филтрирай изгледа")
            col_f1, col_f2 = st.columns(2)
            
            with col_f1:
                filter_class = st.multiselect("Филтър по Клас:", options=all_classes, default=all_classes)
            with col_f2:
                filter_type = st.radio("Показвай:", options=["Само Контролни", "Всички теми (с Нови знания, Преговор и др.)"], index=0)

            filtered_df = df_res[df_res["Клас"].isin(filter_class)]

            # За новия формат на МОН всички редове в df_res вече СА само
            # контролните (филтрирани по is_exam по-горе); за стари файлове
            # пазим и резервно търсене по ключови думи като fallback.
            if filter_type == "Само Контролни" and "Раздел" in filtered_df.columns:
                mask = filtered_df["Раздел / Тема"].str.contains(
                    "Контрол|оценка|Контролна|Изпитване|Тест|Класна работа", case=False, na=False
                )
                filtered_df = filtered_df[mask | (filtered_df["Раздел"] != "")]

            display_cols = [c for c in ["Клас", "Предмет", "Раздел", "Раздел / Тема", "Основна дата", "Ден", "Резервна дата"] if c in filtered_df.columns]
            st.dataframe(
                filtered_df[display_cols],
                width='stretch',
                hide_index=True
            )

with tab_cal:
    st.header("🗓️ Календар по клас")

    if not all_classes:
        st.info("Добавете часове в програмата от менюто вляво.")
    else:
        cal_cls = st.selectbox("Избери клас", options=all_classes, key="cal_class")
        cal_grade = cal_cls.split(".")[0]

        if cal_grade not in st.session_state.grade_files:
            st.warning(f"⚠️ Липсва `.docx` разпределение за {cal_grade} клас. Качете го от менюто вляво.")
        else:
            topics = topics_for_class(cal_cls, cal_grade)
            if not topics or not topics[0].get("vid"):
                st.info(
                    "Календарният изглед работи само с новия формат на годишно "
                    "разпределение на МОН (с номерирани уроци). Файлът за този "
                    "випуск изглежда в стария опростен формат — вижте таб "
                    "'График за Контролни' вместо това."
                )
            else:
                day_map = {"Понеделник": 0, "Вторник": 1, "Сряда": 2, "Четвъртък": 3, "Петък": 4}
                cls_schedule = class_schedule_for(cal_cls)

                year_end = end_of_year_for_grade(cal_grade)
                plan, overflow, hash_mismatch = load_or_generate_plan(cal_cls, topics, cls_schedule, school_year_start, year_end, cal_grade)

                flash_key = f"flash_{cal_cls}"
                if flash_key in st.session_state:
                    level, msg = st.session_state.pop(flash_key)
                    getattr(st, level)(msg)

                last_undo = peek_undo(cal_cls)
                if last_undo:
                    undo_col1, undo_col2 = st.columns([4, 1])
                    with undo_col1:
                        ts_display = last_undo["timestamp"].replace("T", " ")
                        st.caption(f"↩️ Последна промяна: {last_undo['description']} ({ts_display})")
                    with undo_col2:
                        if st.button("↩️ Отмени", key=f"undo_{cal_cls}", width='stretch',
                                     help="Връща плана ТОЧНО към състоянието му преди последната промяна — "
                                          "без преизчисляване, директно от запазено копие."):
                            if undo_last_change(cal_cls):
                                st.session_state[flash_key] = (
                                    "success", f"Отменено: {last_undo['description']}."
                                )
                                st.rerun()

                dismiss_key = f"dismiss_mismatch_{cal_cls}"
                if hash_mismatch and not st.session_state.get(dismiss_key, False):
                    _raw_lessons, _stored_hash, stored_topics_hash = load_plan_raw(cal_cls)
                    docx_changed = stored_topics_hash is None or stored_topics_hash != compute_topics_hash(topics)
                    invalid_lessons = find_invalid_lessons(plan, cls_schedule, cal_grade)
                    compact_anchor = None
                    if not invalid_lessons:
                        compact_anchor = suggest_compact_anchor(plan, topics, cls_schedule, cal_grade, year_end, school_year_start)

                    msg_parts = [f"⚠️ Нещо се е променило спрямо последно запазения график за {cal_cls}."]
                    if invalid_lessons:
                        names = ", ".join(
                            f"№{l['seq']} ({l['date'].strftime('%d.%m.%Y')})" for l in invalid_lessons[:6]
                        )
                        extra = "" if len(invalid_lessons) <= 6 else f" и още {len(invalid_lessons) - 6}"
                        msg_parts.append(f"📅 {len(invalid_lessons)} урок(а) вече падат в неучебен ден: {names}{extra}.")
                    if compact_anchor is not None:
                        msg_parts.append(
                            f"🗜️ Освободило се е място (напр. премахнат неучебен ден) - от урок №{compact_anchor} "
                            "нататък графикът може да се уплътни."
                        )
                    if docx_changed:
                        changed_subjects = sorted({normalize_subject(t.get("subject")) for t in topics})
                        subject_text = ", ".join(changed_subjects) or DEFAULT_SUBJECT
                        msg_parts.append(
                            f"📄 Променено е разпределение за випуск {cal_grade} ({subject_text}). "
                            "Синхронизирането ще използва всички избрани предмети."
                        )
                    st.warning("  \n".join(msg_parts))

                    actions = []
                    if invalid_lessons:
                        actions.append((
                            "auto", "🔄 Автопремести засегнатите",
                            "Премества засегнатите уроци на следващия свободен учебен час за класа "
                            "и приплъзва всичко след тях с толкова дни, колкото трябва."
                        ))
                        actions.append((
                            "manual", "✋ Аз ще ги преместя ръчно",
                            "Нищо не се променя автоматично - използвайте 'Преместване на урок' или "
                            "'Размяна' по-долу за изброените урок(ци) (напр. за да съберете 2 урока в "
                            "1 учебен час и да наваксате)."
                        ))
                    if compact_anchor is not None:
                        actions.append((
                            "compact", "🗜️ Уплътни освободеното място",
                            f"Премества урок №{compact_anchor} и всичко след него на най-ранните свободни "
                            "часове според текущия календар - връща разписанието към нормалния му темп."
                        ))
                    if docx_changed:
                        actions.append((
                            "sync", "🔄 Синхронизирай с .docx",
                            "Запазва датите на уроците, които не са се променили, и преразпределя само "
                            "промените/новите уроци напред от там."
                        ))
                    actions.append((
                        "reset", "♻️ Нулирай по .docx",
                        "Изтрива всички ръчни промени и генерира графика наново от началото на годината."
                    ))
                    actions.append((
                        "dismiss", "➡️ Продължи както си е",
                        "Не прави нищо сега (ще напомня пак при следващо отваряне)."
                    ))

                    mcols = st.columns(len(actions))
                    for col, (action_id, label, help_txt) in zip(mcols, actions):
                        with col:
                            if st.button(label, key=f"{action_id}_{cal_cls}", width='stretch', help=help_txt):
                                if action_id == "auto":
                                    new_plan, _ov = resync_from_invalid(plan, cls_schedule, cal_grade, year_end, school_year_start)
                                    save_plan(cal_cls, new_plan, topics, action_desc="Автопремести засегнатите уроци")
                                    st.session_state.pop(dismiss_key, None)
                                    st.session_state[flash_key] = (
                                        "success", f"Засегнатите уроци за {cal_cls} са преместени автоматично."
                                    )
                                elif action_id == "manual":
                                    st.session_state[dismiss_key] = True
                                elif action_id == "compact":
                                    new_plan, _ov = compact_from(plan, compact_anchor, cls_schedule, cal_grade, year_end, school_year_start)
                                    save_plan(cal_cls, new_plan, topics, action_desc=f"Уплътни от урок №{compact_anchor}")
                                    st.session_state.pop(dismiss_key, None)
                                    st.session_state[flash_key] = (
                                        "success", f"Графикът за {cal_cls} е уплътнен от урок №{compact_anchor} нататък."
                                    )
                                elif action_id == "sync":
                                    new_plan, _ov = sync_plan(plan, topics, cls_schedule, school_year_start, year_end, cal_grade)
                                    save_plan(cal_cls, new_plan, topics, action_desc="Синхронизирай с нов .docx")
                                    st.session_state.pop(dismiss_key, None)
                                    st.session_state[flash_key] = (
                                        "success", f"Графикът за {cal_cls} е синхронизиран с новия .docx."
                                    )
                                elif action_id == "reset":
                                    load_or_generate_plan(cal_cls, topics, cls_schedule, school_year_start, year_end, cal_grade, force_regenerate=True, action_desc="Нулиране по .docx")
                                    st.session_state.pop(dismiss_key, None)
                                    st.session_state[flash_key] = (
                                        "success", f"Графикът за {cal_cls} е нулиран по текущия .docx."
                                    )
                                elif action_id == "dismiss":
                                    st.session_state[dismiss_key] = True
                                st.rerun()
                    st.markdown("---")

                if overflow:
                    st.warning(
                        f"⚠️ При текущата седмична натовареност разпределението не се "
                        f"събира преди {year_end.strftime('%d.%m.%Y')}. Част от последните "
                        "уроци остават непланирани."
                    )

                # --- месечен избор ---
                months = []
                ycur, mcur = school_year_start.year, school_year_start.month
                while (ycur, mcur) <= (year_end.year, year_end.month):
                    months.append((ycur, mcur))
                    mcur += 1
                    if mcur > 12:
                        mcur = 1
                        ycur += 1
                month_labels = [f"{MONTHS_BG[m]} {y}" for y, m in months]
                today = datetime.date.today()
                default_idx = 0
                for i, (y, m) in enumerate(months):
                    if (y, m) == (today.year, today.month):
                        default_idx = i
                        break
                sel_label = st.select_slider("Месец", options=month_labels, value=month_labels[default_idx], key="cal_month")
                sel_y, sel_m = months[month_labels.index(sel_label)]

                from collections import defaultdict
                plan_by_date = defaultdict(list)
                for l in plan:
                    plan_by_date[l["date"]].append(l)

                st.caption("Легенда:")
                st.markdown(render_calendar_legend(), unsafe_allow_html=True)
                st.markdown(render_month_calendar(plan_by_date, sel_y, sel_m, cal_grade), unsafe_allow_html=True)

                st.markdown("---")
                st.subheader("✏️ Преместване на урок")
                st.caption(
                    "Ако даден урок се отложи (болест, събитие, по-бавен темп и т.н.), "
                    "сменете датата му тук."
                )

                WEEKDAY_NAMES_FULL = ["понеделник", "вторник", "сряда", "четвъртък", "петък", "събота", "неделя"]

                col_e1, col_e2 = st.columns([2, 1])
                with col_e1:
                    seq_options = [f"№{l['seq']} [{normalize_subject(l.get('subject'))}] — {l['topic'][:45]}" for l in plan]
                    shift_choice = st.selectbox("Урок за преместване:", options=seq_options, key=f"shiftchoice_{cal_cls}")
                    shift_seq = lesson_seq_from_label(shift_choice)
                with col_e2:
                    default_shift_date = plan[shift_seq - 1]["date"]
                    new_shift_date = st.date_input("Нова дата за този урок:", value=default_shift_date, key=f"shiftdate_{cal_cls}")

                auto_adjust = st.toggle(
                    "Автоматично пренареждане на следващите уроци",
                    value=True,
                    key=f"autoadjust_{cal_cls}",
                    help="Включено: следващите уроци се изместват сами според свободните часове по разписание. "
                         "Изключено: премества се само избраният урок — вие решавате какво да правите с останалите "
                         "(например да съберете 2 урока в 1 учебен час, за да наваксате материал)."
                )

                slot_has_class = new_shift_date.weekday() in cls_schedule
                slot_is_school_day = is_school_day(new_shift_date, cal_grade)
                slot_ok = slot_has_class and slot_is_school_day
                override = True
                if not slot_ok:
                    wd_name = WEEKDAY_NAMES_FULL[new_shift_date.weekday()]
                    if not slot_is_school_day:
                        reason = vacation_reason(new_shift_date, cal_grade) or "неучебен ден (уикенд)"
                        st.warning(f"⚠️ {new_shift_date.strftime('%d.%m.%Y')} ({wd_name}) е неучебен ден: {reason}.")
                    else:
                        st.warning(
                            f"⚠️ Нямате учебен час по математика с {cal_cls} в {wd_name} "
                            f"({new_shift_date.strftime('%d.%m.%Y')}) — вижте седмичната програма. "
                            "Не можете да преподавате на класа този ден."
                        )
                    override = st.checkbox(
                        "Знам, приложи въпреки това (напр. извънреден/заместващ час)",
                        key=f"override_{cal_cls}",
                    )

                if st.button("↪️ Приложи преместването", key=f"shiftbtn_{cal_cls}", disabled=not (slot_ok or override)):
                    if auto_adjust:
                        new_plan = reschedule_from(plan, shift_seq, new_shift_date, cls_schedule, cal_grade, year_end)
                        save_plan(cal_cls, new_plan, topics, action_desc=f"Премести урок №{shift_seq} (с пренареждане)")
                        st.session_state[f"flash_{cal_cls}"] = (
                            "success", f"Графикът за {cal_cls} е пренареден от урок №{shift_seq} нататък."
                        )
                    else:
                        new_plan = [dict(l) for l in plan]
                        new_plan[shift_seq - 1]["date"] = new_shift_date
                        save_plan(cal_cls, new_plan, topics, action_desc=f"Премести урок №{shift_seq} (без пренареждане)")
                        same_day_count = sum(1 for l in new_plan if l["date"] == new_shift_date)
                        capacity = cls_schedule.get(new_shift_date.weekday(), 0)
                        if same_day_count > capacity:
                            st.session_state[f"flash_{cal_cls}"] = (
                                "warning",
                                f"Урок №{shift_seq} е преместен на {new_shift_date.strftime('%d.%m.%Y')}, "
                                f"но останалите уроци НЕ бяха пренаредени. Внимание: сега на тази дата има "
                                f"{same_day_count} урока при {capacity} час(а) по разписание за {cal_cls} — "
                                "ще трябва ръчно да решите как да ги съберете (или да преместите някой от тях)."
                            )
                        else:
                            st.session_state[f"flash_{cal_cls}"] = (
                                "success",
                                f"Урок №{shift_seq} е преместен на {new_shift_date.strftime('%d.%m.%Y')}. "
                                "Останалите уроци не бяха пренаредени."
                            )
                    st.rerun()

                st.markdown("---")
                st.subheader("🗜️ Уплътни графика (при отменен/грешно добавен неучебен ден)")
                st.caption(
                    "Ако премахнете (или изтриете) неучебен ден и вече не е нужен, тук може да "
                    "приберете освободеното място назад, вместо целият график да остане изместен. "
                    "Избраният урок и всичко след него се пренарежда на най-ранните свободни часове "
                    "според текущия календар — уроците ПРЕДИ него не се пипат."
                )
                suggested_anchor = suggest_compact_anchor(plan, topics, cls_schedule, cal_grade, year_end, school_year_start)
                if suggested_anchor is None:
                    st.caption("✅ Графикът вече е максимално уплътнен спрямо текущия календар.")
                else:
                    st.caption(f"💡 Предложение: изглежда има ненужна разлика от урок №{suggested_anchor} нататък.")
                    default_idx = min(suggested_anchor - 1, len(seq_options) - 1)
                    compact_choice = st.selectbox(
                        "Уплътни от урок:", options=seq_options, key=f"compact_choice_{cal_cls}", index=default_idx
                    )
                    compact_seq = lesson_seq_from_label(compact_choice)
                    if st.button("🗜️ Уплътни от тук нататък", key=f"compactbtn_{cal_cls}"):
                        new_plan, ov = compact_from(plan, compact_seq, cls_schedule, cal_grade, year_end, school_year_start)
                        save_plan(cal_cls, new_plan, topics, action_desc=f"Уплътни от урок №{compact_seq}")
                        st.session_state[f"flash_{cal_cls}"] = (
                            "success", f"Графикът за {cal_cls} е уплътнен от урок №{compact_seq} нататък."
                        )
                        st.rerun()

                st.markdown("---")
                st.subheader("🔀 Размяна на два урока (напр. отложи тест)")
                st.caption(
                    "Разменя само датите на два избрани урока — идеално за 'да разменя тест с обикновен "
                    "урок', без да местите нищо друго."
                )
                swp_col1, swp_col2 = st.columns(2)
                with swp_col1:
                    swap_a_choice = st.selectbox("Урок А:", options=seq_options, key=f"swapA_{cal_cls}")
                    swap_a_seq = lesson_seq_from_label(swap_a_choice)
                with swp_col2:
                    swap_b_choice = st.selectbox("Урок Б:", options=seq_options, key=f"swapB_{cal_cls}", index=min(1, len(seq_options) - 1))
                    swap_b_seq = lesson_seq_from_label(swap_b_choice)

                if swap_a_seq == swap_b_seq:
                    st.caption("Изберете два различни урока.")
                elif st.button("🔀 Размени датите", key=f"swapbtn_{cal_cls}"):
                    new_plan = [dict(l) for l in plan]
                    ia, ib = swap_a_seq - 1, swap_b_seq - 1
                    new_plan[ia]["date"], new_plan[ib]["date"] = new_plan[ib]["date"], new_plan[ia]["date"]
                    save_plan(cal_cls, new_plan, topics, action_desc=f"Размяна на урок №{swap_a_seq} и №{swap_b_seq}")
                    st.session_state[f"flash_{cal_cls}"] = (
                        "success",
                        f"Разменени дати: урок №{swap_a_seq} ↔ урок №{swap_b_seq}."
                    )
                    st.rerun()


                with st.expander("📋 Пълен списък с уроци (редактирайте дата на конкретен ред)"):
                    df_plan = pd.DataFrame([{
                        "seq": l["seq"],
                        "Предмет": normalize_subject(l.get("subject")),
                        "Тема": l["topic"],
                        "Вид": l["vid"],
                        "Дата": l["date"],
                    } for l in plan])
                    edited = st.data_editor(
                        df_plan,
                        column_config={
                            "seq": st.column_config.NumberColumn("№", disabled=True),
                            "Предмет": st.column_config.TextColumn("Предмет", disabled=True),
                            "Тема": st.column_config.TextColumn("Тема", disabled=True),
                            "Вид": st.column_config.TextColumn("Вид", disabled=True),
                            "Дата": st.column_config.DateColumn("Дата", format="DD.MM.YYYY"),
                        },
                        hide_index=True,
                        width='stretch',
                        height=400,
                        key=f"editor_{cal_cls}",
                    )
                    if st.button("💾 Запази ръчните промени в датите (без пренареждане)", key=f"save_{cal_cls}"):
                        new_plan = [dict(l) for l in plan]
                        for i, row in edited.iterrows():
                            new_plan[i]["date"] = row["Дата"] if isinstance(row["Дата"], datetime.date) else _d(str(row["Дата"]))
                        save_plan(cal_cls, new_plan, topics, action_desc="Ръчна промяна в таблицата")
                        st.session_state[f"flash_{cal_cls}"] = ("success", "Запазено!")
                        st.rerun()

                if st.button("♻️ Нулирай към стойностите от .docx (изтрива всички ръчни промени за този клас)", key=f"reset_standalone_{cal_cls}"):
                    new_plan, _ov, _hm = load_or_generate_plan(cal_cls, topics, cls_schedule, school_year_start, year_end, cal_grade, force_regenerate=True, action_desc="Нулиране по .docx")
                    st.session_state[f"flash_{cal_cls}"] = (
                        "success", f"Графикът за {cal_cls} е нулиран по текущия .docx."
                    )
                    st.rerun()

with tab3:
    st.header("📥 Експорт")
    if 'filtered_df' in locals() and not filtered_df.empty:
        export_cols = [c for c in ["Клас", "Предмет", "Раздел", "Раздел / Тема", "Основна дата", "Ден", "Резервна дата"] if c in filtered_df.columns]
        col_x1, col_x2 = st.columns(2)
        with col_x1:
            csv_d = filtered_df[export_cols].to_csv(index=False).encode('utf-8-sig')
            st.download_button("📄 Изтегли филтрирания график (CSV)", data=csv_d, file_name="grafik_kontrolni.csv", mime="text/csv", width='stretch')
        with col_x2:
            xlsx_bytes = build_xlsx_bytes(filtered_df[export_cols], CAL.get("source", ""))
            st.download_button(
                "📊 Изтегли филтрирания график (Excel)",
                data=xlsx_bytes,
                file_name="grafik_kontrolni.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width='stretch',
            )
    st.caption(f"Календарни данни: {CAL['source']}")
