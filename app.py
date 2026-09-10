import datetime
import json
import os
import docx
import pandas as pd
import streamlit as st

# --- 1. Календар на МОН (Ваканции и неучебни дни) ---
# Зарежда се от calendar_<school_year>.json, за да може лесно да се
# замени с актуалната заповед на МОН всяка следваща учебна година,
# без да се пипа кодът на приложението.
CALENDAR_FILE = "calendar_2026_2027.json"

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

CONFIG_FILE = "schedule_config.json"
RAZPREDELENIA_DIR = "razpredelenia"

def parse_docx_distribution(file_path_or_bytes):
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

    topics = []
    for table in doc.tables:
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
                    topics.append({"seq": seq, "topic": cells[0], "hours": hours, "vid": "", "theme": "", "is_exam": True})
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

PLANS_DIR = "lesson_plans"

def compute_topics_hash(topics):
    sig = [[t.get("seq"), t.get("topic"), t.get("vid")] for t in topics]
    raw = json.dumps(sig, ensure_ascii=False)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()

def simulate_plan(topics, cls_schedule, start_date, year_end, grade_str):
    """Разпределя урок по урок (1 урок = 1 астрономически час) по реалните
    учебни дни на класа, като спазва ваканциите и неучебните дни."""
    plan = []
    cur = start_date
    idx = 0
    n = len(topics)
    # горна граница за да не влезем в безкраен цикъл при празен график
    hard_stop = year_end + datetime.timedelta(days=1)
    while idx < n and cur <= hard_stop:
        if is_school_day(cur, grade_str) and cur.weekday() in cls_schedule:
            hrs = cls_schedule[cur.weekday()]
            for _ in range(hrs):
                if idx >= n:
                    break
                plan.append({**topics[idx], "date": cur})
                idx += 1
        cur += datetime.timedelta(days=1)
    return plan, idx < n  # (план, overflow?)

def _simulate_forward(topics_slice, start_date, cls_schedule, grade_str, year_end, hours_used_on_start_date=0):
    """Попълва дати за topics_slice, продължавайки от start_date: първо
    остатъчните свободни часове на самия start_date (ако има), после ден
    по ден напред. Използва се и за преместване, и за синхронизация."""
    result = []
    idx = 0
    n = len(topics_slice)
    cur = start_date
    if is_school_day(cur, grade_str) and cur.weekday() in cls_schedule:
        free_today = max(0, cls_schedule[cur.weekday()] - hours_used_on_start_date)
        for _ in range(free_today):
            if idx >= n:
                break
            result.append({**topics_slice[idx], "date": cur})
            idx += 1
    cur += datetime.timedelta(days=1)
    hard_stop = year_end + datetime.timedelta(days=60)
    while idx < n and cur <= hard_stop:
        if is_school_day(cur, grade_str) and cur.weekday() in cls_schedule:
            for _ in range(cls_schedule[cur.weekday()]):
                if idx >= n:
                    break
                result.append({**topics_slice[idx], "date": cur})
                idx += 1
        cur += datetime.timedelta(days=1)
    return result, idx < n  # (резултат, overflow?)

def save_plan(cls, plan, topics):
    os.makedirs(PLANS_DIR, exist_ok=True)
    path = os.path.join(PLANS_DIR, f"{cls}.json")
    serial = []
    for l in plan:
        row = {k: v for k, v in l.items() if k != "date"}
        row["date"] = l["date"].isoformat()
        serial.append(row)
    payload = {"source_hash": compute_topics_hash(topics), "lessons": serial}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def load_plan_raw(cls):
    """Чете запазения план от диска без да го сверява с текущия .docx.
    Връща (lessons_или_None, stored_hash_или_None). stored_hash е None и
    за файлове от по-стара версия на приложението (без записан хеш)."""
    path = os.path.join(PLANS_DIR, f"{cls}.json")
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return None, None
    if isinstance(raw, dict) and "lessons" in raw:
        lessons, stored_hash = raw["lessons"], raw.get("source_hash")
    else:
        lessons, stored_hash = raw, None  # стар формат (v9-v14) - без хеш
    for r in lessons:
        r["date"] = _d(r["date"])
    return lessons, stored_hash

def load_or_generate_plan(cls, topics, cls_schedule, start_date, year_end, grade_str, force_regenerate=False):
    """Зарежда персистирания план. Ако липсва, генерира го от .docx.
    Ако вече съществува, но .docx междувременно се е променил (различен
    хеш), НЕ презаписва автоматично — връща стария план с hash_mismatch=True,
    за да може потребителят съзнателно да избере синхронизация/нулиране
    (вижте таб 'Календар'). За да не губи ръчни редакции без предупреждение."""
    if not force_regenerate:
        lessons, stored_hash = load_plan_raw(cls)
        if lessons is not None:
            if stored_hash == compute_topics_hash(topics):
                return lessons, False, False
            return lessons, False, True
    plan, overflow = simulate_plan(topics, cls_schedule, start_date, year_end, grade_str)
    save_plan(cls, plan, topics)
    return plan, overflow, False

