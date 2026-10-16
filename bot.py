import os
import json
import asyncio
import html
import logging
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    PicklePersistence,
    filters,
)

# ============================================================================
# НАСТРОЙКИ  (заполните перед запуском)
# ============================================================================
BOT_TOKEN = "8945887765:AAEy0wuXUqg9YZtuOJojxSNeAOuFVKyYHiI"   # токен от @BotFather
ADMIN_CHAT_ID = 2067680256                 # chat_id администратора салона (число)
CALENDAR_ID = "aramg0639@gmail.com"       # календарь по умолчанию (например, "aramg0639@gmail.com")

CREDENTIALS_FILE = "credentials.json"     # ключ Service Account
SCOPES = ["https://www.googleapis.com/auth/calendar"]

TIMEZONE = ZoneInfo("Asia/Yerevan")       # UTC+4

# Языки: "ru" — русский, "hy" — армянский
DEFAULT_LANG = "ru"      # язык, если клиент ещё не выбрал (для напоминаний, ошибок)
STAFF_LANG = "ru"        # язык уведомлений администратору и мастерам, а также заголовков в календаре

# Напоминания клиентам
REMINDER_HOURS_BEFORE = 2        # за сколько часов до визита напомнить
REMINDER_CHECK_MINUTES = 10      # как часто бот проверяет календари

# Файл, где бот хранит выбранный клиентами язык (чтобы не терять после перезапуска)
PERSISTENCE_FILE = "bot_data.pickle"

# Сколько дней (включая сегодня) показывать для записи
BOOKING_DAYS_AHEAD = 7

# За сколько часов до визита ещё можно отменить запись (0 — без ограничений)
CANCEL_MIN_HOURS = 0

# Салон работает 10:00-20:00, слоты каждый час.
# Хотите сетку по 90 минут — замените на:
# ["10:00", "11:30", "13:00", "14:30", "16:00", "17:30", "19:00"]
ALL_SLOTS = [f"{h:02d}:00" for h in range(10, 20)]

# Время закрытия салона: услуга должна закончиться не позже этого времени
WORK_END = time(20, 0)

# Услуги: ключ -> названия на языках и длительность в минутах
SERVICES = {
    "haircut": {"names": {"ru": "Стрижка", "hy": "Մազերի կտրում"}, "minutes": 60},
    "styling": {"names": {"ru": "Укладка", "hy": "Մազերի հարդարում"}, "minutes": 90},
    "manicure": {"names": {"ru": "Маникюр", "hy": "Մատնահարդարում"}, "minutes": 60},
}

# Мастера: ключ (латиницей, без пробелов и двоеточий) -> настройки.
#   names       — имя мастера на каждом языке
#   calendar_id — ID календаря мастера (этот календарь нужно «расшарить»
#                 на email сервисного аккаунта с правом «Вносить изменения в мероприятия»)
#   services    — какие услуги делает мастер (ключи из SERVICES)
#   days_off    — выходные дни недели мастера: 0 = пн ... 6 = вс (необязательно)
#   chat_id     — Telegram chat_id мастера для уведомлений (необязательно;
#                 мастер должен один раз нажать /start у бота)

MASTERS = {
    "narek": {
        "names": {
            "ru": "Нарек",
            "hy": "Նարեկ",
        },
        "calendar_id": "aramg0639@gmail.com",
        "services": ["haircut", "styling"],
        "days_off": {6},
        "chat_id": None,
    },
    "samvel": {
        "names": {
            "ru": "Самвел",
            "hy": "Սամվել",
        },
        "calendar_id": "PASTE_SAMVEL_CALENDAR_ID",
        "services": ["haircut", "styling"],
        "days_off": {6},
        "chat_id": None,
    },
    "anna": {
        "names": {
            "ru": "Анна",
            "hy": "Աննա",
        },
        "calendar_id": "PASTE_ANNA_CALENDAR_ID",
        "services": ["manicure"],
        "days_off": {6},
        "chat_id": None,
    },
}


# ============================================================================
# ТЕКСТЫ (русский / армянский)
# ============================================================================
LANGS = ("ru", "hy")
ANY = "any"  # служебный ключ «любой свободный мастер»

CHOOSE_LANG_TEXT = "🌐 Выберите язык / Ընտրեք լեզուն"

