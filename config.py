from zoneinfo import ZoneInfo

# =========================
# BASIC SETTINGS
# =========================

ADMIN_ID = 1847178297
WORK_TZ = ZoneInfo("Europe/Kyiv")

# Если позже перейдёшь на БД, это пригодится
DATABASE_URL = "sqlite:///database/maskerbot.db"

# =========================
# IDLE REMINDER SETTINGS
# =========================

# Раз в сколько дней можно напоминать пользователю без задач
IDLE_REMINDER_DAYS = 3

# Через сколько секунд после старта бота запускать первую idle-проверку
IDLE_REMINDER_START_DELAY_SEC = 300

# =========================
# MONTH NAMES
# =========================

MONTH_NAMES = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}

MONTHS_NOM = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}

# =========================
# ROLES
# =========================

ROLE_CATALOG = {
    "game_design": ("Геймдизайн", "🎮"),
    "level_design": ("Левел-дизайн", "🧱"),
    "narrative": ("Сценарий и нарратив", "📖"),
    "programming": ("Программирование", "💻"),
    "testing": ("Тестирование", "🧪"),
    "2d_art": ("2Д-арт", "🎨"),
    "ui_design": ("Интерфейс", "🧩"),
    "3d_art": ("3Д-арт", "🧊"),
    "animation": ("Анимация", "🎞"),
    "texturing": ("Текстуринг", "🪵"),
    "audio": ("Музыка и звук", "🎵"),
    "management": ("Управление", "📋"),
    "finance": ("Финансы и аудит", "💰"),
    "marketing": ("Аналитика и маркетинг", "📊"),
    "documentation": ("Текст и документация", "📝"),
    "other": ("Другое", "❓"),
}

# Синонимы для автоподбора role id из старых текстовых ролей
ROLE_SYNONYMS = {
    "геймдизайн": "game_design",
    "левел-дизайн": "level_design",
    "левел дизайн": "level_design",
    "нарратив": "narrative",
    "сценарий": "narrative",
    "программирование": "programming",
    "тестирование": "testing",
    "2д": "2d_art",
    "2d": "2d_art",
    "2д-арт": "2d_art",
    "интерфейс": "ui_design",
    "ui": "ui_design",
    "3д": "3d_art",
    "3d": "3d_art",
    "3д-арт": "3d_art",
    "анимация": "animation",
    "текстуринг": "texturing",
    "музыка": "audio",
    "звук": "audio",
    "аудио": "audio",
    "управление": "management",
    "финансы": "finance",
    "аудит": "finance",
    "маркетинг": "marketing",
    "аналитика": "marketing",
    "документация": "documentation",
    "текст": "documentation",
    "другое": "other",
}

# =========================
# PROJECTS
# =========================

DEFAULT_PROJECTS = [
    "F.R.E.U.S",
    "Teamwork",
]

# =========================
# TELEGRAM / UI LIMITS
# =========================

MAX_ACTIVE_TASKS_PER_USER = 3
MAX_TELEGRAM_MESSAGE_LEN = 4000

# =========================
# EVENT REMINDER WINDOWS
# =========================

REMINDER_24H_MIN_HOURS = 23
REMINDER_24H_MAX_HOURS = 25

REMINDER_2H_MIN_HOURS = 1.5
REMINDER_2H_MAX_HOURS = 2.5

# =========================
# STATUS
# =========================

TASK_STATUS_LABELS = {
    "backlog": ("🗂", "Backlog"),
    "available": ("🟢", "Available"),
    "in_progress": ("🛠", "In progress"),
    "review": ("🟡", "Review"),
    "done": ("✅", "Done"),
    "blocked": ("⛔", "Blocked"),
    "overdue": ("🔥", "Overdue"),
}

TASK_STATUS_RU = {
    "backlog": "Бэклог",
    "available": "Доступна",
    "in_progress": "В работе",
    "review": "На проверке",
    "done": "Выполнена",
    "blocked": "Заблокирована",
    "overdue": "Просрочена",
}

TASK_STATUSES = [
    "backlog",
    "available",
    "in_progress",
    "review",
    "done",
    "blocked",
    "overdue",
]

TASK_STATUS_TRANSITIONS = {
    "backlog": {"available"},
    "available": {"in_progress", "blocked", "overdue"},
    "in_progress": {"review", "blocked", "overdue"},
    "review": {"in_progress", "done", "blocked", "overdue"},
    "blocked": {"available", "in_progress"},
    "overdue": {"in_progress", "review", "done", "blocked"},
    "done": set(),
}

# =========================
# JCT LIMITS
# =========================

J_VALUE_MIN = 1
J_VALUE_MAX = 5

C_VALUE_MIN = 1
C_VALUE_MAX = 11

T_VALUE_MIN = 1
T_VALUE_MAX = 7

K_BONUS_MIN = -3
K_BONUS_MAX = 3