def sync_plan(old_plan, new_topics, cls_schedule, start_date, year_end, grade_str):
    """Синхронизира персистирания план с редактиран .docx: пази датите на
    уроците, които не са се променили (по тема+вид, позиция по позиция от
    началото), а всичко след първата разлика се преразпределя наново от
    датата на последния запазен урок."""
    divergence = 0
    for i in range(min(len(old_plan), len(new_topics))):
        if old_plan[i]["topic"] == new_topics[i]["topic"] and old_plan[i]["vid"] == new_topics[i]["vid"]:
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
                bg = "#f4f4f4"
            if reason:
                bg = VACATION_BG
                content.append(
                    f"<div style='color:{VACATION_COLOR}; font-weight:600; font-size:12px; margin-top:2px;'>🔴 {_html.escape(reason)}</div>"
                )
            else:
                lessons = plan_by_date.get(day, [])
                for l in lessons:
                    label = _html.escape(l["topic"][:36])
                    emoji, color = vid_badge(l.get("vid", ""))
                    if l.get("is_exam"):
                        bg = EXAM_BG
                        content.append(
                            f"<div style='color:{color}; font-weight:700; font-size:13px; margin-top:2px;'>{emoji} {label}</div>"
                        )
                    else:
                        content.append(
                            f"<div style='color:#333; font-size:13px; margin-top:2px;'>{emoji} {label}</div>"
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

    cols = [c for c in ["Клас", "Раздел", "Раздел / Тема", "Основна дата", "Ден", "Резервна дата"] if c in df.columns]
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

    widths = {"Клас": 7, "Раздел": 30, "Раздел / Тема": 42, "Основна дата": 14, "Ден": 10, "Резервна дата": 14}
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

# Автоматично зареждане на файлове от папката razpredelenia по випуск
if not os.path.exists(RAZPREDELENIA_DIR):
    os.makedirs(RAZPREDELENIA_DIR)

for f_name in os.listdir(RAZPREDELENIA_DIR):
    if f_name.endswith(".docx"):
        # Очаква име на файл от рода на '7.docx' или '7_klas.docx'
        grade_key = f_name.split(".")[0].replace("_klas", "").strip()
        f_path = os.path.join(RAZPREDELENIA_DIR, f_name)
        parsed = parse_docx_distribution(f_path)
        if parsed:
            st.session_state.grade_files[grade_key] = parsed

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

cls_name = f"{s_grade}.{s_letter}"

col_b1, col_b2 = st.sidebar.columns(2)
with col_b1:
    if st.button(f"➕ Добави", width='stretch'):
        st.session_state.weekly_schedule[selected_day] = [
            item for item in st.session_state.weekly_schedule[selected_day] if item["class"] != cls_name
        ]
        st.session_state.weekly_schedule[selected_day].append({"class": cls_name, "hours": s_hours, "grade": s_grade})
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
    uploaded_f = st.sidebar.file_uploader(f"Файл за {target_grade} клас", type=["docx"], key=f"file_{target_grade}")
    
    if uploaded_f:
        parsed_topics = parse_docx_distribution(uploaded_f)
        if parsed_topics:
            st.session_state.grade_files[target_grade] = parsed_topics
            file_save_name = f"{target_grade}.docx"
            with open(os.path.join(RAZPREDELENIA_DIR, file_save_name), "wb") as f:
                f.write(uploaded_f.getbuffer())
            st.sidebar.success(f"Запазено за цял {target_grade} клас!")

school_year_start = st.sidebar.date_input("Начало на учебната година", CAL["start"])

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
                    st.info(f"🏫 **{e['class']}** — {e['hours']} ч.")
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
                cls_schedule = {}
                for day, items in st.session_state.weekly_schedule.items():
                    for item in items:
                        if item["class"] == cls:
                            cls_schedule[day_map[day]] = item["hours"]

                topics = st.session_state.grade_files[cls_grade]
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
                    if hash_mismatch:
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

            df_res = pd.DataFrame(results).sort_values(by=["raw_date", "Клас"])

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

            display_cols = [c for c in ["Клас", "Раздел", "Раздел / Тема", "Основна дата", "Ден", "Резервна дата"] if c in filtered_df.columns]
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
            topics = st.session_state.grade_files[cal_grade]
            if not topics or not topics[0].get("vid"):
                st.info(
                    "Календарният изглед работи само с новия формат на годишно "
                    "разпределение на МОН (с номерирани уроци). Файлът за този "
                    "випуск изглежда в стария опростен формат — вижте таб "
                    "'График за Контролни' вместо това."
                )
            else:
                day_map = {"Понеделник": 0, "Вторник": 1, "Сряда": 2, "Четвъртък": 3, "Петък": 4}
                cls_schedule = {}
                for day, items in st.session_state.weekly_schedule.items():
                    for item in items:
                        if item["class"] == cal_cls:
                            cls_schedule[day_map[day]] = item["hours"]

                year_end = end_of_year_for_grade(cal_grade)
                plan, overflow, hash_mismatch = load_or_generate_plan(cal_cls, topics, cls_schedule, school_year_start, year_end, cal_grade)

                flash_key = f"flash_{cal_cls}"
                if flash_key in st.session_state:
                    level, msg = st.session_state.pop(flash_key)
                    getattr(st, level)(msg)

                dismiss_key = f"dismiss_mismatch_{cal_cls}"
                if hash_mismatch and not st.session_state.get(dismiss_key, False):
                    st.warning(
                        f"⚠️ Качен е нов или редактиран .docx за {cal_grade} клас, различен от "
                        f"последно запазения график за {cal_cls}. Какво да направя?"
                    )
                    mcol1, mcol2, mcol3 = st.columns(3)
                    with mcol1:
                        if st.button(
                            "🔄 Синхронизирай", key=f"syncbtn_{cal_cls}", width='stretch',
                            help="Запазва датите на уроците, които не са се променили, и преразпределя "
                                 "само промените/новите уроци напред от там."
                        ):
                            new_plan, _ov = sync_plan(plan, topics, cls_schedule, school_year_start, year_end, cal_grade)
                            save_plan(cal_cls, new_plan, topics)
                            st.session_state.pop(dismiss_key, None)
                            st.session_state[flash_key] = ("success", f"Графикът за {cal_cls} е синхронизиран с новия .docx.")
                            st.rerun()
                    with mcol2:
                        if st.button(
                            "♻️ Нулирай по .docx", key=f"resetbtn_top_{cal_cls}", width='stretch',
                            help="Изтрива всички ръчни промени и генерира графика наново от началото на годината."
                        ):
                            new_plan, _ov, _hm = load_or_generate_plan(cal_cls, topics, cls_schedule, school_year_start, year_end, cal_grade, force_regenerate=True)
                            st.session_state.pop(dismiss_key, None)
                            st.session_state[flash_key] = ("success", f"Графикът за {cal_cls} е нулиран по текущия .docx.")
                            st.rerun()
                    with mcol3:
                        if st.button(
                            "➡️ Продължи със стария", key=f"dismissbtn_{cal_cls}", width='stretch',
                            help="Игнорирай промените в .docx за момента (ще напомня пак при следващо отваряне)."
                        ):
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
                    seq_options = [f"№{l['seq']} — {l['topic'][:45]}" for l in plan]
                    shift_choice = st.selectbox("Урок за преместване:", options=seq_options, key=f"shiftchoice_{cal_cls}")
                    shift_seq = int(shift_choice.split("—")[0].replace("№", "").strip())
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
                        save_plan(cal_cls, new_plan, topics)
                        st.session_state[f"flash_{cal_cls}"] = (
                            "success", f"Графикът за {cal_cls} е пренареден от урок №{shift_seq} нататък."
                        )
                    else:
                        new_plan = [dict(l) for l in plan]
                        new_plan[shift_seq - 1]["date"] = new_shift_date
                        save_plan(cal_cls, new_plan, topics)
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

                with st.expander("📋 Пълен списък с уроци (редактирайте дата на конкретен ред)"):
                    df_plan = pd.DataFrame([{
                        "seq": l["seq"],
                        "Тема": l["topic"],
                        "Вид": l["vid"],
                        "Дата": l["date"],
                    } for l in plan])
                    edited = st.data_editor(
                        df_plan,
                        column_config={
                            "seq": st.column_config.NumberColumn("№", disabled=True),
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
                        save_plan(cal_cls, new_plan, topics)
                        st.session_state[f"flash_{cal_cls}"] = ("success", "Запазено!")
                        st.rerun()

                if st.button("♻️ Нулирай към стойностите от .docx (изтрива всички ръчни промени за този клас)", key=f"reset_{cal_cls}"):
                    new_plan, _ov, _hm = load_or_generate_plan(cal_cls, topics, cls_schedule, school_year_start, year_end, cal_grade, force_regenerate=True)
                    st.session_state[f"flash_{cal_cls}"] = (
                        "success", f"Графикът за {cal_cls} е нулиран по текущия .docx."
                    )
                    st.rerun()

with tab3:
    st.header("📥 Експорт")
    if 'filtered_df' in locals() and not filtered_df.empty:
        export_cols = [c for c in ["Клас", "Раздел", "Раздел / Тема", "Основна дата", "Ден", "Резервна дата"] if c in filtered_df.columns]
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