TEXTS = {
    "ru": {
        "welcome": "💄 <b>Добро пожаловать в салон «Mademoiselle»!</b>\n\nВыберите услугу:",
        "min": "мин",
        "today": "Сегодня",
        "tomorrow": "Завтра",
        "any_master": "Любой свободный мастер",
        "btn_my": "📋 Мои записи",
        "btn_lang": "🌐 Язык / Լեզու",
        "btn_cancel": "❌ Отмена",
        "btn_back_service": "⬅️ Другая услуга",
        "btn_back": "⬅️ Назад",
        "btn_other_day": "⬅️ Другой день",
        "btn_confirm": "✅ Подтвердить",
        "btn_new": "📅 Новая запись",
        "btn_my_back": "⬅️ К моим записям",
        "btn_yes_cancel": "✅ Да, отменить",
        "btn_keep": "⬅️ Нет, оставить",
        "btn_cancel_booking": "🗑 Отменить: {dt}",
        "unknown_service": "Неизвестная услуга. Выберите из списка:",
        "no_masters": "😔 Эту услугу сейчас никто не выполняет. Выберите другую:",
        "ask_master": "✨ Услуга: <b>{service}</b> ({minutes} мин)\n\nК какому мастеру вас записать?",
        "master_unavailable": "Мастер недоступен. Нажмите /start, чтобы начать заново.",
        "session_expired": "Сессия устарела. Нажмите /start, чтобы начать заново.",
        "ask_day": (
            "✨ Услуга: <b>{service}</b> ({minutes} мин)\n"
            "👩‍🎨 Мастер: <b>{master}</b>\n\n"
            "На какой день вас записать?"
        ),
        "day_unavailable": "Этот день недоступен. Выберите день из списка:",
        "checking": "⏳ Проверяю свободное время…",
        "schedule_error": (
            "⚠️ Сейчас не удаётся получить расписание. "
            "Попробуйте ещё раз через минуту или свяжитесь с администратором."
        ),
        "no_slots": "😔 {day}: свободных окон нет (мастер: <b>{master}</b>).\nВыберите другой день:",
        "ask_time": (
            "✨ Услуга: <b>{service}</b> ({minutes} мин)\n"
            "👩‍🎨 Мастер: <b>{master}</b>\n"
            "📅 День: <b>{day}</b>\n\n"
            "Выберите удобное время:"
        ),
        "time_unavailable": "Это время недоступно. Нажмите /start, чтобы начать заново.",
        "ask_name": "🕐 Время: <b>{when}</b>\n\nКак вас зовут? Напишите ваше имя:",
        "bad_name": "Пожалуйста, введите имя (от 2 до 50 символов).",
        "ask_phone": (
            "Приятно познакомиться, <b>{name}</b>! 😊\n\n"
            "Теперь укажите номер телефона (например, <code>+374 99 123456</code>):"
        ),
        "bad_phone": (
            "Не похоже на номер телефона. Попробуйте ещё раз, например: "
            "<code>+374 99 123456</code>"
        ),
        "review": (
            "📋 <b>Проверьте данные записи:</b>\n\n"
            "💇 Услуга: <b>{service}</b> ({minutes} мин)\n"
            "👩‍🎨 Мастер: <b>{master}</b>\n"
            "🕐 Время: <b>{when}</b>\n"
            "👤 Имя: <b>{name}</b>\n"
            "📞 Телефон: <b>{phone}</b>\n\n"
            "Всё верно?"
        ),
        "confirming": "⏳ Оформляю запись…",
        "calendar_error_check": (
            "⚠️ Не удалось связаться с календарём. "
            "Нажмите «Подтвердить» ещё раз через минуту."
        ),
        "slot_taken": "😔 К сожалению, время <b>{slot}</b> только что заняли.\nВыберите другое время:",
        "no_more_slots": (
            "😔 К сожалению, на этот день свободных окон больше нет.\n"
            "Выберите другой день:"
        ),
        "save_error": (
            "⚠️ Не удалось сохранить запись из-за временной ошибки сервиса. "
            "Нажмите «Подтвердить» ещё раз или свяжитесь с администратором."
        ),
        "booked": (
            "🎉 <b>Вы записаны!</b>\n\n"
            "💇 Услуга: <b>{service}</b> ({minutes} мин)\n"
            "👩‍🎨 Мастер: <b>{master}</b>\n"
            "🕐 Время: <b>{when}</b>\n"
            "👤 Имя: {name}\n"
            "📞 Телефон: {phone}\n\n"
            "Ждём вас в салоне «Mademoiselle»! 💄\n"
            "Если планы изменятся — отменить запись можно в разделе «Мои записи»."
        ),
        "flow_cancelled": "Запись отменена. Будем рады видеть вас в «Mademoiselle»! 🌸",
        "my_searching": "⏳ Ищу ваши записи…",
        "my_error": "⚠️ Сейчас не удаётся получить ваши записи. Попробуйте чуть позже.",
        "my_failed_warn": "\n\n⚠️ Не удалось проверить расписание: {names}.",
        "my_none": "У вас нет предстоящих записей.",
        "my_title": "📋 <b>Ваши записи:</b>\n",
        "my_line": "{i}. <b>{when}</b>\n    {service} · мастер {master}",
        "not_found": "Запись не найдена.",
        "not_found_or_cancelled": "Запись не найдена или уже отменена.",
        "calendar_try_later": "⚠️ Не удалось связаться с календарём. Попробуйте позже.",
        "ask_cancel": (
            "❓ <b>Отменить эту запись?</b>\n\n"
            "🕐 {when}\n"
            "💇 {service}\n"
            "👩‍🎨 Мастер: {master}"
        ),
        "cancel_error": (
            "⚠️ Не удалось отменить запись из-за ошибки сервиса. "
            "Попробуйте ещё раз или свяжитесь с администратором."
        ),
        "too_late": (
            "⏰ Отменить запись можно не позднее чем за {hours} ч. до визита. "
            "Пожалуйста, свяжитесь с администратором салона."
        ),
        "cancelled_ok": (
            "✅ <b>Запись отменена.</b>\n\n"
            "🕐 {when}\n"
            "💇 {service}\n\n"
            "Будем рады видеть вас снова! 🌸"
        ),
        "reminder": (
            "⏰ <b>Напоминание о записи</b>\n\n"
            "🕐 <b>{when}</b>\n"
            "💇 {service}\n"
            "👩‍🎨 Мастер: {master}\n\n"
            "Ждём вас в салоне «Mademoiselle»! 💄\n"
            "Если планы изменились — отмените запись в разделе «Мои записи»."
        ),
        "generic_error": "⚠️ Произошла непредвиденная ошибка. Нажмите /start, чтобы начать заново.",
        "staff_new": (
            "🔔 <b>Новая запись!</b>\n\n"
            "💇 Услуга: {service} ({minutes} мин)\n"
            "👩‍🎨 Мастер: {master}\n"
            "🕐 Время: {when}\n"
            "👤 Имя: {name}\n"
            "📞 Телефон: {phone}\n"
            "✉️ Telegram: {tg}"
        ),
        "staff_cancel": (
            "❌ <b>Клиент отменил запись</b>\n\n"
            "🕐 {when}\n"
            "💇 {summary}\n"
            "👩‍🎨 Мастер: {master}\n"
            "{phone_line}"
            "✉️ Telegram: {tg}"
        ),
    },
    "hy": {
        "welcome": "💄 <b>Բարի գալուստ «Mademoiselle» սրահ։</b>\n\nԸնտրեք ծառայությունը՝",
        "min": "րոպե",
        "today": "Այսօր",
        "tomorrow": "Վաղը",
        "any_master": "Ցանկացած ազատ վարպետ",
        "btn_my": "📋 Իմ գրանցումները",
        "btn_lang": "🌐 Լեզու / Язык",
        "btn_cancel": "❌ Չեղարկել",
        "btn_back_service": "⬅️ Այլ ծառայություն",
        "btn_back": "⬅️ Հետ",
        "btn_other_day": "⬅️ Այլ օր",
        "btn_confirm": "✅ Հաստատել",
        "btn_new": "📅 Նոր գրանցում",
        "btn_my_back": "⬅️ Իմ գրանցումներին",
        "btn_yes_cancel": "✅ Այո, չեղարկել",
        "btn_keep": "⬅️ Ոչ, թողնել",
        "btn_cancel_booking": "🗑 Չեղարկել՝ {dt}",
        "unknown_service": "Անհայտ ծառայություն։ Ընտրեք ցանկից՝",
        "no_masters": "😔 Այս ծառայությունը ներկայումս ոչ ոք չի մատուցում։ Ընտրեք այլ ծառայություն՝",
        "ask_master": "✨ Ծառայություն՝ <b>{service}</b> ({minutes} րոպե)\n\nԸնտրեք վարպետին՝",
        "master_unavailable": "Վարպետը հասանելի չէ։ Սեղմեք /start՝ նորից սկսելու համար։",
        "session_expired": "Սեսիան ավարտվել է։ Սեղմեք /start՝ նորից սկսելու համար։",
        "ask_day": (
            "✨ Ծառայություն՝ <b>{service}</b> ({minutes} րոպե)\n"
            "👩‍🎨 Վարպետ՝ <b>{master}</b>\n\n"
            "Ընտրեք օրը՝"
        ),
        "day_unavailable": "Այս օրը հասանելի չէ։ Ընտրեք օրը ցանկից՝",
        "checking": "⏳ Ստուգում եմ ազատ ժամերը…",
        "schedule_error": (
            "⚠️ Այս պահին չի հաջողվում ստանալ ժամանակացույցը։ "
            "Փորձեք մեկ րոպեից կամ կապվեք ադմինիստրատորի հետ։"
        ),
        "no_slots": "😔 {day}՝ ազատ ժամեր չկան (վարպետ՝ <b>{master}</b>)։\nԸնտրեք այլ օր՝",
        "ask_time": (
            "✨ Ծառայություն՝ <b>{service}</b> ({minutes} րոպե)\n"
            "👩‍🎨 Վարպետ՝ <b>{master}</b>\n"
            "📅 Օր՝ <b>{day}</b>\n\n"
            "Ընտրեք հարմար ժամը՝"
        ),
        "time_unavailable": "Այս ժամը հասանելի չէ։ Սեղմեք /start՝ նորից սկսելու համար։",
        "ask_name": "🕐 Ժամ՝ <b>{when}</b>\n\nԳրեք, խնդրում ենք, ձեր անունը՝",
        "bad_name": "Խնդրում ենք մուտքագրել անունը (2-ից 50 նիշ)։",
        "ask_phone": (
            "Հաճելի է ծանոթանալ, <b>{name}</b>։ 😊\n\n"
            "Հիմա նշեք հեռախոսահամարը (օրինակ՝ <code>+374 99 123456</code>)՝"
        ),
        "bad_phone": (
            "Սա հեռախոսահամարի չի նման։ Փորձեք նորից, օրինակ՝ "
            "<code>+374 99 123456</code>"
        ),
        "review": (
            "📋 <b>Ստուգեք գրանցման տվյալները՝</b>\n\n"
            "💇 Ծառայություն՝ <b>{service}</b> ({minutes} րոպե)\n"
            "👩‍🎨 Վարպետ՝ <b>{master}</b>\n"
            "🕐 Ժամ՝ <b>{when}</b>\n"
            "👤 Անուն՝ <b>{name}</b>\n"
            "📞 Հեռախոս՝ <b>{phone}</b>\n\n"
            "Եթե ամեն ինչ ճիշտ է, հաստատեք գրանցումը։"
        ),
        "confirming": "⏳ Ձևակերպում եմ գրանցումը…",
        "calendar_error_check": (
            "⚠️ Չհաջողվեց կապվել օրացույցի հետ։ "
            "Մեկ րոպեից նորից սեղմեք «Հաստատել»։"
        ),
        "slot_taken": "😔 Ցավոք, <b>{slot}</b> ժամը հենց նոր զբաղեցրին։\nԸնտրեք այլ ժամ՝",
        "no_more_slots": (
            "😔 Ցավոք, այս օրվա համար ազատ ժամեր այլևս չկան։\n"
            "Ընտրեք այլ օր՝"
        ),
        "save_error": (
            "⚠️ Գրանցումը չհաջողվեց պահպանել ծառայության ժամանակավոր սխալի պատճառով։ "
            "Նորից սեղմեք «Հաստատել» կամ կապվեք ադմինիստրատորի հետ։"
        ),
        "booked": (
            "🎉 <b>Դուք գրանցված եք։</b>\n\n"
            "💇 Ծառայություն՝ <b>{service}</b> ({minutes} րոպե)\n"
            "👩‍🎨 Վարպետ՝ <b>{master}</b>\n"
            "🕐 Ժամ՝ <b>{when}</b>\n"
            "👤 Անուն՝ {name}\n"
            "📞 Հեռախոս՝ {phone}\n\n"
            "Սպասում ենք ձեզ «Mademoiselle» սրահում։ 💄\n"
            "Եթե ծրագրերը փոխվեն, գրանցումը կարող եք չեղարկել «Իմ գրանցումները» բաժնում։"
        ),
        "flow_cancelled": "Գրանցումը չեղարկվեց։ Սիրով կսպասենք ձեզ «Mademoiselle» սրահում։ 🌸",
        "my_searching": "⏳ Փնտրում եմ ձեր գրանցումները…",
        "my_error": "⚠️ Այս պահին չի հաջողվում ստանալ ձեր գրանցումները։ Փորձեք մի փոքր ուշ։",
        "my_failed_warn": "\n\n⚠️ Չհաջողվեց ստուգել ժամանակացույցը՝ {names}։",
        "my_none": "Դուք առաջիկա գրանցումներ չունեք։",
        "my_title": "📋 <b>Ձեր գրանցումները՝</b>\n",
        "my_line": "{i}. <b>{when}</b>\n    {service} · վարպետ՝ {master}",
        "not_found": "Գրանցումը չի գտնվել։",
        "not_found_or_cancelled": "Գրանցումը չի գտնվել կամ արդեն չեղարկված է։",
        "calendar_try_later": "⚠️ Չհաջողվեց կապվել օրացույցի հետ։ Փորձեք ավելի ուշ։",
        "ask_cancel": (
            "❓ <b>Հաստատեք գրանցման չեղարկումը</b>\n\n"
            "🕐 {when}\n"
            "💇 {service}\n"
            "👩‍🎨 Վարպետ՝ {master}"
        ),
        "cancel_error": (
            "⚠️ Գրանցումը չհաջողվեց չեղարկել ծառայության սխալի պատճառով։ "
            "Փորձեք նորից կամ կապվեք ադմինիստրատորի հետ։"
        ),
        "too_late": (
            "⏰ Գրանցումը կարելի է չեղարկել այցից առնվազն {hours} ժամ առաջ։ "
            "Խնդրում ենք կապվել սրահի ադմինիստրատորի հետ։"
        ),
        "cancelled_ok": (
            "✅ <b>Գրանցումը չեղարկվեց։</b>\n\n"
            "🕐 {when}\n"
            "💇 {service}\n\n"
            "Շատ ուրախ կլինենք կրկին տեսնել ձեզ։ 🌸"
        ),
        "reminder": (
            "⏰ <b>Գրանցման հիշեցում</b>\n\n"
            "🕐 <b>{when}</b>\n"
            "💇 {service}\n"
            "👩‍🎨 Վարպետ՝ {master}\n\n"
            "Սպասում ենք ձեզ «Mademoiselle» սրահում։ 💄\n"
            "Եթե ծրագրերը փոխվել են, չեղարկեք գրանցումը «Իմ գրանցումները» բաժնում։"
        ),
        "generic_error": "⚠️ Տեղի ունեցավ չնախատեսված սխալ։ Սեղմեք /start՝ նորից սկսելու համար։",
        "staff_new": (
            "🔔 <b>Նոր գրանցում։</b>\n\n"
            "💇 Ծառայություն՝ {service} ({minutes} րոպե)\n"
            "👩‍🎨 Վարպետ՝ {master}\n"
            "🕐 Ժամ՝ {when}\n"
            "👤 Անուն՝ {name}\n"
            "📞 Հեռախոս՝ {phone}\n"
            "✉️ Telegram՝ {tg}"
        ),
        "staff_cancel": (
            "❌ <b>Հաճախորդը չեղարկեց գրանցումը</b>\n\n"
            "🕐 {when}\n"
            "💇 {summary}\n"
            "👩‍🎨 Վարպետ՝ {master}\n"
            "{phone_line}"
            "✉️ Telegram՝ {tg}"
        ),
    },
}

