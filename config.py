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
    "gamedesign": ("Геймдизайн", "🎮"),
    "narrative": ("Сценарий и нарратив", "🖋️"),
    "art3d": ("3D-арт", "🧊"),
    "art2d": ("2D-арт", "🎨"),
    "animation": ("Анимация", "🎞️"),
    "programming": ("Программирование", "💻"),
    "testing": ("Тестирование", "🧪"),
    "docs": ("Документация", "📚"),
    "finance_legal": ("Финансы и юр. вопросы", "⚖️"),
    "marketing_pr": ("Маркетинг и PR", "📣"),
    "management": ("Управление", "🧭"),
    "audio": ("Аудио", "🎵"),
}

# Синонимы для автоподбора role id из старых текстовых ролей
ROLE_SYNONYMS = {
    "геймдизайн": "gamedesign",
    "нарратив": "narrative",
    "сценарий": "narrative",
    "3д": "art3d",
    "3d": "art3d",
    "3д-арт": "art3d",
    "2д": "art2d",
    "2d": "art2d",
    "2д-арт": "art2d",
    "визуальная работа": "visual",
    "анимация": "animation",
    "программирование": "programming",
    "тестирование": "testing",
    "документация": "docs",
    "финансы": "finance_legal",
    "юридические": "finance_legal",
    "маркетинг": "marketing_pr",
    "pr": "marketing_pr",
    "управление": "management",
    "аудио": "audio",
}

# =========================
# PROJECTS
# =========================

DEFAULT_PROJECTS = [
    "Starky Jungle",
    "Ideal Abyss",
    "Short film",
    "Non-project work",
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