"""
🥗 NutritionBot — расчёт КБЖУ по Харрису-Бенедикту
Стек: aiogram 3.x | SQLite | FSM | HTML-разметка
Установка: pip install aiogram aiosqlite
"""

import asyncio
import logging
import sqlite3
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ─────────────────────────────────────────────
# ⚙️  КОНФИГ
# ─────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "8736413039:AAH7Snz2mGy9heZ9GwbMScoJicOwNPlUVsE")
DB_PATH   = "nutrition_bot.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 🗄️  БАЗА ДАННЫХ
# ─────────────────────────────────────────────
def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id       INTEGER PRIMARY KEY,
                username      TEXT,
                first_name    TEXT,
                age           INTEGER,
                gender        TEXT,
                weight        REAL,
                height        REAL,
                activity      REAL,
                goal          TEXT,
                calories      REAL,
                protein       REAL,
                fat           REAL,
                carbs         REAL,
                subscribed    INTEGER DEFAULT 0,
                created_at    TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()


def upsert_user(user_id: int, username: str, first_name: str) -> None:
    with db_conn() as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username   = excluded.username,
                first_name = excluded.first_name
        """, (user_id, username, first_name))
        conn.commit()


def save_kbju(user_id: int, age: int, gender: str, weight: float, height: float,
              activity: float, goal: str, calories: float,
              protein: float, fat: float, carbs: float) -> None:
    with db_conn() as conn:
        conn.execute("""
            UPDATE users SET
                age=?, gender=?, weight=?, height=?, activity=?, goal=?,
                calories=?, protein=?, fat=?, carbs=?
            WHERE user_id=?
        """, (age, gender, weight, height, activity, goal,
              calories, protein, fat, carbs, user_id))
        conn.commit()


def get_user(user_id: int) -> sqlite3.Row | None:
    with db_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def set_subscribed(user_id: int) -> None:
    with db_conn() as conn:
        conn.execute("UPDATE users SET subscribed=1 WHERE user_id=?", (user_id,))
        conn.commit()


# ─────────────────────────────────────────────
# 🧮  ФОРМУЛА ХАРРИСА-БЕНЕДИКТА
# ─────────────────────────────────────────────
ACTIVITY_LABELS = {
    1.20: "🛋 Сидячий (офис/дом)",
    1.375: "🚶 Лёгкая активность (1–2 трен./нед.)",
    1.55: "🏃 Умеренная (3–5 трен./нед.)",
    1.725: "💪 Высокая (6–7 трен./нед.)",
    1.90: "🔥 Очень высокая (физ. труд + спорт)",
}

GOAL_LABELS = {
    "loss":  "⬇️ Похудение (-15% калорий)",
    "keep":  "➡️ Поддержание веса",
    "gain":  "⬆️ Набор массы (+15% калорий)",
}

GOAL_COEFF = {"loss": 0.85, "keep": 1.0, "gain": 1.15}


def harris_benedict(age: int, gender: str, weight: float, height: float) -> float:
    """Базовый обмен (BMR)."""
    if gender == "male":
        return 66 + 13.7 * weight + 5 * height - 6.8 * age
    return 655 + 9.6 * weight + 1.8 * height - 4.7 * age


MACRO_RATIOS = {
    "loss": {"protein": 0.25, "fat": 0.35, "carbs": 0.40},
    "keep": {"protein": 0.30, "fat": 0.25, "carbs": 0.45},
    "gain": {"protein": 0.25, "fat": 0.25, "carbs": 0.50},
}


def calculate_kbju(age: int, gender: str, weight: float, height: float,
                   activity: float, goal: str) -> tuple[int, int, int, int]:
    bmr     = harris_benedict(age, gender, weight, height)
    tdee    = bmr * activity
    kcal    = tdee * GOAL_COEFF[goal]   # цель по калориям (±15%)
    ratios  = MACRO_RATIOS[goal]
    # БЖУ считаем от TDEE (общий обмен без поправки на цель)
    protein = tdee * ratios["protein"] / 4
    fat     = tdee * ratios["fat"] / 9
    carbs   = tdee * ratios["carbs"] / 4
    return round(kcal), round(protein), round(fat), round(carbs)


# ─────────────────────────────────────────────
# 🍽️  ПРИМЕРНОЕ МЕНЮ
# ─────────────────────────────────────────────
def build_menu(calories: int, goal: str) -> str:
    """Генерирует примерное меню без граммовок — только продукты."""

    if goal == "gain":
        menu = f"""