WEEKDAYS = {
    "ru": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"],
    "hy": ["Երկ", "Երք", "Չրք", "Հնգ", "Ուրբ", "Շբթ", "Կիր"],
}

# ============================================================================
# ЛОГИРОВАНИЕ
# ============================================================================
logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("mademoiselle")

# Состояния диалога записи
SERVICE, MASTER, DATE, TIME, NAME, PHONE, CONFIRM = range(7)


# ============================================================================
# GOOGLE CALENDAR
# ============================================================================
def get_calendar_service():
    """
    Ստեղծում է Google Calendar API կլիենտ։
    Եթե Render-ում լցված է GOOGLE_CREDENTIALS_JSON փոփոխականը, կարդում է այնտեղից,
    հակառակ դեպքում՝ տեղային credentials.json ֆայլից։
    """
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        creds_info = json.loads(creds_json)
        creds = service_account.Credentials.from_service_account_info(
            creds_info, scopes=SCOPES
        )
    else:
        creds = service_account.Credentials.from_service_account_file(
            CREDENTIALS_FILE, scopes=SCOPES
        )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _parse_dt(value: str) -> datetime:
    """Разбирает ISO-строку из Google Calendar и приводит к времени Еревана."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TIMEZONE)


def get_available_times(
    duration_minutes: int,
    day: date | None = None,
    calendar_id: str = CALENDAR_ID,
) -> list[str]:
    """
    Возвращает список свободных стартовых слотов («HH:MM») на указанный день
    (по умолчанию — сегодня) в календаре `calendar_id` для услуги заданной
    длительности.

    Слот исключается, если:
      * он уже в прошлом (актуально для сегодняшнего дня);
      * услуга заканчивается после закрытия салона (WORK_END);
      * интервал [начало, начало + длительность) пересекается
        с любым событием в календаре.

    Функция синхронная (сетевой вызов Google) — из async-кода
    вызывайте через `await asyncio.to_thread(...)`.
    Бросает исключение при недоступности Google API.
    """
    now = datetime.now(TIMEZONE)
    day = day or now.date()

    day_start = datetime.combine(day, time(0, 0), tzinfo=TIMEZONE)
    day_end = day_start + timedelta(days=1)
    closing = datetime.combine(day, WORK_END, tzinfo=TIMEZONE)

    events = (
        get_calendar_service()
        .events()
        .list(
            calendarId=calendar_id,
            timeMin=day_start.isoformat(),
            timeMax=day_end.isoformat(),
            singleEvents=True,      # разворачиваем повторяющиеся события
            orderBy="startTime",
            maxResults=250,
        )
        .execute()
        .get("items", [])
    )

    # Собираем занятые интервалы
    busy: list[tuple[datetime, datetime]] = []
    for ev in events:
        if ev.get("status") == "cancelled":
            continue
        if ev.get("transparency") == "transparent":
            continue  # событие помечено как «свободен» — не блокирует запись

        start, end = ev.get("start", {}), ev.get("end", {})
        if "dateTime" in start and "dateTime" in end:
            busy.append((_parse_dt(start["dateTime"]), _parse_dt(end["dateTime"])))
        else:
            # Событие на весь день (выходной, праздник) — блокируем весь день
            busy.append((day_start, day_end))

    # Проверяем каждый слот
    available: list[str] = []
    duration = timedelta(minutes=duration_minutes)
    for slot in ALL_SLOTS:
        hour, minute = map(int, slot.split(":"))
        slot_start = datetime.combine(day, time(hour, minute), tzinfo=TIMEZONE)
        slot_end = slot_start + duration

        if slot_start <= now:                                   # время прошло
            continue
        if slot_end > closing:                                  # после закрытия
            continue
        # Пересечение интервалов: A.start < B.end и A.end > B.start
        if any(slot_start < b_end and slot_end > b_start for b_start, b_end in busy):
            continue
        available.append(slot)

    logger.info(
        "Календарь %s | день %s | сейчас %s | занято: %s | свободно: %s",
        calendar_id, day, now.strftime("%H:%M"), busy, available,
    )
    return available


def create_calendar_event(
    calendar_id: str,
    master_key: str,
    user_id: int,
    lang: str,
    name: str,
    service_key: str,
    phone: str,
    day: date,
    slot: str,
    duration_minutes: int,
) -> str:
    """
    Создаёт событие в календаре мастера. Возвращает ID события.
    В скрытые поля события пишем Telegram ID клиента, мастера, услугу и язык —
    по ним бот находит записи клиента для отмены и шлёт напоминания.
    """
    hour, minute = map(int, slot.split(":"))
    start = datetime.combine(day, time(hour, minute), tzinfo=TIMEZONE)
    end = start + timedelta(minutes=duration_minutes)

    body = {
        "summary": f"{name} - {svc_name(service_key, STAFF_LANG)}",
        "description": f"Тел: {phone}",
        "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Yerevan"},
        "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Yerevan"},
        "extendedProperties": {
            "private": {
                "tg_user": str(user_id),
                "master": master_key,
                "service": service_key,
                "lang": lang,
            }
        },
    }
    created = get_calendar_service().events().insert(calendarId=calendar_id, body=body).execute()
    return created["id"]


def list_user_bookings(user_id: int) -> tuple[list[dict], list[str]]:
    """
    Ищет предстоящие записи клиента во всех календарях мастеров.
    Возвращает (список записей, список ключей мастеров, чей календарь не удалось прочитать).
    """
    now = datetime.now(TIMEZONE)
    service = get_calendar_service()
    bookings: list[dict] = []
    failed: list[str] = []

    for master_key, master in MASTERS.items():
        try:
            items = (
                service.events()
                .list(
                    calendarId=master["calendar_id"],
                    timeMin=now.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    privateExtendedProperty=f"tg_user={user_id}",
                    maxResults=20,
                )
                .execute()
                .get("items", [])
            )
        except Exception:
            logger.exception("Не удалось прочитать календарь мастера %s", master_key)
            failed.append(master_key)
            continue

        for ev in items:
            start = ev.get("start", {}).get("dateTime")
            if ev.get("status") == "cancelled" or not start:
                continue
            private = ev.get("extendedProperties", {}).get("private", {})
            bookings.append(
                {
                    "master_key": master_key,
                    "event_id": ev["id"],
                    "summary": ev.get("summary", "—"),
                    "service_key": private.get("service"),
                    "start": _parse_dt(start),
                }
            )

    bookings.sort(key=lambda b: b["start"])
    return bookings[:10], failed


def _get_own_event(service, master_key: str, event_id: str, user_id: int) -> dict | None:
    """Возвращает событие, только если оно существует и принадлежит этому клиенту."""
    try:
        ev = (
            service.events()
            .get(calendarId=MASTERS[master_key]["calendar_id"], eventId=event_id)
            .execute()
        )
    except Exception as exc:
        # 404/410 — события нет (уже удалено)
        if getattr(getattr(exc, "resp", None), "status", None) in (404, 410):
            return None
        raise
    private = ev.get("extendedProperties", {}).get("private", {})
    if ev.get("status") == "cancelled" or private.get("tg_user") != str(user_id):
        return None
    if not ev.get("start", {}).get("dateTime"):
        return None
    return {
        "summary": ev.get("summary", "—"),
        "description": ev.get("description", ""),
        "service_key": private.get("service"),
        "start": _parse_dt(ev["start"]["dateTime"]),
    }


def get_booking(master_key: str, event_id: str, user_id: int) -> dict | None:
    """Данные одной записи клиента (или None, если не найдена / чужая)."""
    return _get_own_event(get_calendar_service(), master_key, event_id, user_id)


def cancel_booking(master_key: str, event_id: str, user_id: int) -> tuple[str, dict | None]:
    """
    Удаляет запись клиента из календаря мастера.
    Возвращает ("ok" | "not_found" | "too_late", данные записи).
    """
    service = get_calendar_service()
    info = _get_own_event(service, master_key, event_id, user_id)
    if info is None:
        return "not_found", None

    if CANCEL_MIN_HOURS > 0:
        if info["start"] - datetime.now(TIMEZONE) < timedelta(hours=CANCEL_MIN_HOURS):
            return "too_late", info

    service.events().delete(
        calendarId=MASTERS[master_key]["calendar_id"], eventId=event_id
    ).execute()
    return "ok", info


def find_due_reminders() -> list[dict]:
    """
    Ищет записи, начинающиеся в ближайшие REMINDER_HOURS_BEFORE часов,
    о которых клиенту ещё не напомнили. Не шлёт напоминание, если запись
    была создана уже внутри этого окна (клиент и так только что о ней узнал).
    """
    now = datetime.now(TIMEZONE)
    window = timedelta(hours=REMINDER_HOURS_BEFORE)
    service = get_calendar_service()
    due: list[dict] = []

    for master_key, master in MASTERS.items():
        try:
            items = (
                service.events()
                .list(
                    calendarId=master["calendar_id"],
                    timeMin=now.isoformat(),
                    timeMax=(now + window).isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=100,
                )
                .execute()
                .get("items", [])
            )
        except Exception:
            logger.exception("Напоминания: не удалось прочитать календарь %s", master_key)
            continue

        for ev in items:
            private = ev.get("extendedProperties", {}).get("private", {})
            start_raw = ev.get("start", {}).get("dateTime")
            if (
                ev.get("status") == "cancelled"
                or not private.get("tg_user")
                or private.get("reminded") == "1"
                or not start_raw
            ):
                continue
            start = _parse_dt(start_raw)
            if start <= now:
                continue
            created = ev.get("created")
            if created and _parse_dt(created) > start - window:
                continue  # запись создана внутри окна напоминания
            due.append(
                {
                    "master_key": master_key,
                    "event_id": ev["id"],
                    "user_id": int(private["tg_user"]),
                    "service_key": private.get("service"),
                    "summary": ev.get("summary", "—"),
                    "lang": private.get("lang", DEFAULT_LANG),
                    "start": start,
                }
            )
    return due


def mark_reminded(master_key: str, event_id: str) -> None:
    """Помечает событие как «напоминание отправлено» (в скрытом поле события)."""
    get_calendar_service().events().patch(
        calendarId=MASTERS[master_key]["calendar_id"],
        eventId=event_id,
        body={"extendedProperties": {"private": {"reminded": "1"}}},
    ).execute()


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================
def tr(lang: str, key: str, **kwargs) -> str:
    """Возвращает текст на нужном языке и подставляет значения."""
    text = TEXTS[lang if lang in LANGS else DEFAULT_LANG][key]
    return text.format(**kwargs) if kwargs else text


def esc(text: str) -> str:
    """Экранирование пользовательского ввода для HTML-разметки Telegram."""
    return html.escape(str(text), quote=False)


def get_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Язык клиента: выбранный им, иначе по языку Telegram, иначе DEFAULT_LANG."""
    lang = context.user_data.get("lang")
    if lang in LANGS:
        return lang
    user = update.effective_user
    code = (user.language_code or "").lower() if user else ""
    return "hy" if code.startswith("hy") else DEFAULT_LANG


