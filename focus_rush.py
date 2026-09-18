import json
import csv
import io
from pathlib import Path
import random
import time
from datetime import date, datetime, timedelta
from urllib.request import Request, urlopen
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog


APP_NAME = "FOCUS RUSH"
DATA_FILE = Path(__file__).with_name("focus_rush_data.json")

RARITIES = ["COMMON", "RARE", "EPIC", "LEGENDARY"]
DROP_ITEMS = {
    "COMMON": ["Small Star", "Quick Start", "Paper Rush", "Focus Seed"],
    "RARE": ["Turbo Focus", "Speed Runner", "Blue Note", "Study Spark"],
    "EPIC": ["Boss Breaker", "Quest Hunter", "Golden Focus", "Rush Engine"],
    "LEGENDARY": ["Submission Slayer", "Ultimate Focus", "All Clear", "Focus Master"],
}
PET_MESSAGES = [
    "あと1ページ！",
    "いいペース！",
    "NEXT!",
    "そのまま行こう！",
    "QUESTを消していこう！",
]

DEFAULT_DATA = {
    "tasks": [],
    "xp": 0,
    "level": 1,
    "combo": 0,
    "streak": 0,
    "last_focus_date": None,
    "collections": [],
    "pet_xp": 0,
    "pet_level": 1,
    "daily": {},
    "study_log": {},
    "stopwatch": {
        "running": False,
        "elapsed": 0,
        "last_start_ts": None,
    },
    "achievements": [],
    "achievement_url": "",
    "achievement_last_updated": None,
    "achievement_counters": {
        "clears": 0,
        "completes": 0,
        "focus_seconds": 0,
    },
}



DEFAULT_ACHIEVEMENTS = [
    {
        "id": "first_clear",
        "name": "FIRST CLEAR",
        "description": "最初の単位をCLEARする",
        "condition_type": "clears",
        "target": 1,
        "xp": 20,
    },
    {
        "id": "clear_10",
        "name": "10 CLEAR",
        "description": "累計10単位をCLEARする",
        "condition_type": "clears",
        "target": 10,
        "xp": 50,
    },
    {
        "id": "quest_1",
        "name": "QUEST BREAKER",
        "description": "QUESTを1つCOMPLETEする",
        "condition_type": "completes",
        "target": 1,
        "xp": 50,
    },
    {
        "id": "focus_15",
        "name": "GOOD FOCUS",
        "description": "累計15分FOCUSする",
        "condition_type": "focus_seconds",
        "target": 900,
        "xp": 30,
    },
    {
        "id": "focus_60",
        "name": "ONE HOUR",
        "description": "累計60分FOCUSする",
        "condition_type": "focus_seconds",
        "target": 3600,
        "xp": 75,
    },
    {
        "id": "streak_7",
        "name": "7 DAY RUSH",
        "description": "7日連続でFOCUSする",
        "condition_type": "streak",
        "target": 7,
        "xp": 100,
    },
]


def normalize_achievement(row):
    """CSV 1行をアプリ内部形式へ正規化。"""
    row = {str(k).strip(): str(v).strip() for k, v in row.items() if k is not None}
    required = ("id", "name", "description", "condition_type", "target", "xp")
    if any(not row.get(k) for k in required):
        return None

    try:
        target = int(row["target"])
        xp = int(row["xp"])
    except ValueError:
        return None

    if target <= 0 or xp < 0:
        return None

    allowed = {"clears", "completes", "focus_seconds", "streak", "level", "tasks_total"}
    if row["condition_type"] not in allowed:
        return None

    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "condition_type": row["condition_type"],
        "target": target,
        "xp": xp,
    }


def parse_achievement_csv(csv_text):
    """UTF-8-SIG/UTF-8 のCSVを実績定義へ変換。"""
    stream = io.StringIO(csv_text)
    reader = csv.DictReader(stream)
    if not reader.fieldnames:
        raise ValueError("CSVヘッダーがありません。")

    achievements = []
    seen = set()
    for row in reader:
        achievement = normalize_achievement(row)
        if not achievement:
            continue
        if achievement["id"] in seen:
            continue
        seen.add(achievement["id"])
        achievement["unlocked"] = False
        achievement["unlocked_at"] = None
        achievements.append(achievement)

    if not achievements:
        raise ValueError("有効な実績が1件もありません。")

    return achievements


def fetch_achievement_csv(url, timeout=10):
    if not url:
        raise ValueError("実績CSV URLが設定されていません。")
    req = Request(
        url,
        headers={
            "User-Agent": "FOCUS-RUSH/1.0",
            "Accept": "text/csv,text/plain,*/*",
        },
    )
    with urlopen(req, timeout=timeout) as response:
        raw = response.read()
    return raw.decode("utf-8-sig")




def migrate_tasks(data):
    """旧形式(total/done)のQUESTを新しいページ番号形式へ移行する。"""
    for task in data.get("tasks", []):
        if "start_page" in task and "end_page" in task and "current_page" in task:
            try:
                task["start_page"] = int(task["start_page"])
                task["end_page"] = int(task["end_page"])
                task["current_page"] = int(task["current_page"])
            except (TypeError, ValueError):
                task["start_page"] = 1
                task["end_page"] = max(1, int(task.get("total", 1)))
                task["current_page"] = min(
                    task["end_page"],
                    task["start_page"] + max(0, int(task.get("done", 0)))
                )
        else:
            # 旧形式:
            # total=16, done=14 -> 1〜16, 現在14
            total = max(1, int(task.get("total", 1)))
            done = min(total, max(0, int(task.get("done", 0))))
            task["start_page"] = 1
            task["end_page"] = total
            task["current_page"] = done if done > 0 else 1
        if task["end_page"] < task["start_page"]:
            task["start_page"], task["end_page"] = task["end_page"], task["start_page"]
        task["current_page"] = max(
            task["start_page"],
            min(task["end_page"], task["current_page"])
        )
        task.setdefault("skipped", False)
        task.setdefault("skipped_at", None)
    return data


def load_data():
    if not DATA_FILE.exists():
        return json.loads(json.dumps(DEFAULT_DATA))
    try:
        with DATA_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return merge_defaults(data, DEFAULT_DATA)
    except Exception:
        backup = DATA_FILE.with_suffix(".broken.json")
        try:
            DATA_FILE.replace(backup)
        except OSError:
            pass
        return json.loads(json.dumps(DEFAULT_DATA))


def merge_defaults(data, defaults):
    if isinstance(defaults, dict):
        if not isinstance(data, dict):
            data = {}
        for key, value in defaults.items():
            if key not in data:
                data[key] = json.loads(json.dumps(value))
            elif isinstance(value, dict):
                data[key] = merge_defaults(data[key], value)
        return data
    return data


def initialize_achievements(data):
    # CSVがまだ一度も取得されていなければ、内蔵実績を利用。
    if not data.get("achievements"):
        data["achievements"] = json.loads(json.dumps(DEFAULT_ACHIEVEMENTS))
    for achievement in data.get("achievements", []):
        achievement.setdefault("unlocked", False)
        achievement.setdefault("unlocked_at", None)
    return data


def save_data(data):
    tmp = DATA_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(DATA_FILE)


def now_ts():
    return time.time()


def today_str():
    return date.today().isoformat()