🌅 <b>Завтрак — 15%</b> (~{round(calories * 0.15)} ккал)
<i>Белок + жиры + углеводы низкого и высокого ГИ</i>
• Яйца варёные / омлет / скрэмбл
• Овсянка на молоке / рисовая каша
• Арахисовая или миндальная паста <i>(жиры)</i>
• Банан / финики <i>(высокий ГИ)</i>

🥐 <b>2-й завтрак — 15%</b> (~{round(calories * 0.15)} ккал)
<i>Белок + жиры + углеводы низкого и высокого ГИ</i>
• Творог 5% / греческий йогурт 2%
• Орехи (грецкие / миндаль / кешью) <i>(жиры)</i>
• Цельнозерновой хлеб / хлебцы <i>(низкий ГИ)</i>
• Мёд / джем <i>(высокий ГИ)</i>

🥗 <b>Обед — 35%</b> (~{round(calories * 0.35)} ккал)
<i>Белок + жиры + углеводы низкого и высокого ГИ</i>
• Куриное бедро / говядина / лосось
• Белый рис / макароны из тв. сортов / картофель <i>(высокий ГИ)</i>
• Булгур / нут <i>(низкий ГИ)</i>
• Авокадо / оливковое масло / сыр <i>(жиры)</i>

🍎 <b>Перекус — 10%</b> (~{round(calories * 0.10)} ккал)
<i>Белок + углеводы низкого ГИ (без жиров)</i>
• Греческий йогурт 2% / кефир / протеиновый коктейль
• Яблоко / груша / апельсин <i>(низкий ГИ)</i>

🌙 <b>Ужин — 25%</b> (~{round(calories * 0.25)} ккал)
<i>Белок + углеводы низкого ГИ (без жиров и высокого ГИ)</i>
• Куриная грудка / индейка / минтай / треска / кальмар
• Гречка / бурый рис / чечевица <i>(низкий ГИ)</i>
• Тушёные овощи: брокколи / кабачок / шпинат / спаржа"""

    elif goal == "loss":
        menu = f"""
🌅 <b>Завтрак — 25%</b> (~{round(calories * 0.25)} ккал)
<i>Белок + жиры + углеводы</i>
• Яйца варёные / омлет / скрэмбл
• Овсянка на воде / гречка / ячневая каша <i>(низкий ГИ)</i>
• Авокадо / оливковое масло / семена льна <i>(жиры)</i>
• Ягоды (черника, клубника) / яблоко

🥐 <b>2-й завтрак — 17%</b> (~{round(calories * 0.17)} ккал)
<i>Белок + жиры + углеводы низкого ГИ</i>
• Творог 5% / греческий йогурт 0–2% / кефир 1%
• Орехи (миндаль / грецкие / фундук) <i>(жиры)</i>
• Яблоко / грейпфрут / огурцы с зеленью <i>(низкий ГИ)</i>

🥗 <b>Обед — 35%</b> (~{round(calories * 0.35)} ккал)
<i>Белок + умеренные жиры + углеводы</i>
• Куриная грудка / индейка / тунец / лосось
• Гречка / булгур / нут / чечевица <i>(низкий ГИ)</i>
• Овощной салат с оливковым маслом <i>(умеренные жиры)</i>

🍊 <b>Перекус — 13%</b> (~{round(calories * 0.13)} ккал)
<i>Белок + углеводы низкого ГИ (без жиров)</i>
• Греческий йогурт 0% / кефир 1% / творог 0%
• Огурец / сельдерей / болгарский перец / яблоко <i>(низкий ГИ)</i>

🌙 <b>Ужин — 10%</b> (~{round(calories * 0.10)} ккал)
<i>Только белок + клетчатка (без жиров и высокого ГИ)</i>
• Минтай / треска / пикша / кальмар / креветки
• Тушёные овощи: брокколи / стручковая фасоль / шпинат / цветная капуста
• Зелень, специи, лимонный сок"""

    else:  # keep
        menu = f"""
🌅 <b>Завтрак — 25%</b> (~{round(calories * 0.25)} ккал)
• Яйца (омлет / варёные / скрэмбл)
• Овсянка на молоке / гречка
• Цельнозерновой тост / хлебцы
• Оливковое масло / авокадо <i>(жиры)</i>

🥗 <b>Обед — 35%</b> (~{round(calories * 0.35)} ккал)
• Куриная грудка / индейка / говядина / лосось
• Гречка / бурый рис / булгур
• Овощной салат с оливковым маслом