def reset_session(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Очищает данные текущей записи, но сохраняет выбранный язык."""
    lang = context.user_data.get("lang")
    context.user_data.clear()
    if lang:
        context.user_data["lang"] = lang


def svc_name(service_key: str, lang: str) -> str:
    return SERVICES[service_key]["names"][lang]


def master_name(master_key: str, lang: str) -> str:
    if master_key == ANY:
        return tr(lang, "any_master")
    return MASTERS[master_key]["names"][lang]


def masters_for_service(service_key: str) -> list[tuple[str, dict]]:
    return [(k, m) for k, m in MASTERS.items() if service_key in m["services"]]


def available_days(candidates: list[str]) -> list[date]:
    """Дни, на которые можно записаться (хотя бы один из мастеров работает)."""
    today = datetime.now(TIMEZONE).date()
    days = [today + timedelta(days=i) for i in range(BOOKING_DAYS_AHEAD)]
    return [
        d for d in days
        if any(d.weekday() not in MASTERS[k].get("days_off", set()) for k in candidates)
    ]


def day_label(day: date, lang: str) -> str:
    """Подпись дня: «Сегодня», «Завтра» или «Сб 17.10»."""
    today = datetime.now(TIMEZONE).date()
    if day == today:
        return tr(lang, "today")
    if day == today + timedelta(days=1):
        return tr(lang, "tomorrow")
    return f"{WEEKDAYS[lang][day.weekday()]} {day:%d.%m}"


def when_text(day: date, slot: str, lang: str) -> str:
    return f"{day_label(day, lang)} ({day:%d.%m.%Y}), {slot}"


async def find_slots(duration: int, day: date, candidates: list[str]) -> dict[str, list[str]]:
    """
    Свободные слоты на день: {слот: [мастера, у которых он свободен]}.
    Календари мастеров опрашиваются параллельно. Если у кого-то из мастеров
    календарь недоступен, он просто не предлагается; ошибка выбрасывается,
    только если недоступны все календари.
    """
    workers = [
        k for k in candidates if day.weekday() not in MASTERS[k].get("days_off", set())
    ]
    if not workers:
        return {}

    results = await asyncio.gather(
        *[
            asyncio.to_thread(get_available_times, duration, day, MASTERS[k]["calendar_id"])
            for k in workers
        ],
        return_exceptions=True,
    )

    slot_masters: dict[str, list[str]] = {}
    errors: list[BaseException] = []
    for master_key, result in zip(workers, results):
        if isinstance(result, BaseException):
            logger.error("Календарь мастера %s недоступен", master_key, exc_info=result)
            errors.append(result)
            continue
        for slot in result:
            slot_masters.setdefault(slot, []).append(master_key)

    if errors and len(errors) == len(workers):
        raise errors[0]
    return dict(sorted(slot_masters.items()))


# ---------- клавиатуры ----------
def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🇦🇲 Հայերեն", callback_data="lang:hy"),
                InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru"),
            ]
        ]
    )


def services_keyboard(lang: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"{svc['names'][lang]} — {svc['minutes']} {tr(lang, 'min')}",
                callback_data=f"service:{key}",
            )
        ]
        for key, svc in SERVICES.items()
    ]
    rows.append([InlineKeyboardButton(tr(lang, "btn_my"), callback_data="my")])
    rows.append([InlineKeyboardButton(tr(lang, "btn_lang"), callback_data="change_lang")])
    rows.append([InlineKeyboardButton(tr(lang, "btn_cancel"), callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def masters_keyboard(lang: str, service_key: str) -> InlineKeyboardMarkup:
    masters = masters_for_service(service_key)
    rows = []
    if len(masters) > 1:
        rows.append(
            [InlineKeyboardButton(f"🎲 {tr(lang, 'any_master')}", callback_data=f"master:{ANY}")]
        )
    rows += [
        [InlineKeyboardButton(f"👩‍🎨 {m['names'][lang]}", callback_data=f"master:{key}")]
        for key, m in masters
    ]
    rows.append([InlineKeyboardButton(tr(lang, "btn_back_service"), callback_data="back")])
    rows.append([InlineKeyboardButton(tr(lang, "btn_cancel"), callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def dates_keyboard(lang: str, candidates: list[str]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(day_label(d, lang), callback_data=f"date:{d.isoformat()}")
        for d in available_days(candidates)
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(tr(lang, "btn_back"), callback_data="back_master")])
    rows.append([InlineKeyboardButton(tr(lang, "btn_cancel"), callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def times_keyboard(lang: str, slots: list[str]) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(s, callback_data=f"time:{s}") for s in slots]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(tr(lang, "btn_other_day"), callback_data="back_date")])
    rows.append([InlineKeyboardButton(tr(lang, "btn_cancel"), callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def confirm_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(tr(lang, "btn_confirm"), callback_data="confirm"),
                InlineKeyboardButton(tr(lang, "btn_cancel"), callback_data="cancel"),
            ]
        ]
    )


def cancel_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(tr(lang, "btn_cancel"), callback_data="cancel")]]
    )


def restart_keyboard(lang: str, with_my: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(tr(lang, "btn_new"), callback_data="restart")]]
    if with_my:
        rows.append([InlineKeyboardButton(tr(lang, "btn_my"), callback_data="my")])
    return InlineKeyboardMarkup(rows)


def my_back_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(tr(lang, "btn_my_back"), callback_data="my")]]
    )


async def respond(update: Update, text: str, markup: InlineKeyboardMarkup | None = None):
    """Редактирует сообщение (если это нажатие кнопки) или отправляет новое."""
    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        except BadRequest as exc:
            if "not modified" not in str(exc).lower():
                raise
    else:
        await update.effective_message.reply_text(
            text, reply_markup=markup, parse_mode=ParseMode.HTML
        )


async def notify_staff(context: ContextTypes.DEFAULT_TYPE, text: str, master: dict | None = None):
    """Отправляет уведомление администратору и (если задан chat_id) мастеру."""
    chat_ids = {ADMIN_CHAT_ID}
    if master and master.get("chat_id"):
        chat_ids.add(master["chat_id"])
    for chat_id in chat_ids:
        try:
            await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
        except Exception:
            logger.exception("Не удалось отправить уведомление в чат %s", chat_id)


def tg_link(user) -> str:
    link = f'<a href="tg://user?id={user.id}">{esc(user.full_name)}</a>'
    if user.username:
        link += f" (@{esc(user.username)})"
    return link


# ============================================================================
# ХЕНДЛЕРЫ: ЯЗЫК
# ============================================================================
async def show_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает выбор языка (кнопка «Язык / Լեզու» или первый запуск)."""
    if update.callback_query:
        await update.callback_query.answer()
    await respond(update, CHOOSE_LANG_TEXT, language_keyboard())
    return ConversationHandler.END


async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запоминает язык и открывает главное меню."""
    lang = update.callback_query.data.split(":", 1)[1]
    if lang not in LANGS:
        lang = DEFAULT_LANG
    context.user_data["lang"] = lang
    return await start(update, context)


# ============================================================================
# ХЕНДЛЕРЫ: ДИАЛОГ ЗАПИСИ
# ============================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Главное меню: выбор услуги (при первом запуске — выбор языка)."""
    if context.user_data.get("lang") not in LANGS:
        return await show_language(update, context)

    if update.callback_query:
        await update.callback_query.answer()
    reset_session(context)
    lang = context.user_data["lang"]

    await respond(update, tr(lang, "welcome"), services_keyboard(lang))
    return SERVICE


async def choose_service(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Услуга выбрана → выбор мастера (или сразу день, если мастер один)."""
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    d = context.user_data

    key = query.data.split(":", 1)[1]
    if key not in SERVICES:
        await respond(update, tr(lang, "unknown_service"), services_keyboard(lang))
        return SERVICE

    masters = masters_for_service(key)
    if not masters:
        await respond(update, tr(lang, "no_masters"), services_keyboard(lang))
        return SERVICE

    d.update(
        service_key=key,
        duration=SERVICES[key]["minutes"],
        candidates=[k for k, _ in masters],
    )

    if len(masters) == 1:  # мастер один — шаг выбора пропускаем
        d["master_key"] = masters[0][0]
        return await show_dates(update, context)

    return await show_masters(update, context)


async def show_masters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = get_lang(update, context)
    d = context.user_data
    await respond(
        update,
        tr(
            lang, "ask_master",
            service=esc(svc_name(d["service_key"], lang)), minutes=d["duration"],
        ),
        masters_keyboard(lang, d["service_key"]),
    )
    return MASTER


async def choose_master(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    d = context.user_data

    master_key = query.data.split(":", 1)[1]
    allowed = [k for k, _ in masters_for_service(d.get("service_key", ""))]
    if master_key != ANY and master_key not in allowed:
        await respond(update, tr(lang, "master_unavailable"))
        return ConversationHandler.END

    d["master_key"] = master_key
    d["candidates"] = allowed if master_key == ANY else [master_key]
    return await show_dates(update, context)


async def show_dates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает кнопки с днями. Также вызывается кнопкой «Другой день»."""
    if update.callback_query:
        await update.callback_query.answer()
    lang = get_lang(update, context)
    d = context.user_data

    if "master_key" not in d or "candidates" not in d:
        await respond(update, tr(lang, "session_expired"))
        return ConversationHandler.END

    await respond(
        update,
        tr(
            lang, "ask_day",
            service=esc(svc_name(d["service_key"], lang)),
            minutes=d["duration"],
            master=esc(master_name(d["master_key"], lang)),
        ),
        dates_keyboard(lang, d["candidates"]),
    )
    return DATE


async def back_from_dates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """«Назад» с выбора дня: к мастерам (если их несколько) или к услугам."""
    service_key = context.user_data.get("service_key")
    if service_key and len(masters_for_service(service_key)) > 1:
        if update.callback_query:
            await update.callback_query.answer()
        return await show_masters(update, context)
    return await start(update, context)


async def choose_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """День выбран → ищем свободные слоты в календаре(ях) мастера(ов)."""
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    d = context.user_data

    try:
        day = date.fromisoformat(query.data.split(":", 1)[1])
    except ValueError:
        day = None

    if "master_key" not in d or "candidates" not in d:
        await respond(update, tr(lang, "session_expired"))
        return ConversationHandler.END
    if day is None or day not in available_days(d["candidates"]):
        await respond(update, tr(lang, "day_unavailable"), dates_keyboard(lang, d["candidates"]))
        return DATE

    d["day"] = day
    await respond(update, tr(lang, "checking"))

    try:
        slot_masters = await find_slots(d["duration"], day, d["candidates"])
    except Exception:
        logger.exception("Ошибка при получении слотов из Google Calendar")
        await respond(update, tr(lang, "schedule_error"), dates_keyboard(lang, d["candidates"]))
        return DATE

    master_label = esc(master_name(d["master_key"], lang))
    if not slot_masters:
        await respond(
            update,
            tr(lang, "no_slots", day=esc(day_label(day, lang)), master=master_label),
            dates_keyboard(lang, d["candidates"]),
        )
        return DATE

    d["slot_masters"] = slot_masters
    await respond(
        update,
        tr(
            lang, "ask_time",
            service=esc(svc_name(d["service_key"], lang)),
            minutes=d["duration"],
            master=master_label,
            day=esc(day_label(day, lang)),
        ),
        times_keyboard(lang, list(slot_masters)),
    )
    return TIME


async def choose_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Время выбрано → просим имя."""
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    d = context.user_data

    slot = query.data.split(":", 1)[1]
    if slot not in d.get("slot_masters", {}):
        await respond(update, tr(lang, "time_unavailable"))
        return ConversationHandler.END

    d["slot"] = slot
    await respond(
        update,
        tr(lang, "ask_name", when=esc(f"{day_label(d['day'], lang)}, {slot}")),
        cancel_keyboard(lang),
    )
    return NAME


async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = get_lang(update, context)
    name = update.message.text.strip()
    if not 2 <= len(name) <= 50:
        await update.message.reply_text(tr(lang, "bad_name"), reply_markup=cancel_keyboard(lang))
        return NAME

    context.user_data["client_name"] = name
    await update.message.reply_text(
        tr(lang, "ask_phone", name=esc(name)),
        reply_markup=cancel_keyboard(lang),
        parse_mode=ParseMode.HTML,
    )
    return PHONE


async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = get_lang(update, context)
    phone = update.message.text.strip()
    digits = re.sub(r"\D", "", phone)
    if not re.fullmatch(r"\+?[\d\s\-()]+", phone) or not 7 <= len(digits) <= 15:
        await update.message.reply_text(
            tr(lang, "bad_phone"),
            reply_markup=cancel_keyboard(lang),
            parse_mode=ParseMode.HTML,
        )
        return PHONE

    d = context.user_data
    d["phone"] = phone

    await update.message.reply_text(
        tr(
            lang, "review",
            service=esc(svc_name(d["service_key"], lang)),
            minutes=d["duration"],
            master=esc(master_name(d["master_key"], lang)),
            when=esc(when_text(d["day"], d["slot"], lang)),
            name=esc(d["client_name"]),
            phone=esc(phone),
        ),
        reply_markup=confirm_keyboard(lang),
        parse_mode=ParseMode.HTML,
    )
    return CONFIRM


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Подтверждение: повторная проверка слота → создание события → уведомления."""
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    d = context.user_data
    user = update.effective_user

    if not d.get("slot") or not d.get("day") or not d.get("master_key"):
        await respond(update, tr(lang, "session_expired"))
        return ConversationHandler.END

    await respond(update, tr(lang, "confirming"))

    # 1. Защита от двойного бронирования: слот мог занять кто-то другой
    #    за время, пока клиент вводил данные.
    try:
        slot_masters = await find_slots(d["duration"], d["day"], d["candidates"])
    except Exception:
        logger.exception("Ошибка проверки слота перед записью")
        await respond(update, tr(lang, "calendar_error_check"), confirm_keyboard(lang))
        return CONFIRM

    if d["slot"] not in slot_masters:
        if slot_masters:
            d["slot_masters"] = slot_masters
            await respond(
                update,
                tr(lang, "slot_taken", slot=d["slot"]),
                times_keyboard(lang, list(slot_masters)),
            )
            return TIME
        await respond(update, tr(lang, "no_more_slots"), dates_keyboard(lang, d["candidates"]))
        return DATE

    # 2. Выбираем мастера: для «любого» — первого свободного в этот слот
    free = slot_masters[d["slot"]]
    master_key = free[0] if d["master_key"] == ANY else d["master_key"]
    master = MASTERS[master_key]

    # 3. Создаём событие в календаре мастера
    try:
        await asyncio.to_thread(
            create_calendar_event,
            master["calendar_id"], master_key, user.id, lang,
            d["client_name"], d["service_key"], d["phone"],
            d["day"], d["slot"], d["duration"],
        )
    except Exception:
        logger.exception("Ошибка создания события в Google Calendar")
        await respond(update, tr(lang, "save_error"), confirm_keyboard(lang))
        return CONFIRM

    # 4. Сообщение клиенту
    await respond(
        update,
        tr(
            lang, "booked",
            service=esc(svc_name(d["service_key"], lang)),
            minutes=d["duration"],
            master=esc(master_name(master_key, lang)),
            when=esc(when_text(d["day"], d["slot"], lang)),
            name=esc(d["client_name"]),
            phone=esc(d["phone"]),
        ),
        restart_keyboard(lang, with_my=True),
    )

    # 5. Уведомление администратору и мастеру (сбой не ломает запись клиента)
    await notify_staff(
        context,
        tr(
            STAFF_LANG, "staff_new",
            service=esc(svc_name(d["service_key"], STAFF_LANG)),
            minutes=d["duration"],
            master=esc(master_name(master_key, STAFF_LANG)),
            when=esc(when_text(d["day"], d["slot"], STAFF_LANG)),
            name=esc(d["client_name"]),
            phone=esc(d["phone"]),
            tg=tg_link(user),
        ),
        master,
    )

    reset_session(context)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Выход из диалога записи на любом шаге (кнопка или /cancel)."""
    if update.callback_query:
        await update.callback_query.answer()
    lang = get_lang(update, context)
    reset_session(context)
    await respond(update, tr(lang, "flow_cancelled"), restart_keyboard(lang, with_my=True))
    return ConversationHandler.END


# ============================================================================
# ХЕНДЛЕРЫ: «МОИ ЗАПИСИ» И ОТМЕНА ЗАПИСИ КЛИЕНТОМ
# (работают вне диалога, поэтому доступны в любой момент)
# ============================================================================
async def my_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Список предстоящих записей клиента с кнопками отмены."""
    lang = get_lang(update, context)
    if update.callback_query:
        await update.callback_query.answer()
        await respond(update, tr(lang, "my_searching"))

    user = update.effective_user
    try:
        bookings, failed = await asyncio.to_thread(list_user_bookings, user.id)
    except Exception:
        logger.exception("Ошибка получения записей клиента")
        await respond(update, tr(lang, "my_error"), restart_keyboard(lang))
        return

    warning = ""
    if failed:
        names = ", ".join(master_name(k, lang) for k in failed)
        warning = tr(lang, "my_failed_warn", names=esc(names))

    if not bookings:
        await respond(update, tr(lang, "my_none") + warning, restart_keyboard(lang))
        return

    lines = [tr(lang, "my_title")]
    rows = []
    for i, b in enumerate(bookings, 1):
        service = (
            svc_name(b["service_key"], lang) if b["service_key"] in SERVICES else b["summary"]
        )
        when = when_text(b["start"].date(), b["start"].strftime("%H:%M"), lang)
        lines.append(
            tr(
                lang, "my_line",
                i=i, when=esc(when), service=esc(service),
                master=esc(master_name(b["master_key"], lang)),
            )
        )
        label = tr(lang, "btn_cancel_booking", dt=f"{b['start']:%d.%m %H:%M}")
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"cb:{b['master_key']}:{b['event_id']}")]
        )
    rows.append([InlineKeyboardButton(tr(lang, "btn_new"), callback_data="restart")])

    await respond(update, "\n".join(lines) + warning, InlineKeyboardMarkup(rows))