def fmt_seconds(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    if m:
        return f"{m}:{s:02d}"
    return f"{s}秒"


def fmt_long(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}時間{m}分{s}秒"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


def xp_needed(level):
    # Lv.1 -> 100, Lv.2 -> 150, Lv.3 -> 200, ...
    return 100 + max(0, level - 1) * 50


def total_xp_for_level(level):
    return sum(xp_needed(lv) for lv in range(1, level))


def ensure_level(data):
    while data["xp"] >= total_xp_for_level(data["level"] + 1):
        data["level"] += 1



def task_first_page(task):
    return int(task.get("start_page", 1))


def task_last_page(task):
    return int(task.get("end_page", task.get("total", 1)))


def task_next_page(task):
    start = task_first_page(task)
    current = int(task.get("current_page", start))
    if not task.get("started", False):
        return start
    return current + 1


def task_next_range(task, max_pages=3):
    start = task_next_page(task)
    end = task_last_page(task)
    if start > end:
        return None
    return start, min(end, start + max_pages - 1)


def task_remaining_pages(task):
    start = task_first_page(task)
    end = task_last_page(task)
    current = int(task.get("current_page", start))
    if not task.get("started", False):
        return end - start + 1
    return max(0, end - current)


def current_level_progress(data):
    level = data["level"]
    start = total_xp_for_level(level)
    need = xp_needed(level)
    current = max(0, data["xp"] - start)
    return current, need


def task_progress(task):
    start = int(task.get("start_page", 1))
    end = int(task.get("end_page", task.get("total", 1)))
    current = int(task.get("current_page", start))
    if end < start:
        start, end = end, start
    current = max(start, min(end, current))
    total = end - start + 1
    done = current - start
    # 「現在ページ」が開始ページを意味するため、
    # まだ開始していない場合は0ページCLEAR、開始ページを終えたら1ページCLEAR。
    if task.get("current_page", start) == start and task.get("started", False) is not True:
        done = 0
    return done, total, max(0, min(1, done / max(1, total)))


def add_seconds_to_today(data, seconds):
    if seconds <= 0:
        return
    key = today_str()
    data["study_log"][key] = int(data["study_log"].get(key, 0)) + int(seconds)


def stopwatch_current_elapsed(data):
    sw = data["stopwatch"]
    elapsed = int(sw.get("elapsed", 0))
    if sw.get("running") and sw.get("last_start_ts"):
        elapsed += max(0, int(now_ts() - sw["last_start_ts"]))
    return elapsed


def settle_running_stopwatch(data):
    sw = data["stopwatch"]
    if sw.get("running") and sw.get("last_start_ts"):
        delta = max(0, int(now_ts() - sw["last_start_ts"]))
        sw["elapsed"] = int(sw.get("elapsed", 0)) + delta
        add_seconds_to_today(data, delta)
        sw["last_start_ts"] = now_ts()


class FocusRushApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("980x760")
        self.minsize(900, 680)

        self.data = load_data()
        migrate_tasks(self.data)
        initialize_achievements(self.data)
        settle_running_stopwatch(self.data)
        self.focus_running = False
        self.focus_remaining = 300
        self.focus_started_task_id = None
        self.focus_gain = 0
        self.focus_after_id = None
        self.stopwatch_after_id = None
        self.last_stopwatch_tick = time.time()

        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        self.colors = {
            "bg": "#0f1115",
            "panel": "#171a21",
            "panel2": "#1f2430",
            "text": "#f2f4f8",
            "sub": "#a8b0bf",
            "accent": "#7dd3fc",
            "success": "#86efac",
            "warning": "#facc15",
            "danger": "#f87171",
        }
        self.configure(bg=self.colors["bg"])
        self._configure_styles()

        self.main = ttk.Frame(self, padding=14)
        self.main.pack(fill="both", expand=True)

        self.header = ttk.Frame(self.main)
        self.header.pack(fill="x", pady=(0, 10))

        self.header_title = ttk.Label(self.header, text=APP_NAME, style="Title.TLabel")
        self.header_title.pack(side="left")

        self.header_stats = ttk.Label(self.header, style="Header.TLabel")
        self.header_stats.pack(side="right")

        self.notebook = ttk.Notebook(self.main)
        self.notebook.pack(fill="both", expand=True)

        self.home_tab = ttk.Frame(self.notebook, padding=10)
        self.stopwatch_tab = ttk.Frame(self.notebook, padding=10)
        self.stats_tab = ttk.Frame(self.notebook, padding=10)
        self.collection_tab = ttk.Frame(self.notebook, padding=10)
        self.achievements_tab = ttk.Frame(self.notebook, padding=10)

        self.notebook.add(self.home_tab, text="HOME")
        self.notebook.add(self.stopwatch_tab, text="STOPWATCH")
        self.notebook.add(self.stats_tab, text="STUDY RECORD")
        self.notebook.add(self.collection_tab, text="COLLECTION")
        self.notebook.add(self.achievements_tab, text="ACHIEVEMENTS")

        self.build_home()
        self.build_stopwatch()
        self.build_stats()
        self.build_collection()
        self.build_achievements()

        self.ensure_daily_quests()
        self.refresh_all()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(1000, self.tick)

    def _configure_styles(self):
        self.style.configure(".", font=("Segoe UI", 11))
        self.style.configure("TFrame", background=self.colors["bg"])
        self.style.configure("Panel.TFrame", background=self.colors["panel"])
        self.style.configure("Panel2.TFrame", background=self.colors["panel2"])
        self.style.configure("TLabel", background=self.colors["bg"], foreground=self.colors["text"])
        self.style.configure("Panel.TLabel", background=self.colors["panel"], foreground=self.colors["text"])
        self.style.configure("Sub.TLabel", background=self.colors["bg"], foreground=self.colors["sub"])
        self.style.configure("Title.TLabel", font=("Segoe UI", 24, "bold"),
                             background=self.colors["bg"], foreground=self.colors["text"])
        self.style.configure("Header.TLabel", font=("Segoe UI", 12, "bold"),
                             background=self.colors["bg"], foreground=self.colors["accent"])
        self.style.configure("Section.TLabel", font=("Segoe UI", 15, "bold"),
                             background=self.colors["panel"], foreground=self.colors["text"])
        self.style.configure("Hero.TLabel", font=("Segoe UI", 30, "bold"),
                             background=self.colors["panel"], foreground=self.colors["text"])
        self.style.configure("Big.TLabel", font=("Segoe UI", 18, "bold"),
                             background=self.colors["panel"], foreground=self.colors["text"])
        self.style.configure("Mono.TLabel", font=("Consolas", 26, "bold"),
                             background=self.colors["panel"], foreground=self.colors["text"])
        self.style.configure("Accent.TButton", font=("Segoe UI", 12, "bold"))
        self.style.configure("Danger.TButton", font=("Segoe UI", 11, "bold"))
        self.style.configure("Treeview", rowheight=30, font=("Segoe UI", 10))
        self.style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        self.style.configure("TNotebook", background=self.colors["bg"], borderwidth=0)
        self.style.configure("TNotebook.Tab", padding=(14, 8), font=("Segoe UI", 10, "bold"))
        self.style.configure("TProgressbar", thickness=14)

    def make_panel(self, parent, title=None):
        frame = ttk.Frame(parent, style="Panel.TFrame", padding=14)
        if title:
            ttk.Label(frame, text=title, style="Section.TLabel").pack(anchor="w", pady=(0, 10))
        return frame

    # ---------- HOME ----------
    def build_home(self):
        top = ttk.Frame(self.home_tab)
        top.pack(fill="x", pady=(0, 10))
        self.home_message = ttk.Label(top, style="Header.TLabel")
        self.home_message.pack(side="left")
        self.manage_task_btn = ttk.Button(top, text="QUEST管理", command=self.manage_tasks_dialog)
        self.manage_task_btn.pack(side="right", padx=(0, 6))
        self.add_task_btn = ttk.Button(top, text="+ QUEST追加", command=self.add_task_dialog)
        self.add_task_btn.pack(side="right")

        content = ttk.Panedwindow(self.home_tab, orient="horizontal")
        content.pack(fill="both", expand=True)

        left = ttk.Frame(content)
        right = ttk.Frame(content)
        content.add(left, weight=3)
        content.add(right, weight=2)

        self.quest_panel = self.make_panel(left, "TODAY'S QUEST")
        self.quest_panel.pack(fill="both", expand=True, padx=(0, 7))

        self.quest_container = ttk.Frame(self.quest_panel, style="Panel.TFrame")
        self.quest_container.pack(fill="both", expand=True)

        q_buttons = ttk.Frame(self.quest_panel, style="Panel.TFrame")
        q_buttons.pack(fill="x", pady=(12, 0))
        self.focus_button = ttk.Button(q_buttons, text="FOCUS RUN", style="Accent.TButton",
                                       command=self.start_focus_from_recommended)
        self.focus_button.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(q_buttons, text="+1 CLEAR", command=self.manual_clear_recommended).pack(
            side="left", fill="x", expand=True, padx=5
        )
        self.skip_button = ttk.Button(q_buttons, text="⏭ 飛ばす", command=self.skip_recommended)
        self.skip_button.pack(side="left", fill="x", expand=True, padx=(5, 0))

        self.next_panel = self.make_panel(right, "NEXT REWARD")
        self.next_panel.pack(fill="x", pady=(0, 7))

        self.xp_summary = ttk.Label(self.next_panel, style="Big.TLabel")
        self.xp_summary.pack(anchor="w")
        self.xp_bar = ttk.Progressbar(self.next_panel, maximum=100)
        self.xp_bar.pack(fill="x", pady=8)
        self.next_xp_label = ttk.Label(self.next_panel, style="Panel.TLabel")
        self.next_xp_label.pack(anchor="w")
        self.next_action_label = ttk.Label(self.next_panel, style="Panel.TLabel", wraplength=300)
        self.next_action_label.pack(anchor="w", pady=(8, 0))

        self.daily_panel = self.make_panel(right, "DAILY QUEST")
        self.daily_panel.pack(fill="x", pady=7)
        self.daily_container = ttk.Frame(self.daily_panel, style="Panel.TFrame")
        self.daily_container.pack(fill="x")

        self.pet_panel = self.make_panel(right, "FOCUS PET")
        self.pet_panel.pack(fill="x", pady=(7, 0))
        self.pet_label = ttk.Label(self.pet_panel, text="", style="Big.TLabel")
        self.pet_label.pack(anchor="w")
        self.pet_progress = ttk.Progressbar(self.pet_panel, maximum=100)
        self.pet_progress.pack(fill="x", pady=7)
        self.pet_message_label = ttk.Label(self.pet_panel, text="", style="Panel.TLabel", wraplength=300)
        self.pet_message_label.pack(anchor="w")

    def get_open_tasks(self):
        """未完了かつスキップされていないQUESTだけを候補にする。"""
        tasks = []
        for t in self.data["tasks"]:
            if t.get("skipped", False):
                continue
            if task_remaining_pages(t) > 0:
                tasks.append(t)
        return tasks

    def recommended_task(self):
        tasks = self.get_open_tasks()
        if not tasks:
            return None
        today = date.today()

        def key(t):
            remain = task_remaining_pages(t)
            start_page = task_first_page(t)
            end_page = task_last_page(t)
            total = max(1, end_page - start_page + 1)
            if t.get("started", False):
                current_page = int(t.get("current_page", start_page))
                done = max(0, min(total, current_page - start_page))
            else:
                done = 0

            due_raw = t.get("due")
            if due_raw:
                try:
                    due = date.fromisoformat(due_raw)
                    overdue = (due - today).days
                    due_score = overdue
                except ValueError:
                    due_score = 999
            else:
                due_score = 999

            progress = done / total
            subject_bonus = 0
            return (remain, due_score, -progress, -subject_bonus, t.get("name", ""))

        return sorted(tasks, key=key)[0]

    def render_progress(self, parent, done, total, width=40):
        frac = done / max(1, total)
        filled = round(frac * width)
        return "█" * filled + "░" * (width - filled)

    def refresh_home(self):
        # Header
        cur, need = current_level_progress(self.data)
        self.header_stats.config(
            text=f"LV.{self.data['level']}   XP {cur}/{need}   COMBO x{self.data['combo']}   STREAK {self.data['streak']}D"
        )

        self.home_message.config(text=self.next_home_message())

        for child in self.quest_container.winfo_children():
            child.destroy()

        task = self.recommended_task()
        if task:
            done, total, frac = task_progress(task)
            first_page = task_first_page(task)
            last_page = task_last_page(task)
            current_page = int(task.get("current_page", first_page))
            ttk.Label(self.quest_container, text=task["name"], style="Big.TLabel").pack(anchor="w")
            meta = []
            if task.get("subject"):
                meta.append(f"教科: {task['subject']}")
            if task.get("due"):
                meta.append(f"期限: {task['due']}")
            if meta:
                ttk.Label(self.quest_container, text=" / ".join(meta),
                          style="Panel.TLabel").pack(anchor="w", pady=(2, 4))

            ttk.Label(
                self.quest_container,
                text=self.render_progress(self.quest_container, done, total),
                style="Mono.TLabel"
            ).pack(anchor="w", pady=(5, 0))
            if task.get("started", False):
                status_text = f"{first_page}〜{last_page}  /  現在 {current_page}ページ"
            else:
                status_text = f"{first_page}〜{last_page}ページ"
            ttk.Label(
                self.quest_container, text=status_text,
                style="Panel.TLabel"
            ).pack(anchor="w")

            remain = task_remaining_pages(task)
            next_range = task_next_range(task, 3)
            if next_range:
                nr_start, nr_end = next_range
                if nr_start == nr_end:
                    action_text = f"次にやる：{nr_start}ページ"
                else:
                    action_text = f"次にやる：{nr_start}〜{nr_end}ページ"
            else:
                action_text = "QUEST COMPLETE"
            ttk.Label(
                self.quest_container, text=f"あと{remain}ページ",
                style="Hero.TLabel"
            ).pack(anchor="w", pady=(8, 0))
            ttk.Label(
                self.quest_container, text=action_text,
                style="Big.TLabel"
            ).pack(anchor="w", pady=(2, 0))

            self.focus_button.config(text=f"FOCUS RUN  •  {task['name']}")
            self.focus_button.state(["!disabled"])
            self.skip_button.config(text="⏭ 飛ばす")
            self.skip_button.state(["!disabled"])
        else:
            skipped_tasks = [t for t in self.data.get("tasks", []) if t.get("skipped", False) and task_remaining_pages(t) > 0]
            if skipped_tasks:
                ttk.Label(
                    self.quest_container, text="ALL AVAILABLE QUESTS CLEAR", style="Hero.TLabel"
                ).pack(anchor="center", pady=(20, 8))
                ttk.Label(
                    self.quest_container,
                    text=f"飛ばしているQUESTが {len(skipped_tasks)} 件あります。\nQUEST管理から再開できます。",
                    style="Panel.TLabel", justify="center"
                ).pack(anchor="center")
            else:
                ttk.Label(self.quest_container, text="提出物 ALL CLEAR", style="Hero.TLabel").pack(anchor="center", pady=25)
                ttk.Label(
                    self.quest_container,
                    text="未完了QUESTはありません。\n今日は完了を維持しましょう。",
                    style="Panel.TLabel",
                    justify="center"
                ).pack(anchor="center")
            self.focus_button.config(text="FOCUS RUN")
            self.focus_button.state(["disabled"])
            self.skip_button.config(text="⏭ 飛ばす")
            self.skip_button.state(["disabled"])

        cur, need = current_level_progress(self.data)
        self.xp_summary.config(text=f"LV.{self.data['level']}   {cur} / {need} XP")
        self.xp_bar["value"] = (cur / max(1, need)) * 100
        self.next_xp_label.config(text=f"あと {need - cur} XP → LEVEL UP")

        estimate = self.estimate_levelup()
        if estimate:
            self.next_action_label.config(
                text=f"次の目安: {estimate[1]} を {estimate[0]} 回CLEARでLEVEL UPに近づく"
            )
        else:
            self.next_action_label.config(text="あと少しのXPでLEVEL UP！")

        self.render_daily()
        self.render_pet()

    def next_home_message(self):
        if not self.get_open_tasks():
            return "提出物 ALL CLEAR!"
        task = self.recommended_task()
        remaining = task_remaining_pages(task)
        next_range = task_next_range(task, 3)
        if next_range:
            a, b = next_range
            page_text = f"{a}ページ" if a == b else f"{a}〜{b}ページ"
        else:
            page_text = "完了"
        return f"NEXT: {task['name']} / 次にやる {page_text} / あと{remaining}ページ"

    def estimate_levelup(self):
        tasks = self.get_open_tasks()
        if not tasks:
            return None
        remain = min(task_remaining_pages(t) for t in tasks)
        t = self.recommended_task()
        return max(1, min(remain, 5)), t["name"]

    # ---------- DAILY QUEST ----------
    def daily_templates(self):
        return [
            {"id": "focus15", "text": "15分集中", "target": 900, "kind": "focus_seconds", "xp": 50},
            {"id": "clear3", "text": "3ページCLEAR", "target": 3, "kind": "clears", "xp": 50},
            {"id": "complete2", "text": "2つのQUEST COMPLETE", "target": 2, "kind": "completes", "xp": 50},
        ]

    def ensure_daily_quests(self):
        key = today_str()
        d = self.data["daily"].get(key)
        if not d:
            d = {
                "quests": [
                    {
                        **tpl,
                        "value": 0,
                        "done": False,
                    }
                    for tpl in self.daily_templates()
                ]
            }
            self.data["daily"] = {key: d}
        else:
            self.data["daily"] = {key: d}
        save_data(self.data)

    def daily_value(self, kind):
        d = self.data["daily"][today_str()]
        return sum(q["value"] for q in d["quests"] if q["kind"] == kind) if kind in {
            "focus_seconds", "clears", "completes"
        } else 0

    def add_daily_progress(self, kind, amount):
        d = self.data["daily"][today_str()]
        changed = False
        for q in d["quests"]:
            if q["kind"] == kind and not q["done"]:
                q["value"] += amount
                if q["value"] >= q["target"]:
                    q["value"] = q["target"]
                    q["done"] = True
                    self.gain_xp(q["xp"], pet=True)
                    changed = True
        if changed:
            all_done = all(q["done"] for q in d["quests"])
            if all_done:
                self.gain_xp(50, pet=True)
                self.award_drop(force_min="RARE")
                messagebox.showinfo("DAILY QUEST COMPLETE", "DAILY QUEST COMPLETE!\n+50 XP\nDROP!")
        save_data(self.data)

    def render_daily(self):
        for child in self.daily_container.winfo_children():
            child.destroy()
        d = self.data["daily"][today_str()]
        for q in d["quests"]:
            icon = "✓" if q["done"] else "□"
            value = f"{q['value']}/{q['target']}"
            ttk.Label(
                self.daily_container,
                text=f"{icon} {q['text']}   {value}" + ("  COMPLETE" if q["done"] else ""),
                style="Panel.TLabel"
            ).pack(anchor="w", pady=2)

    # ---------- PET / DROP ----------
    def gain_xp(self, amount, pet=True):
        amount = int(amount)
        before_level = self.data["level"]
        self.data["xp"] += amount
        if pet:
            self.data["pet_xp"] += max(1, amount)
            while self.data["pet_xp"] >= self.data["pet_level"] * 100:
                self.data["pet_xp"] -= self.data["pet_level"] * 100
                self.data["pet_level"] += 1
                try:
                    messagebox.showinfo("NEW FORM!", f"FOCUS PET LV.{self.data['pet_level']}!\nNEW FORM!")
                except tk.TclError:
                    pass
        ensure_level(self.data)
        if self.data["level"] > before_level:
            try:
                messagebox.showinfo("LEVEL UP", f"LEVEL UP!\nLV.{self.data['level']}")
            except tk.TclError:
                pass

    def render_pet(self):
        pet_level = self.data["pet_level"]
        need = pet_level * 100
        cur = self.data["pet_xp"]
        self.pet_label.config(text=f"FOCUS PET   LV.{pet_level}    [ CAT ]")
        self.pet_progress["value"] = (cur / max(1, need)) * 100
        if self.focus_running:
            self.pet_message_label.config(text=random.choice(PET_MESSAGES))
        else:
            self.pet_message_label.config(text="FOCUS RUNを始めるとリアクションします。")

    def award_drop(self, force_min=None):
        weights = [65, 25, 8, 2]
        if force_min == "RARE":
            weights = [0, 72, 23, 5]
        rarity = random.choices(RARITIES, weights=weights, k=1)[0]
        item = random.choice(DROP_ITEMS[rarity])
        if item not in self.data["collections"]:
            self.data["collections"].append(item)
        save_data(self.data)
        messagebox.showinfo("DROP!", f"{rarity} DROP!\n\n{item}")

    # ---------- TASKS ----------
    def add_task_dialog(self):
        self.task_edit_dialog(task=None)

    def task_edit_dialog(self, task=None, parent=None):
        """新規QUEST追加 / 既存QUEST編集を共通化。"""
        win = tk.Toplevel(parent or self)
        win.title("QUEST編集" if task else "QUEST追加")
        win.geometry("460x420")
        win.transient(parent or self)
        win.grab_set()

        current_task = task or {}

        fields = {}
        specs = [
            ("提出物名", "name"),
            ("開始ページ", "start_page"),
            ("終了ページ", "end_page"),
            ("現在のページ", "current_page"),
            ("期限 (YYYY-MM-DD / 任意)", "due"),
            ("教科 (任意)", "subject"),
        ]
        defaults = {
            "start_page": str(current_task.get("start_page", 1)),
            "end_page": str(current_task.get("end_page", 10)),
            "current_page": (
                str(current_task.get("current_page", 1))
                if task and task.get("started", False)
                else ""
            ),
            "due": current_task.get("due", ""),
            "subject": current_task.get("subject", ""),
        }

        for i, (label, key) in enumerate(specs):
            ttk.Label(win, text=label).grid(row=i, column=0, sticky="w", padx=12, pady=8)
            e = ttk.Entry(win, width=32)
            e.grid(row=i, column=1, padx=12, pady=8)
            if key == "name":
                e.insert(0, current_task.get("name", ""))
            else:
                e.insert(0, defaults[key])
            fields[key] = e

        # 編集時の補足表示
        note_text = (
            "「現在のページ」は、まだ開始していない場合は空欄でも構いません。"
            if not task else
            "現在ページを変更すると、次のCLEAR位置も変更されます。"
        )
        ttk.Label(
            win, text=note_text, style="Sub.TLabel",
            wraplength=420, justify="left"
        ).grid(row=len(specs), column=0, columnspan=2, sticky="w", padx=12, pady=(4, 12))

        def submit():
            name = fields["name"].get().strip()
            try:
                start_page = int(fields["start_page"].get())
                end_page = int(fields["end_page"].get())
                current_text = fields["current_page"].get().strip()

                if current_text:
                    current_page = int(current_text)
                    started = True
                else:
                    # 未開始なら開始ページより1つ前を保持し、表示だけ「未開始」とする。
                    current_page = start_page
                    started = False
            except ValueError:
                messagebox.showerror("入力エラー", "ページ番号は整数で入力してください。", parent=win)
                return

            if not name:
                messagebox.showerror("入力エラー", "提出物名を入力してください。", parent=win)
                return
            if start_page <= 0 or end_page < start_page:
                messagebox.showerror("入力エラー", "開始ページと終了ページを確認してください。", parent=win)
                return
            if started and not (start_page <= current_page <= end_page):
                messagebox.showerror(
                    "入力エラー",
                    "現在のページは開始ページ〜終了ページの範囲内にしてください。",
                    parent=win
                )
                return

            due = fields["due"].get().strip()
            if due:
                try:
                    date.fromisoformat(due)
                except ValueError:
                    messagebox.showerror(
                        "入力エラー", "期限はYYYY-MM-DD形式です。", parent=win
                    )
                    return

            if task is None:
                new_task = {
                    "id": str(int(now_ts() * 1000000)),
                    "name": name,
                    "start_page": start_page,
                    "end_page": end_page,
                    "current_page": current_page,
                    "started": started,
                    "skipped": False,
                    "skipped_at": None,
                    "due": due,
                    "subject": fields["subject"].get().strip(),
                    "created": today_str(),
                }
                self.data["tasks"].append(new_task)
                target = new_task
            else:
                task["name"] = name
                task["start_page"] = start_page
                task["end_page"] = end_page
                task["current_page"] = min(end_page, max(start_page, current_page))
                task["started"] = started
                task["due"] = due
                task["subject"] = fields["subject"].get().strip()
                target = task

            save_data(self.data)
            win.destroy()
            self.refresh_all()

            # 編集/追加直後に100%なら自動完了はせず、管理上は完了状態にする。
            if task is not None and target.get("started") and task_last_page(target) == int(target["current_page"]):
                self.refresh_all()

        ttk.Button(
            win,
            text="保存" if task else "QUEST追加",
            command=submit
        ).grid(row=len(specs) + 1, column=0, columnspan=2, pady=14)

    def manage_tasks_dialog(self):
        """全QUESTの一覧から編集・削除できる管理画面。"""
        win = tk.Toplevel(self)
        win.title("QUEST管理")
        win.geometry("820x520")
        win.transient(self)

        ttk.Label(
            win, text="QUEST管理", style="Section.TLabel"
        ).pack(anchor="w", padx=12, pady=(12, 4))

        ttk.Label(
            win,
            text="未完了・完了済みを含むすべてのQUESTを管理できます。",
            style="Sub.TLabel"
        ).pack(anchor="w", padx=12, pady=(0, 10))

        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=12)

        cols = ("name", "range", "current", "remaining", "due", "subject", "status")
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        headings = {
            "name": "提出物",
            "range": "範囲",
            "current": "現在",
            "remaining": "残り",
            "due": "期限",
            "subject": "教科",
            "status": "状態",
        }
        widths = {
            "name": 190, "range": 100, "current": 80,
            "remaining": 80, "due": 105, "subject": 90, "status": 90
        }
        for col in cols:
            tree.heading(col, text=headings[col])
            tree.column(col, width=widths[col], anchor="w")
        tree.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        sb.pack(side="right", fill="y")
        tree.configure(yscrollcommand=sb.set)

        def populate():
            for item in tree.get_children():
                tree.delete(item)
            for t in self.data.get("tasks", []):
                start_page = task_first_page(t)
                end_page = task_last_page(t)
                if t.get("started", False):
                    current = int(t.get("current_page", start_page))
                    current_text = f"{current}p"
                else:
                    current_text = "未開始"

                remaining = task_remaining_pages(t)
                if remaining == 0:
                    status = "COMPLETE"
                elif t.get("skipped", False):
                    status = "SKIPPED"
                else:
                    status = "ACTIVE"
                tree.insert(
                    "", "end",
                    iid=str(t["id"]),
                    values=(
                        t.get("name", ""),
                        f"{start_page}〜{end_page}",
                        current_text,
                        f"{remaining}p",
                        t.get("due", ""),
                        t.get("subject", ""),
                        status,
                    )
                )

        def selected_task():
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("QUEST管理", "編集または削除するQUESTを選択してください。", parent=win)
                return None
            return self.find_task(selected[0])

        def edit_selected():
            target = selected_task()
            if not target:
                return
            self.task_edit_dialog(target, parent=win)
            # child dialog is modal; refresh after it closes.
            populate()

        def toggle_skip_selected():
            target = selected_task()
            if not target:
                return
            if task_remaining_pages(target) == 0:
                messagebox.showinfo("QUEST管理", "完了済みのQUESTは飛ばせません。", parent=win)
                return

            target["skipped"] = not target.get("skipped", False)
            target["skipped_at"] = (
                datetime.now().isoformat(timespec="seconds")
                if target["skipped"] else None
            )
            save_data(self.data)
            self.refresh_all()
            populate()

        def delete_selected():
            target = selected_task()
            if not target:
                return

            confirm = messagebox.askyesno(
                "QUEST削除",
                f"「{target['name']}」を削除しますか？\n\nこの操作は元に戻せません。",
                parent=win
            )
            if not confirm:
                return

            self.data["tasks"] = [
                t for t in self.data["tasks"] if t.get("id") != target.get("id")
            ]

            # 削除されたQUESTがFOCUS中なら、安全にFOCUSを終了。
            if self.focus_running and self.focus_started_task_id == target.get("id"):
                self.abort_focus()

            save_data(self.data)
            self.refresh_all()
            populate()

        def add_new():
            self.task_edit_dialog(task=None, parent=win)
            populate()

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=12)
        ttk.Button(btns, text="+ 新規QUEST", command=add_new).pack(side="left")
        ttk.Button(btns, text="編集", command=edit_selected).pack(side="left", padx=8)
        ttk.Button(btns, text="⏭ 飛ばす / 再開", command=toggle_skip_selected).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="削除", command=delete_selected).pack(side="left")
        ttk.Button(btns, text="閉じる", command=win.destroy).pack(side="right")

        tree.bind("<Double-1>", lambda _event: edit_selected())
        populate()

    def find_task(self, task_id):
        return next((t for t in self.data["tasks"] if t["id"] == task_id), None)

    def clear_task_unit(self, task, amount=1):
        if not task:
            return

        start_page = task_first_page(task)
        end_page = task_last_page(task)
        current_page = int(task.get("current_page", start_page))
        was_started = bool(task.get("started", False))

        pages_to_clear = max(1, int(amount))
        if not was_started:
            # 最初のCLEARで開始ページを完了扱いにする。
            new_current = min(end_page, start_page)
            gained = 1
            task["started"] = True
        else:
            if current_page >= end_page:
                return
            new_current = min(end_page, current_page + pages_to_clear)
            gained = new_current - current_page

        task["current_page"] = new_current

        self.data["combo"] += gained
        self.increment_achievement_counter("clears", gained)
        xp = 10 * gained
        self.gain_xp(xp, pet=True)
        self.add_daily_progress("clears", gained)

        task_completed = task["current_page"] >= end_page
        save_data(self.data)
        self.refresh_all()

        if task_completed:
            self.complete_task(task)
        else:
            next_range = task_next_range(task, 1)
            next_text = ""
            if next_range:
                a, b = next_range
                next_text = f"\n次にやる：{a}ページ" if a == b else f"\n次にやる：{a}〜{b}ページ"
            messagebox.showinfo(
                "PAGE CLEAR!",
                f"{task['name']}\n\n"
                f"範囲 {start_page}〜{end_page}ページ\n"
                f"現在 {task['current_page']}ページ\n"
                f"あと{task_remaining_pages(task)}ページ\n"
                f"+{xp} XP\n"
                f"COMBO x{self.data['combo']}\n\n"
                f"PAGE CLEAR!{next_text}"
            )

    def complete_task(self, task):
        task["current_page"] = task_last_page(task)
        task["started"] = True

        self.data["combo"] += 1
        self.increment_achievement_counter("completes", 1)
        self.gain_xp(50, pet=True)
        self.add_daily_progress("completes", 1)

        self.award_drop()
        save_data(self.data)
        self.refresh_all()

        messagebox.showinfo(
            "BOSS CLEAR",
            f"BOSS CLEAR\\n\\n"
            f"{task['name']}\\n"
            f"{task_first_page(task)}〜{task_last_page(task)}ページ\\n"
            f"100%\\n\\n"
            f"+50 XP\\n"
            f"DROP!"
        )

        # FOCUS RUN中なら、完了後も次のQUESTを操作できるように
        # 現在のFOCUS画面を閉じてホームへ戻す。
        if self.focus_running:
            self.focus_running = False
            try:
                self.focus_win.destroy()
            except Exception:
                pass
            self.focus_win = None
            self.focus_button.config(text="FOCUS RUN")

        next_task = self.recommended_task()
        if next_task:
            next_range = task_next_range(next_task, 3)
            if next_range:
                a, b = next_range
                page_text = f"{a}ページ" if a == b else f"{a}〜{b}ページ"
            else:
                page_text = "COMPLETE"

            messagebox.showinfo(
                "NEXT QUEST",
                f"NEXT QUEST\\n\\n"
                f"{next_task['name']}\\n"
                f"次にやる：{page_text}"
            )
        else:
            messagebox.showinfo(
                "ALL CLEAR",
                "提出物 ALL CLEAR!\\n今日は完全クリアです。"
            )

    def skip_recommended(self):
        task = self.recommended_task()
        if not task:
            return
        if not messagebox.askyesno(
            "QUESTを飛ばす",
            f"「{task['name']}」を一時的に飛ばしますか？\n\n"
            "削除はされません。\n"
            "あとからQUEST管理で再開できます。"
        ):
            return

        task["skipped"] = True
        task["skipped_at"] = datetime.now().isoformat(timespec="seconds")
        save_data(self.data)
        self.refresh_all()

        next_task = self.recommended_task()
        if next_task:
            next_range = task_next_range(next_task, 3)
            if next_range:
                a, b = next_range
                page_text = f"{a}ページ" if a == b else f"{a}〜{b}ページ"
            else:
                page_text = "COMPLETE"
            messagebox.showinfo(
                "NEXT QUEST",
                f"⏭ {task['name']} を飛ばしました。\n\n"
                f"NEXT QUEST\n{next_task['name']}\n次にやる：{page_text}"
            )
        else:
            messagebox.showinfo(
                "QUEST SKIPPED",
                f"⏭ {task['name']} を飛ばしました。\n\n"
                "現在、利用可能なQUESTはありません。\n"
                "QUEST管理から再開できます。"
            )

    def manual_clear_recommended(self):
        self.clear_task_unit(self.recommended_task())

    # ---------- FOCUS RUN ----------
    def start_focus_from_recommended(self):
        task = self.recommended_task()
        if not task:
            return
        if self.focus_running:
            return
        self.focus_started_task_id = task["id"]
        self.focus_remaining = 300
        self.focus_gain = 0
        self.focus_running = True
        self.focus_button.config(text="FOCUS RUN中…")
        self.open_focus_popup(task)

    def open_focus_popup(self, task):
        self.focus_win = tk.Toplevel(self)
        self.focus_win.title("FOCUS RUN")
        self.focus_win.geometry("600x520")
        self.focus_win.transient(self)

        frame = ttk.Frame(self.focus_win, style="Panel.TFrame", padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="FOCUS RUN", style="Section.TLabel").pack(anchor="w")
        self.focus_task_label = ttk.Label(frame, text="", style="Big.TLabel")
        self.focus_task_label.pack(anchor="w", pady=(10, 4))

        self.focus_progress_label = ttk.Label(frame, text="", style="Panel.TLabel")
        self.focus_progress_label.pack(anchor="w")

        self.focus_bar = ttk.Progressbar(frame, maximum=100)
        self.focus_bar.pack(fill="x", pady=12)

        self.focus_timer_label = ttk.Label(frame, text="05:00", style="Mono.TLabel")
        self.focus_timer_label.pack(pady=12)

        self.focus_status_label = ttk.Label(frame, text="まず5分だけ。", style="Panel.TLabel")
        self.focus_status_label.pack(pady=4)

        self.focus_gain_label = ttk.Label(frame, text="+0 XP", style="Big.TLabel")
        self.focus_gain_label.pack(pady=4)

        btns = ttk.Frame(frame, style="Panel.TFrame")
        btns.pack(fill="x", pady=15)
        self.focus_clear_button = ttk.Button(
            btns, text="+1 CLEAR", command=self.focus_manual_clear, style="Accent.TButton"
        )
        self.focus_clear_button.pack(
            side="left", fill="x", expand=True, padx=(0, 5)
        )
        ttk.Button(btns, text="⏭ QUESTを飛ばす", command=self.skip_focus_task).pack(
            side="left", fill="x", expand=True, padx=5
        )
        ttk.Button(btns, text="CLOSE / ABORT", command=self.abort_focus).pack(
            side="left", fill="x", expand=True, padx=(5, 0)
        )

        self.update_focus_popup()

    def focus_manual_clear(self):
        """FOCUS RUN中の+1 CLEAR。モーダル表示を挟まず即座に画面を更新する。"""
        task = self.find_task(self.focus_started_task_id)
        if not task or not self.focus_running:
            return

        start_page = task_first_page(task)
        end_page = task_last_page(task)
        current_page = int(task.get("current_page", start_page))
        was_started = bool(task.get("started", False))

        # 最初の+1は開始ページをCLEAR扱いにする。
        if not was_started:
            task["started"] = True
            new_current = start_page
            gained = 1
        elif current_page < end_page:
            new_current = current_page + 1
            gained = 1
        else:
            return

        task["current_page"] = new_current

        self.data["combo"] += gained
        self.increment_achievement_counter("clears", gained)
        self.gain_xp(10 * gained, pet=True)
        self.add_daily_progress("clears", gained)
        self.focus_gain += 10 * gained

        # 最終ページなら、そのままBOSS CLEARへ。
        completed = new_current >= end_page
        save_data(self.data)

        if completed:
            self.complete_task(task)
            return

        self.refresh_home()
        self.refresh_header_only()
        self.update_focus_popup()
        self.focus_status_label.config(
            text=f"PAGE CLEAR!  →  次は {task_next_range(task, 1)[0]}ページ"
            if task_next_range(task, 1)
            else "PAGE CLEAR!"
        )
        self.focus_gain_label.config(text=f"Session +{self.focus_gain} XP")
        save_data(self.data)

    def update_focus_popup(self):
        if not self.focus_running:
            return
        task = self.find_task(self.focus_started_task_id)
        if not task:
            self.abort_focus()
            return

        done, total, frac = task_progress(task)
        self.focus_task_label.config(text=task["name"])
        next_range = task_next_range(task, 3)
        if next_range:
            a, b = next_range
            next_text = f"{a}ページ" if a == b else f"{a}〜{b}ページ"
        else:
            next_text = "COMPLETE"
        current_display = int(task.get("current_page", task_first_page(task)))
        if not task.get("started", False):
            current_display = task_first_page(task) - 1
        self.focus_progress_label.config(
            text=f"{task_first_page(task)}〜{task_last_page(task)}ページ   •   "
                 f"現在 {current_display}ページ   •   "
                 f"あと{task_remaining_pages(task)}ページ   •   次にやる {next_text}"
        )
        self.focus_bar["value"] = frac * 100
        mins, secs = divmod(max(0, int(self.focus_remaining)), 60)
        self.focus_timer_label.config(text=f"{mins:02d}:{secs:02d}")
        self.focus_status_label.config(
            text=f"🔥 COMBO x{self.data['combo']}     LV.{self.data['level']}     {random.choice(PET_MESSAGES)}"
        )
        self.focus_gain_label.config(text=f"Session +{self.focus_gain} XP")

    def tick_focus(self):
        if not self.focus_running:
            return
        self.focus_remaining -= 1
        if self.focus_remaining in (240, 180, 120, 60):
            elapsed = 300 - self.focus_remaining
            msg = {
                60: "GOOD FOCUS",
                120: "いいペース！",
                180: f"COMBO x{max(1, self.data['combo'])}",
                240: "あと少し！",
            }.get(elapsed, "FOCUS")
            self.focus_status_label.config(text=msg)
        if self.focus_remaining <= 0:
            self.finish_focus()
        else:
            self.update_focus_popup()

    def finish_focus(self):
        if not self.focus_running:
            return
        self.focus_running = False
        self.data["combo"] += 1
        self.increment_achievement_counter("focus_seconds", 300)
        self.gain_xp(15, pet=True)
        self.add_daily_progress("focus_seconds", 300)
        # Record 5 actual minutes in the study log as a completed focus session.
        add_seconds_to_today(self.data, 300)
        self.award_drop()
        save_data(self.data)
        try:
            self.focus_win.destroy()
        except Exception:
            pass
        self.focus_win = None
        self.focus_button.config(text="FOCUS RUN")
        self.refresh_all()
        messagebox.showinfo(
            "SESSION CLEAR!",
            "SESSION CLEAR!\n\n+15 XP\nCOMBO UP\nDROP!\n\n次の5分へ進めます。"
        )

    def skip_focus_task(self):
        if not self.focus_running:
            return
        task = self.find_task(self.focus_started_task_id)
        if not task:
            self.abort_focus()
            return

        if not messagebox.askyesno(
            "QUESTを飛ばす",
            f"「{task['name']}」を一時的に飛ばしますか？\n\n"
            "現在のページ進捗はそのまま残ります。",
            parent=self.focus_win
        ):
            return

        task["skipped"] = True
        task["skipped_at"] = datetime.now().isoformat(timespec="seconds")
        save_data(self.data)

        self.focus_running = False
        try:
            self.focus_win.destroy()
        except Exception:
            pass
        self.focus_win = None
        self.focus_button.config(text="FOCUS RUN")
        self.refresh_all()

        next_task = self.recommended_task()
        if next_task:
            next_range = task_next_range(next_task, 3)
            if next_range:
                a, b = next_range
                page_text = f"{a}ページ" if a == b else f"{a}〜{b}ページ"
            else:
                page_text = "COMPLETE"
            messagebox.showinfo(
                "NEXT QUEST",
                f"⏭ {task['name']} を飛ばしました。\n\n"
                f"NEXT QUEST\n{next_task['name']}\n次にやる：{page_text}"
            )

    def abort_focus(self):
        if not self.focus_running:
            return
        self.focus_running = False
        try:
            self.focus_win.destroy()
        except Exception:
            pass
        self.focus_win = None
        self.focus_button.config(text="FOCUS RUN")
        self.refresh_all()

    # ---------- STOPWATCH ----------
    def build_stopwatch(self):
        self.sw_title = ttk.Label(self.stopwatch_tab, text="STOPWATCH", style="Section.TLabel")
        self.sw_title.pack(anchor="w", pady=(0, 10))

        panel = self.make_panel(self.stopwatch_tab)
        panel.pack(fill="x")

        self.sw_big = ttk.Label(panel, text="00:00:00", style="Mono.TLabel")
        self.sw_big.pack(pady=(20, 10))

        self.sw_today_label = ttk.Label(panel, text="", style="Big.TLabel")
        self.sw_today_label.pack(pady=5)

        buttons = ttk.Frame(panel, style="Panel.TFrame")
        buttons.pack(fill="x", pady=15)
        self.sw_start_btn = ttk.Button(buttons, text="START", command=self.stopwatch_start)
        self.sw_pause_btn = ttk.Button(buttons, text="PAUSE", command=self.stopwatch_pause)
        self.sw_reset_btn = ttk.Button(buttons, text="RESET", command=self.stopwatch_reset)
        self.sw_start_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.sw_pause_btn.pack(side="left", fill="x", expand=True, padx=4)
        self.sw_reset_btn.pack(side="left", fill="x", expand=True, padx=(4, 0))

        note = ttk.Label(
            self.stopwatch_tab,
            text="FOCUS RUNとは別管理。ここは学校の勉強記録用に、実測時間を保存します。",
            style="Sub.TLabel",
            wraplength=850,
        )
        note.pack(anchor="w", pady=12)

        self.sw_week_label = ttk.Label(self.stopwatch_tab, style="Big.TLabel")
        self.sw_week_label.pack(anchor="w", pady=(10, 4))
        self.sw_month_label = ttk.Label(self.stopwatch_tab, style="Big.TLabel")
        self.sw_month_label.pack(anchor="w", pady=4)

    def stopwatch_start(self):
        sw = self.data["stopwatch"]
        if sw["running"]:
            return
        sw["running"] = True
        sw["last_start_ts"] = now_ts()
        save_data(self.data)
        self.refresh_stopwatch()

    def stopwatch_pause(self):
        settle_running_stopwatch(self.data)
        self.data["stopwatch"]["running"] = False
        self.data["stopwatch"]["last_start_ts"] = None
        save_data(self.data)
        self.refresh_all()

    def stopwatch_reset(self):
        if not messagebox.askyesno("RESET", "現在のストップウォッチ計測を0にしますか？"):
            return
        self.data["stopwatch"] = {"running": False, "elapsed": 0, "last_start_ts": None}
        save_data(self.data)
        self.refresh_all()

    def refresh_stopwatch(self):
        elapsed = stopwatch_current_elapsed(self.data)
        self.sw_big.config(text=time.strftime("%H:%M:%S", time.gmtime(elapsed)))
        today = int(self.data["study_log"].get(today_str(), 0))
        # If the stopwatch is currently running, current session time isn't yet in study_log.
        if self.data["stopwatch"]["running"]:
            today += max(0, int(now_ts() - self.data["stopwatch"]["last_start_ts"]))
        self.sw_today_label.config(text=f"今日の勉強時間  {fmt_long(today)}")
        self.sw_start_btn.state(["disabled"] if self.data["stopwatch"]["running"] else ["!disabled"])
        self.sw_pause_btn.state(["!disabled"] if self.data["stopwatch"]["running"] else ["disabled"])

        now = date.today()
        week_start = now - timedelta(days=now.weekday())
        week_total = 0
        month_total = 0
        for k, v in self.data["study_log"].items():
            try:
                d = date.fromisoformat(k)
            except ValueError:
                continue
            if week_start <= d <= now:
                week_total += int(v)
            if d.year == now.year and d.month == now.month:
                month_total += int(v)
        self.sw_week_label.config(text=f"今週  {fmt_long(week_total)}")
        self.sw_month_label.config(text=f"今月  {fmt_long(month_total)}")

    # ---------- STATS ----------
    def build_stats(self):
        ttk.Label(self.stats_tab, text="STUDY RECORD", style="Section.TLabel").pack(anchor="w", pady=(0, 10))

        summary = ttk.Frame(self.stats_tab)
        summary.pack(fill="x")
        self.stats_today = ttk.Label(summary, style="Big.TLabel")
        self.stats_week = ttk.Label(summary, style="Big.TLabel")
        self.stats_month = ttk.Label(summary, style="Big.TLabel")
        for label in (self.stats_today, self.stats_week, self.stats_month):
            label.pack(side="left", expand=True, anchor="w", padx=6, pady=8)

        frame = ttk.Frame(self.stats_tab)
        frame.pack(fill="both", expand=True, pady=10)

        columns = ("date", "seconds", "human")
        self.stats_tree = ttk.Treeview(frame, columns=columns, show="headings")
        self.stats_tree.heading("date", text="日付")
        self.stats_tree.heading("seconds", text="秒")
        self.stats_tree.heading("human", text="表示")
        self.stats_tree.column("date", width=140)
        self.stats_tree.column("seconds", width=100, anchor="e")
        self.stats_tree.column("human", width=240)
        self.stats_tree.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.stats_tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.stats_tree.configure(yscrollcommand=scrollbar.set)

        btns = ttk.Frame(self.stats_tab)
        btns.pack(fill="x")
        ttk.Button(btns, text="日別データ更新", command=self.refresh_stats).pack(side="left")
        ttk.Button(btns, text="JSONを開く", command=self.open_data_folder).pack(side="left", padx=8)

    def refresh_stats(self):
        for item in self.stats_tree.get_children():
            self.stats_tree.delete(item)
        rows = []
        for k, v in self.data["study_log"].items():
            rows.append((k, int(v), fmt_long(v)))
        for row in sorted(rows, reverse=True):
            self.stats_tree.insert("", "end", values=row)

        now = date.today()
        week_start = now - timedelta(days=now.weekday())
        today_total = int(self.data["study_log"].get(today_str(), 0))
        week_total = 0
        month_total = 0
        for k, v in self.data["study_log"].items():
            try:
                d = date.fromisoformat(k)
            except ValueError:
                continue
            if week_start <= d <= now:
                week_total += int(v)
            if d.year == now.year and d.month == now.month:
                month_total += int(v)

        self.stats_today.config(text=f"今日\n{fmt_long(today_total)}")
        self.stats_week.config(text=f"今週\n{fmt_long(week_total)}")
        self.stats_month.config(text=f"今月\n{fmt_long(month_total)}")

    def open_data_folder(self):
        messagebox.showinfo("データファイル", str(DATA_FILE.resolve()))

    # ---------- COLLECTION ----------
    def build_collection(self):
        ttk.Label(self.collection_tab, text="FOCUS COLLECTION", style="Section.TLabel").pack(anchor="w", pady=(0, 10))

        self.collection_summary = ttk.Label(self.collection_tab, style="Big.TLabel")
        self.collection_summary.pack(anchor="w", pady=(0, 10))

        frame = ttk.Frame(self.collection_tab)
        frame.pack(fill="both", expand=True)

        self.collection_tree = ttk.Treeview(frame, columns=("rarity", "name", "owned"), show="headings")
        self.collection_tree.heading("rarity", text="Rarity")
        self.collection_tree.heading("name", text="Item")
        self.collection_tree.heading("owned", text="Status")
        self.collection_tree.column("rarity", width=140)
        self.collection_tree.column("name", width=260)
        self.collection_tree.column("owned", width=120)
        self.collection_tree.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(frame, orient="vertical", command=self.collection_tree.yview)
        sb.pack(side="right", fill="y")
        self.collection_tree.configure(yscrollcommand=sb.set)

    def collection_rarity(self, item):
        for rarity, items in DROP_ITEMS.items():
            if item in items:
                return rarity
        return "UNKNOWN"

    def refresh_collection(self):
        for item in self.collection_tree.get_children():
            self.collection_tree.delete(item)

        all_items = []
        for rarity in RARITIES:
            for item in DROP_ITEMS[rarity]:
                all_items.append((rarity, item, item in self.data["collections"]))

        owned = sum(1 for _, _, ok in all_items if ok)
        self.collection_summary.config(text=f"{owned} / {len(all_items)} COLLECTION")

        for rarity, item, ok in all_items:
            self.collection_tree.insert(
                "", "end",
                values=(rarity, item, "✓ GET" if ok else "□ LOCKED")
            )

    # ---------- ACHIEVEMENTS ----------
    def build_achievements(self):
        ttk.Label(
            self.achievements_tab,
            text="ACHIEVEMENTS",
            style="Section.TLabel"
        ).pack(anchor="w", pady=(0, 10))

        url_frame = ttk.Frame(self.achievements_tab)
        url_frame.pack(fill="x", pady=(0, 10))

        ttk.Label(url_frame, text="CSV URL:").pack(side="left")
        self.achievement_url_var = tk.StringVar(value=self.data.get("achievement_url", ""))
        self.achievement_url_entry = ttk.Entry(url_frame, textvariable=self.achievement_url_var)
        self.achievement_url_entry.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(url_frame, text="URLから更新", command=self.update_achievements_from_url).pack(side="left")

        self.achievement_status = ttk.Label(
            self.achievements_tab,
            text="",
            style="Sub.TLabel",
            wraplength=850
        )
        self.achievement_status.pack(anchor="w", pady=(0, 8))

        frame = ttk.Frame(self.achievements_tab)
        frame.pack(fill="both", expand=True)

        self.achievement_tree = ttk.Treeview(
            frame,
            columns=("status", "name", "description", "target", "reward"),
            show="headings"
        )
        self.achievement_tree.heading("status", text="STATUS")
        self.achievement_tree.heading("name", text="実績")
        self.achievement_tree.heading("description", text="条件")
        self.achievement_tree.heading("target", text="目標")
        self.achievement_tree.heading("reward", text="XP")
        self.achievement_tree.column("status", width=90)
        self.achievement_tree.column("name", width=180)
        self.achievement_tree.column("description", width=360)
        self.achievement_tree.column("target", width=90, anchor="e")
        self.achievement_tree.column("reward", width=70, anchor="e")
        self.achievement_tree.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(frame, orient="vertical", command=self.achievement_tree.yview)
        sb.pack(side="right", fill="y")
        self.achievement_tree.configure(yscrollcommand=sb.set)

    def achievement_current_value(self, achievement):
        kind = achievement["condition_type"]
        if kind == "clears":
            # 累計CLEAR数を保存せず、daily以外の値から逆算しないため、
            # 進捗用カウンタを data に持つ。
            return int(self.data.get("achievement_counters", {}).get("clears", 0))
        if kind == "completes":
            return int(self.data.get("achievement_counters", {}).get("completes", 0))
        if kind == "focus_seconds":
            return int(self.data.get("achievement_counters", {}).get("focus_seconds", 0))
        if kind == "streak":
            return int(self.data.get("streak", 0))
        if kind == "level":
            return int(self.data.get("level", 1))
        if kind == "tasks_total":
            return int(len(self.data.get("tasks", [])))
        return 0

    def check_achievements(self):
        unlocked_now = []
        for achievement in self.data.get("achievements", []):
            if achievement.get("unlocked"):
                continue
            current = self.achievement_current_value(achievement)
            if current >= int(achievement["target"]):
                achievement["unlocked"] = True
                achievement["unlocked_at"] = datetime.now().isoformat(timespec="seconds")
                self.gain_xp(int(achievement.get("xp", 0)), pet=True)
                unlocked_now.append(achievement)
        if unlocked_now:
            save_data(self.data)
            names = "\n".join(
                f"🏆 {a['name']}  +{int(a.get('xp', 0))} XP"
                for a in unlocked_now
            )
            messagebox.showinfo("ACHIEVEMENT UNLOCKED", names)

    def increment_achievement_counter(self, kind, amount):
        counters = self.data.setdefault("achievement_counters", {})
        counters[kind] = int(counters.get(kind, 0)) + int(amount)
        self.check_achievements()

    def refresh_achievements(self):
        if not hasattr(self, "achievement_tree"):
            return
        for item in self.achievement_tree.get_children():
            self.achievement_tree.delete(item)

        achievements = self.data.get("achievements", [])
        unlocked = sum(1 for a in achievements if a.get("unlocked"))
        self.achievement_status.config(
            text=f"{unlocked} / {len(achievements)} UNLOCKED"
                 + (f"  • 最終更新: {self.data.get('achievement_last_updated')}"
                    if self.data.get("achievement_last_updated") else "")
        )

        for a in achievements:
            current = self.achievement_current_value(a)
            target = int(a["target"])
            status = "✓ CLEAR" if a.get("unlocked") else "□ LOCKED"
            target_text = f"{min(current, target)}/{target}"
            self.achievement_tree.insert(
                "", "end",
                values=(
                    status,
                    a["name"],
                    a["description"],
                    target_text,
                    f"+{int(a.get('xp', 0))}"
                )
            )

    def update_achievements_from_url(self):
        url = self.achievement_url_var.get().strip()
        if not url:
            messagebox.showerror("CSV URL", "CSVのURLを入力してください。")
            return
        if not (url.startswith("http://") or url.startswith("https://")):
            messagebox.showerror("CSV URL", "http:// または https:// のURLを指定してください。")
            return

        try:
            csv_text = fetch_achievement_csv(url)
            new_defs = parse_achievement_csv(csv_text)
        except Exception as exc:
            messagebox.showerror(
                "CSV取得エラー",
                f"実績CSVを取得できませんでした。\n\n{exc}\n\n"
                "前回の実績データはそのまま残ります。"
            )
            return

        # 既存実績の解放状態をIDで引き継ぐ。
        old_by_id = {a.get("id"): a for a in self.data.get("achievements", [])}
        for a in new_defs:
            old = old_by_id.get(a["id"])
            if old:
                a["unlocked"] = bool(old.get("unlocked"))
                a["unlocked_at"] = old.get("unlocked_at")

        self.data["achievements"] = new_defs
        self.data["achievement_url"] = url
        self.data["achievement_last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_data(self.data)
        self.refresh_achievements()
        messagebox.showinfo(
            "実績更新完了",
            f"{len(new_defs)}件の実績をCSVから読み込みました。"
        )

    # ---------- GLOBAL TICK / STREAK ----------
    def tick(self):
        # Stopwatch: commit whole elapsed seconds at a regular cadence.
        sw = self.data["stopwatch"]
        if sw.get("running") and sw.get("last_start_ts"):
            # Only settle occasionally to keep JSON accurate if the process is closed unexpectedly.
            if time.time() - self.last_stopwatch_tick >= 5:
                settle_running_stopwatch(self.data)
                self.last_stopwatch_tick = time.time()
                save_data(self.data)

        if self.focus_running:
            self.tick_focus()

        self.refresh_stopwatch()
        self.refresh_header_only()
        self.after(1000, self.tick)

    def refresh_header_only(self):
        cur, need = current_level_progress(self.data)
        self.header_stats.config(
            text=f"LV.{self.data['level']}   XP {cur}/{need}   COMBO x{self.data['combo']}   STREAK {self.data['streak']}D"
        )

    def refresh_all(self):
        self.ensure_daily_quests()
        self.update_streak_if_needed()
        self.refresh_home()
        self.refresh_stopwatch()
        self.refresh_stats()
        self.refresh_collection()
        self.refresh_achievements()
        save_data(self.data)

    def update_streak_if_needed(self):
        last = self.data.get("last_focus_date")
        today = today_str()
        if last == today:
            return

        # Streak increments when a focus session is completed today.
        # The increment happens on session completion, not merely app open.
        # Keep stored value until then.
        if last and last < today:
            try:
                last_d = date.fromisoformat(last)
                if (date.today() - last_d).days > 1:
                    self.data["streak"] = 0
            except ValueError:
                self.data["streak"] = 0

    def mark_focus_day(self):
        today = today_str()
        last = self.data.get("last_focus_date")
        if last == today:
            return
        if last:
            try:
                diff = (date.today() - date.fromisoformat(last)).days
            except ValueError:
                diff = 999
        else:
            diff = 999
        if diff == 1:
            self.data["streak"] += 1
        else:
            self.data["streak"] = 1
        self.data["last_focus_date"] = today
        self.check_achievements()

    def on_close(self):
        if self.focus_running:
            if not messagebox.askyesno("終了確認", "FOCUS RUN中です。終了しますか？"):
                return
            self.abort_focus()

        settle_running_stopwatch(self.data)
        # On close, stopwatch remains running so it can be restored accurately next launch.
        save_data(self.data)
        self.destroy()


if __name__ == "__main__":
    app = FocusRushApp()
    # Mark focus day at the end of a session, via monkey patch-free wrapper:
    original_finish_focus = app.finish_focus

    def wrapped_finish_focus():
        original_finish_focus()
        # finish_focus may display dialogs; record the streak after successful completion.
        app.mark_focus_day()
        save_data(app.data)
        app.refresh_all()

    app.finish_focus = wrapped_finish_focus
    app.mainloop()