🍊 <b>Перекус — 10%</b> (~{round(calories * 0.10)} ккал)
• Творог 5% / греческий йогурт / кефир
• Фрукт по сезону / ягоды

🌙 <b>Ужин — 30%</b> (~{round(calories * 0.30)} ккал)
• Рыба (лосось / минтай / тунец) / телятина
• Тушёные / свежие овощи (брокколи, кабачок, морковь)
• Кефир 1% / натуральный йогурт"""

    return menu.strip()


# ─────────────────────────────────────────────
# ⌨️  КЛАВИАТУРЫ
# ─────────────────────────────────────────────
def kb_gender() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👨 Мужской", callback_data="gender_male"),
        InlineKeyboardButton(text="👩 Женский",  callback_data="gender_female"),
    ]])


def kb_activity() -> InlineKeyboardMarkup:
    rows = []
    for coeff, label in ACTIVITY_LABELS.items():
        rows.append([InlineKeyboardButton(text=label, callback_data=f"act_{coeff}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_goal() -> InlineKeyboardMarkup:
    rows = []
    for key, label in GOAL_LABELS.items():
        rows.append([InlineKeyboardButton(text=label, callback_data=f"goal_{key}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_result() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🍽 Показать меню на день",   callback_data="show_menu")],
        [InlineKeyboardButton(text="📖 Как использовать КБЖУ",  callback_data="show_tips")],
        [InlineKeyboardButton(text="🤝 Работа со мной",          callback_data="coaching")],
        [InlineKeyboardButton(text="🔄 Пересчитать",            callback_data="restart")],
    ])


def kb_coaching() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Написать Дмитрию", url="https://t.me/dmitry_nutri")],
        [InlineKeyboardButton(text="◀️ Назад",            callback_data="back_result")],
    ])


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад к результату", callback_data="back_result")],
    ])


# ─────────────────────────────────────────────
# 📋  FSM СОСТОЯНИЯ
# ─────────────────────────────────────────────
class Form(StatesGroup):
    age      = State()
    gender   = State()
    weight   = State()
    height   = State()
    activity = State()
    goal     = State()


# ─────────────────────────────────────────────
# 🤖  ХЭНДЛЕРЫ
# ─────────────────────────────────────────────
dp = Dispatcher(storage=MemoryStorage())


# /start
@dp.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext) -> None:
    upsert_user(msg.from_user.id, msg.from_user.username or "", msg.from_user.first_name)
    await state.clear()
    name = msg.from_user.first_name or "друг"
    await msg.answer(
        f"👋 Привет, <b>{name}</b>!\n\n"
        "Я рассчитаю твои индивидуальные <b>КБЖУ</b> (калории, белки, жиры, углеводы) "
        "по формуле <b>Харриса-Бенедикта</b> и составлю пример меню.\n\n"
        "Это займёт ~1 минуту. Начнём? 🚀\n\n"
        "❓ <b>Сколько тебе лет?</b> (введи число, например: <code>28</code>)",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Form.age)


# Возраст
@dp.message(Form.age)
async def process_age(msg: Message, state: FSMContext) -> None:
    try:
        age = int(msg.text.strip())
        assert 10 <= age <= 100
    except (ValueError, AssertionError):
        await msg.answer("⚠️ Введи корректный возраст от 10 до 100 лет.")
        return
    await state.update_data(age=age)
    await msg.answer(
        f"✅ Возраст: <b>{age} лет</b>\n\n❓ <b>Укажи пол:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_gender(),
    )
    await state.set_state(Form.gender)


# Пол
@dp.callback_query(Form.gender, F.data.startswith("gender_"))
async def process_gender(call: CallbackQuery, state: FSMContext) -> None:
    gender = call.data.split("_")[1]
    label  = "Мужской" if gender == "male" else "Женский"
    await state.update_data(gender=gender)
    await call.message.edit_text(
        f"✅ Пол: <b>{label}</b>\n\n"
        "❓ <b>Введи вес</b> в кг (например: <code>75.5</code>)",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Form.weight)


# Вес
@dp.message(Form.weight)
async def process_weight(msg: Message, state: FSMContext) -> None:
    try:
        weight = float(msg.text.strip().replace(",", "."))
        assert 30 <= weight <= 300
    except (ValueError, AssertionError):
        await msg.answer("⚠️ Введи корректный вес от 30 до 300 кг.")
        return
    await state.update_data(weight=weight)
    await msg.answer(
        f"✅ Вес: <b>{weight} кг</b>\n\n"
        "❓ <b>Введи рост</b> в см (например: <code>178</code>)",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Form.height)


# Рост
@dp.message(Form.height)
async def process_height(msg: Message, state: FSMContext) -> None:
    try:
        height = float(msg.text.strip().replace(",", "."))
        assert 100 <= height <= 250
    except (ValueError, AssertionError):
        await msg.answer("⚠️ Введи корректный рост от 100 до 250 см.")
        return
    await state.update_data(height=height)
    await msg.answer(
        f"✅ Рост: <b>{height} см</b>\n\n"
        "❓ <b>Выбери уровень физической активности:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_activity(),
    )
    await state.set_state(Form.activity)


# Активность
@dp.callback_query(Form.activity, F.data.startswith("act_"))
async def process_activity(call: CallbackQuery, state: FSMContext) -> None:
    coeff = float(call.data.replace("act_", ""))
    label = ACTIVITY_LABELS.get(coeff, str(coeff))
    await state.update_data(activity=coeff)
    await call.message.edit_text(
        f"✅ Активность: <b>{label}</b>\n\n"
        "❓ <b>Какова твоя цель?</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_goal(),
    )
    await state.set_state(Form.goal)


# Цель → финальный расчёт
@dp.callback_query(Form.goal, F.data.startswith("goal_"))
async def process_goal(call: CallbackQuery, state: FSMContext) -> None:
    goal = call.data.replace("goal_", "")
    data = await state.get_data()
    data["goal"] = goal

    age      = data["age"]
    gender   = data["gender"]
    weight   = data["weight"]
    height   = data["height"]
    activity = data["activity"]

    calories, protein, fat, carbs = calculate_kbju(age, gender, weight, height, activity, goal)

    save_kbju(call.from_user.id, age, gender, weight, height,
              activity, goal, calories, protein, fat, carbs)

    await state.update_data(calories=calories, protein=protein, fat=fat, carbs=carbs)
    await state.set_state(None)

    bmr = round(harris_benedict(age, gender, weight, height))
    tdee = round(bmr * activity)
    goal_label   = GOAL_LABELS[goal]
    gender_label = "Мужской" if gender == "male" else "Женский"
    activity_label = ACTIVITY_LABELS.get(activity, str(activity))

    result_text = (
        "✅ <b>Расчёт КБЖУ готов!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "👤 <b>Твои данные:</b>\n"
        f"  • Возраст: <b>{age} лет</b>\n"
        f"  • Пол: <b>{gender_label}</b>\n"
        f"  • Вес: <b>{weight} кг</b> | Рост: <b>{height} см</b>\n"
        f"  • Активность: <b>{activity_label}</b>\n"
        f"  • Цель: <b>{goal_label}</b>\n\n"
        "🔬 <b>Формула Харриса-Бенедикта:</b>\n"
        f"  • Базовый обмен: <b>{bmr} ккал</b>\n"
        f"  • С учётом активности: <b>{tdee} ккал</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 <b>Твоя норма в день:</b>\n\n"
        f"  🔥 Калории: <b>{calories} ккал</b>\n"
        f"  🥩 Белки:   <b>{protein} г</b> ({round(protein*4/calories*100)}%)\n"
        f"  🧈 Жиры:    <b>{fat} г</b>    ({round(fat*9/calories*100)}%)\n"
        f"  🍞 Углеводы: <b>{carbs} г</b>  ({round(carbs*4/calories*100)}%)\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⬇️ Выбери, что делать дальше:"
    )

    await call.message.edit_text(
        result_text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb_result(),
    )


# ── Кнопка: меню на день ──────────────────────────────────
@dp.callback_query(F.data == "show_menu")
async def cb_show_menu(call: CallbackQuery, state: FSMContext) -> None:
    row = get_user(call.from_user.id)
    if not row or not row["calories"]:
        await call.answer("Сначала пройди расчёт — /start", show_alert=True)
        return

    menu = build_menu(int(row["calories"]), row["goal"])
    goal_label = GOAL_LABELS.get(row["goal"], row["goal"])

    text = (
        f"🍽 <b>Примерное меню на день</b>\n"
        f"Цель: {goal_label}\n"
        f"Калорийность: <b>~{int(row['calories'])} ккал</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{menu}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Это шаблон — меняй продукты в рамках КБЖУ.\n"
        "Хочешь меню с граммовками и персональным ведением? 👇</i>"
    )

    await call.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🤝 Работа со мной",      callback_data="coaching")],
            [InlineKeyboardButton(text="◀️ Назад к результату", callback_data="back_result")],
        ]),
    )


# ── Кнопка: советы ───────────────────────────────────────
@dp.callback_query(F.data == "show_tips")
async def cb_show_tips(call: CallbackQuery) -> None:
    text = (
        "📖 <b>Общие рекомендации по питанию и образу жизни</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "1️⃣ <b>Вода — основа</b>\n"
        "   Норма: <b>35–40 мл на каждый кг</b> массы тела в день.\n"
        "   При весе 70 кг — это 2,5–2,8 л. "
        "Жажда часто маскируется под голод.\n\n"
        "2️⃣ <b>Сон до 23:00</b>\n"
        "   Именно с 23:00 до 01:00 происходит пик выработки гормона роста "
        "и восстановления мышц. Ложись спать до 23:00 и спи 7–9 часов.\n\n"
        "3️⃣ <b>Установи приложение для учёта</b>\n"
        "   MyFitnessPal, FatSecret или Cronometer — "
        "сканируй штрихкоды и взвешивай продукты.\n\n"
        "4️⃣ <b>Взвешивай еду</b>\n"
        "   Кухонные весы — главный инструмент. "
        "Разница между «горстью» и реальным весом — до 50%.\n\n"
        "5️⃣ <b>Жиры — в первой половине дня</b>\n"
        "   Авокадо, орехи, масло, яичные желтки — до 14:00–15:00. "
        "Вечером жиры тормозят восстановление и жиросжигание.\n\n"
        "6️⃣ <b>Контроль гликемического индекса</b>\n"
        "   Высокий ГИ (белый рис, банан, мёд) — допустим до ужина. "
        "На ужин — только низкий ГИ: гречка, бурый рис, овощи.\n\n"
        "7️⃣ <b>Белок — приоритет</b>\n"
        "   Высокобелковая диета сохраняет мышцы при похудении "
        "и ускоряет восстановление при наборе.\n\n"
        "8️⃣ <b>Пересчитывай каждые 4–6 недель</b>\n"
        "   Вес меняется → меняется и норма КБЖУ. "
        "Используй /start для пересчёта.\n\n"
        "9️⃣ <b>Дефицит ≠ голод</b>\n"
        "   При похудении дефицит не должен превышать 500 ккал/день — "
        "иначе организм включает режим экономии.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🤝 <i>Хочешь индивидуальный план, граммовки и личное сопровождение?</i>"
    )
    await call.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🤝 Работа со мной", callback_data="coaching")],
            [InlineKeyboardButton(text="◀️ Назад",          callback_data="back_result")],
        ]),
    )


# ── Кнопка: работа со мной ───────────────────────────────
@dp.callback_query(F.data.in_({"coaching", "subscribe"}))
async def cb_coaching(call: CallbackQuery) -> None:
    text = (
        "🤝 <b>Персональное сопровождение</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Бот даёт расчёт — но цифры сами по себе не работают.\n"
        "В персональной работе со мной ты получаешь:\n\n"
        "✅ Индивидуальный план питания под твои параметры\n"
        "✅ Разбор твоего рациона и привычек\n"
        "✅ Еженедельная корректировка по результатам\n"
        "✅ Поддержка и ответы на вопросы в любое время\n"
        "✅ Контроль динамики веса и состава тела\n"
        "✅ Меню с граммовками под каждый приём пищи\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👇 Напиши мне — обсудим твою цель и формат работы:"
    )
    await call.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb_coaching(),
    )


# ── Кнопка: назад к результату ───────────────────────────
@dp.callback_query(F.data == "back_result")
async def cb_back_result(call: CallbackQuery) -> None:
    row = get_user(call.from_user.id)
    if not row or not row["calories"]:
        await call.answer("Нет сохранённых данных. Начни заново: /start", show_alert=True)
        return

    bmr = round(harris_benedict(row["age"], row["gender"], row["weight"], row["height"]))
    tdee = round(bmr * row["activity"])
    calories = int(row["calories"])
    protein  = int(row["protein"])
    fat      = int(row["fat"])
    carbs    = int(row["carbs"])
    gender_label   = "Мужской" if row["gender"] == "male" else "Женский"
    activity_label = ACTIVITY_LABELS.get(row["activity"], str(row["activity"]))
    goal_label     = GOAL_LABELS.get(row["goal"], row["goal"])

    text = (
        "✅ <b>Твой расчёт КБЖУ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "👤 <b>Данные:</b>\n"
        f"  • Пол: <b>{gender_label}</b> | Возраст: <b>{row['age']} лет</b>\n"
        f"  • Вес: <b>{row['weight']} кг</b> | Рост: <b>{row['height']} см</b>\n"
        f"  • Активность: <b>{activity_label}</b>\n"
        f"  • Цель: <b>{goal_label}</b>\n\n"
        "🔬 <b>Харрис-Бенедикт:</b>\n"
        f"  BMR: <b>{bmr} ккал</b> → TDEE: <b>{tdee} ккал</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 <b>Норма на день:</b>\n\n"
        f"  🔥 Калории: <b>{calories} ккал</b>\n"
        f"  🥩 Белки:   <b>{protein} г</b>\n"
        f"  🧈 Жиры:    <b>{fat} г</b>\n"
        f"  🍞 Углеводы: <b>{carbs} г</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⬇️ Выбери действие:"
    )
    await call.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_result())


# ── Кнопка: перезапуск ───────────────────────────────────
@dp.callback_query(F.data == "restart")
async def cb_restart(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text(
        "🔄 Начинаем заново!\n\n"
        "❓ <b>Сколько тебе лет?</b> (введи число, например: <code>28</code>)",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(Form.age)


# /profile
@dp.message(Command("profile"))
async def cmd_profile(msg: Message) -> None:
    row = get_user(msg.from_user.id)
    if not row or not row["calories"]:
        await msg.answer("Профиль не найден. Запусти /start для расчёта.")
        return

    created = row["created_at"][:10] if row["created_at"] else "—"
    goal_label = GOAL_LABELS.get(row["goal"], row["goal"])

    text = (
        f"👤 <b>Твой профиль</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"  Имя: <b>{msg.from_user.first_name}</b>\n"
        f"  Аккаунт с: <b>{created}</b>\n\n"
        f"  Вес: <b>{row['weight']} кг</b> | Рост: <b>{row['height']} см</b>\n"
        f"  Цель: <b>{goal_label}</b>\n\n"
        "🎯 <b>Норма КБЖУ:</b>\n"
        f"  🔥 <b>{int(row['calories'])} ккал</b>\n"
        f"  🥩 Белки: <b>{int(row['protein'])} г</b>\n"
        f"  🧈 Жиры: <b>{int(row['fat'])} г</b>\n"
        f"  🍞 Углеводы: <b>{int(row['carbs'])} г</b>"
    )
    await msg.answer(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Пересчитать КБЖУ", callback_data="restart")],
            [InlineKeyboardButton(text="🤝 Работа со мной",   callback_data="coaching")],
        ]),
    )


# /help
@dp.message(Command("help"))
async def cmd_help(msg: Message) -> None:
    await msg.answer(
        "🤖 <b>NutritionBot — команды:</b>\n\n"
        "/start — 🚀 Новый расчёт КБЖУ\n"
        "/profile — 👤 Мой профиль и результаты\n"
        "/help — ❓ Помощь\n\n"
        "<b>Что умеет бот:</b>\n"
        "• Расчёт нормы калорий, белков, жиров, углеводов\n"
        "• Формула Харриса-Бенедикта с учётом активности\n"
        "• Пример меню под твою цель\n"
        "• Советы по применению КБЖУ в жизни",
        parse_mode=ParseMode.HTML,
    )


# Неизвестное сообщение
@dp.message()
async def fallback(msg: Message) -> None:
    await msg.answer(
        "Используй /start для расчёта КБЖУ или /help для справки.",
        parse_mode=ParseMode.HTML,
    )


# ─────────────────────────────────────────────
# 🚀  ЗАПУСК
# ─────────────────────────────────────────────
async def main() -> None:
    init_db()
    bot = Bot(token=BOT_TOKEN)

    # Сбрасываем webhook и выбиваем любые конкурирующие соединения
    await bot.delete_webhook(drop_pending_updates=True)
    log.info("🔌 Старые соединения разорваны, webhook сброшен")

    log.info("✅ Bot started")
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            close_bot_session=True,
        )
    finally:
        await bot.session.close()
        log.info("🛑 Сессия бота закрыта")


if __name__ == "__main__":
    asyncio.run(main())