def _parse_booking_callback(data: str) -> tuple[str, str] | None:
    """Разбирает callback_data вида 'cb:<мастер>:<id события>'."""
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[1] not in MASTERS or not parts[2]:
        return None
    return parts[1], parts[2]


async def ask_cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Шаг 1: показываем запись и просим подтвердить отмену."""
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)

    parsed = _parse_booking_callback(query.data)
    if not parsed:
        await respond(update, tr(lang, "not_found"), my_back_keyboard(lang))
        return
    master_key, event_id = parsed

    try:
        info = await asyncio.to_thread(get_booking, master_key, event_id, update.effective_user.id)
    except Exception:
        logger.exception("Ошибка чтения записи для отмены")
        await respond(update, tr(lang, "calendar_try_later"), my_back_keyboard(lang))
        return

    if info is None:
        await respond(update, tr(lang, "not_found_or_cancelled"), my_back_keyboard(lang))
        return

    service = svc_name(info["service_key"], lang) if info["service_key"] in SERVICES else info["summary"]
    when = when_text(info["start"].date(), info["start"].strftime("%H:%M"), lang)
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(tr(lang, "btn_yes_cancel"), callback_data=f"cy:{master_key}:{event_id}")],
            [InlineKeyboardButton(tr(lang, "btn_keep"), callback_data="my")],
        ]
    )
    await respond(
        update,
        tr(
            lang, "ask_cancel",
            when=esc(when), service=esc(service), master=esc(master_name(master_key, lang)),
        ),
        keyboard,
    )


async def do_cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Шаг 2: удаляем событие из календаря и уведомляем персонал."""
    query = update.callback_query
    await query.answer()
    lang = get_lang(update, context)
    user = update.effective_user

    parsed = _parse_booking_callback(query.data)
    if not parsed:
        await respond(update, tr(lang, "not_found"), my_back_keyboard(lang))
        return
    master_key, event_id = parsed

    try:
        status, info = await asyncio.to_thread(cancel_booking, master_key, event_id, user.id)
    except Exception:
        logger.exception("Ошибка отмены записи")
        await respond(update, tr(lang, "cancel_error"), my_back_keyboard(lang))
        return

    if status == "not_found":
        await respond(update, tr(lang, "not_found_or_cancelled"), my_back_keyboard(lang))
        return
    if status == "too_late":
        await respond(
            update, tr(lang, "too_late", hours=CANCEL_MIN_HOURS), my_back_keyboard(lang)
        )
        return

    service = svc_name(info["service_key"], lang) if info["service_key"] in SERVICES else info["summary"]
    when = when_text(info["start"].date(), info["start"].strftime("%H:%M"), lang)
    await respond(
        update,
        tr(lang, "cancelled_ok", when=esc(when), service=esc(service)),
        restart_keyboard(lang, with_my=True),
    )

    staff_when = when_text(info["start"].date(), info["start"].strftime("%H:%M"), STAFF_LANG)
    phone_line = f"📞 {esc(info['description'])}\n" if info["description"] else ""
    await notify_staff(
        context,
        tr(
            STAFF_LANG, "staff_cancel",
            when=esc(staff_when),
            summary=esc(info["summary"]),
            master=esc(master_name(master_key, STAFF_LANG)),
            phone_line=phone_line,
            tg=tg_link(user),
        ),
        MASTERS[master_key],
    )


# ============================================================================
# НАПОМИНАНИЯ
# ============================================================================
async def send_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Периодическая задача: напоминает клиентам о ближайших записях."""
    try:
        due = await asyncio.to_thread(find_due_reminders)
    except Exception:
        logger.exception("Не удалось получить список напоминаний")
        return

    for r in due:
        lang = r["lang"] if r["lang"] in LANGS else DEFAULT_LANG
        service = (
            svc_name(r["service_key"], lang) if r["service_key"] in SERVICES else r["summary"]
        )
        when = when_text(r["start"].date(), r["start"].strftime("%H:%M"), lang)
        text = tr(
            lang, "reminder",
            when=esc(when), service=esc(service),
            master=esc(master_name(r["master_key"], lang)),
        )
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(tr(lang, "btn_my"), callback_data="my")]]
        )
        try:
            await context.bot.send_message(
                r["user_id"], text, reply_markup=keyboard, parse_mode=ParseMode.HTML
            )
            logger.info("Напоминание отправлено пользователю %s", r["user_id"])
        except Exception:
            # Например, клиент заблокировал бота. Всё равно помечаем, чтобы не повторять.
            logger.warning("Не удалось отправить напоминание пользователю %s", r["user_id"])

        try:
            await asyncio.to_thread(mark_reminded, r["master_key"], r["event_id"])
        except Exception:
            logger.exception("Не удалось пометить напоминание как отправленное")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Глобальный обработчик непредвиденных ошибок."""
    logger.error("Необработанная ошибка", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                update.effective_chat.id,
                tr("ru", "generic_error") + "\n" + tr("hy", "generic_error"),
            )
        except Exception:
            pass


# ============================================================================
# ЗАПУСК
# ============================================================================
def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .persistence(PicklePersistence(filepath=PERSISTENCE_FILE))
        .build()
    )

    cancel_cb = CallbackQueryHandler(cancel, pattern=r"^cancel$")
    text_input = filters.TEXT & ~filters.COMMAND

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("language", show_language),
            CallbackQueryHandler(start, pattern=r"^restart$"),
            CallbackQueryHandler(show_language, pattern=r"^change_lang$"),
            CallbackQueryHandler(set_language, pattern=r"^lang:"),
        ],
        states={
            SERVICE: [CallbackQueryHandler(choose_service, pattern=r"^service:"), cancel_cb],
            MASTER: [
                CallbackQueryHandler(choose_master, pattern=r"^master:"),
                CallbackQueryHandler(start, pattern=r"^back$"),
                cancel_cb,
            ],
            DATE: [
                CallbackQueryHandler(choose_date, pattern=r"^date:"),
                CallbackQueryHandler(back_from_dates, pattern=r"^back_master$"),
                cancel_cb,
            ],
            TIME: [
                CallbackQueryHandler(choose_time, pattern=r"^time:"),
                CallbackQueryHandler(show_dates, pattern=r"^back_date$"),
                cancel_cb,
            ],
            NAME: [MessageHandler(text_input, get_name), cancel_cb],
            PHONE: [MessageHandler(text_input, get_phone), cancel_cb],
            CONFIRM: [CallbackQueryHandler(confirm, pattern=r"^confirm$"), cancel_cb],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),  # /start на любом шаге — начать заново
        ],
        allow_reentry=True,
    )
    app.add_handler(conv)

    # «Мои записи» и отмена — вне диалога, доступны в любой момент
    app.add_handler(CommandHandler("mybookings", my_bookings))
    app.add_handler(CallbackQueryHandler(my_bookings, pattern=r"^my$"))
    app.add_handler(CallbackQueryHandler(ask_cancel_booking, pattern=r"^cb:"))
    app.add_handler(CallbackQueryHandler(do_cancel_booking, pattern=r"^cy:"))

    app.add_error_handler(error_handler)

    # Напоминания (нужен extra job-queue: pip install "python-telegram-bot[job-queue]")
    if app.job_queue is None:
        logger.warning(
            'Напоминания отключены: установите пакет командой '
            'pip install "python-telegram-bot[job-queue]"'
        )
    else:
        app.job_queue.run_repeating(
            send_reminders, interval=REMINDER_CHECK_MINUTES * 60, first=20
        )

    logger.info("Бот «Mademoiselle» запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()