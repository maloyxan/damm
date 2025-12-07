import asyncio
import logging
import sys
import random
import aiosqlite
import json
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove, FSInputFile, ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiocryptopay import AioCryptoPay, Networks

# ================= КОНФИГУРАЦИЯ =================

# ❗ ВСТАВЬТЕ СВОИ ДАННЫЕ ЗДЕСЬ
BOT_TOKEN = "7989899932:AAEaMozTbnqx4Fnbl41Qm1cq7UlkZG-jrSs"
CRYPTO_TOKEN = "456720:AAv9nYNWYopIgi8RiivYHrAHk16ibOWfw4j"
ADMIN_IDS = [7834799163, 7623901324]

# Настройки сети
CURRENT_NETWORK = Networks.MAIN_NET

# Настройки казино
MIN_BET = 1
MAX_BET = 1000
MIN_WITHDRAW = 10
PVP_COMMISSION = 0.05

# Настройки Бонусов
INITIAL_BONUS = 3.0
WAGER_MULTIPLIER = 65

LOSE_QUOTES = [
    "Кратковременная неудача лучше кратковременной удачи. — Харун",
    "Неудача — это просто возможность начать снова, но уже более мудро. — Генри Форд",
    "Успех — это умение двигаться от неудачи к неудаче, не теряя энтузиазма. — Черчилль",
    "Иногда нужно проиграть битву, чтобы выиграть войну.",
    "Не везет в картах — повезет в любви!",
    "Удача любит смелых. Попробуй еще раз!",
    "Падение — не провал. Провал — это остаться лежать."
]

DB_NAME = "bearsbet_v888.db"

# Хранилище активных игр Mines в оперативной памяти
MINES_GAMES = {}

# Ранговая система
RANKS = {
    0: "👶 Новичок",
    100: "🥉 Игрок",
    500: "🥈 Любитель",
    1000: "🥇 Профи",
    5000: "💎 Магнат",
    10000: "👑 Король Азарта"
}

# ================= ИНИЦИАЛИЗАЦИЯ =================

bot = Bot(token=BOT_TOKEN)
cryptopay = AioCryptoPay(token=CRYPTO_TOKEN, network=CURRENT_NETWORK)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# ================= FSM =================
class UserState(StatesGroup):
    deposit_amount = State()
    withdraw_amount = State()
    bet_amount = State()
    broadcast_text = State()

    mines_bet = State()
    mines_count = State()

    pvp_bet = State()

# ================= БАЗА ДАННЫХ =================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Создаем таблицу users, если нет
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                user_id INTEGER UNIQUE,
                username TEXT,
                balance REAL DEFAULT 0.0,
                bonus_balance REAL DEFAULT 3.0,
                ref_balance REAL DEFAULT 0.0,
                games_played INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                max_win REAL DEFAULT 0.0,
                total_withdrawn REAL DEFAULT 0.0,
                referrer_id INTEGER,
                reg_date TEXT,
                task_login_claimed INTEGER DEFAULT 0,
                task_bet_done INTEGER DEFAULT 0,
                turnover REAL DEFAULT 0.0,
                last_daily_bonus TEXT,
                bonus_wager_required REAL DEFAULT 0.0,
                bonus_wagered REAL DEFAULT 0.0
            )
        """)

        # --- МИГРАЦИЯ: Добавляем колонки, если база старая ---
        try:
            await db.execute("ALTER TABLE users ADD COLUMN turnover REAL DEFAULT 0.0")
            print("✅ Колонка turnover добавлена.")
        except Exception:
            pass

        try:
            await db.execute("ALTER TABLE users ADD COLUMN last_daily_bonus TEXT")
            print("✅ Колонка last_daily_bonus добавлена.")
        except Exception:
            pass

        try:
            await db.execute("ALTER TABLE users ADD COLUMN bonus_wager_required REAL DEFAULT 0.0")
            print("✅ Колонка bonus_wager_required добавлена.")
        except Exception: pass

        try:
            await db.execute("ALTER TABLE users ADD COLUMN bonus_wagered REAL DEFAULT 0.0")
            print("✅ Колонка bonus_wagered добавлена.")
        except Exception: pass
        # -----------------------------------------------------

        # Таблица депозитов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS deposits (
                id INTEGER PRIMARY KEY,
                user_id INTEGER,
                amount REAL,
                invoice_id INTEGER UNIQUE,
                status TEXT
            )
        """)
        # Таблица ручного топа
        await db.execute("""
            CREATE TABLE IF NOT EXISTS manual_top (
                username TEXT,
                amount REAL
            )
        """)
        # Таблица PvP игр
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pvp_games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_id INTEGER,
                creator_name TEXT,
                bet_amount REAL,
                status TEXT DEFAULT 'waiting'
            )
        """)
        await db.commit()

async def get_user_data(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                columns = [description[0] for description in cursor.description]
                return dict(zip(columns, row))
            return None

async def update_stat(user_id, field, value, mode='+'):
    async with aiosqlite.connect(DB_NAME) as db:
        # Установка значения для bonus_wager_required, если mode='='
        if mode == '=':
            await db.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
        else:
            await db.execute(f"UPDATE users SET {field} = {field} {mode} ? WHERE user_id = ?", (value, user_id))
        await db.commit()

async def add_turnover(user_id, amount):
    await update_stat(user_id, "turnover", amount, '+')

def get_rank(turnover):
    if turnover is None: turnover = 0
    current_rank = "👶 Новичок"
    for threshold, title in sorted(RANKS.items()):
        if turnover >= threshold:
            current_rank = title
        else:
            break
    return current_rank

# ================= КЛАВИАТУРЫ =================

def get_main_reply_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎲 Играть"), KeyboardButton(text="👤 Профиль")],
            [KeyboardButton(text="💰 Пополнить"), KeyboardButton(text="👥 Реф.Программа")],
            [KeyboardButton(text="🎡 Ежедневный бонус"), KeyboardButton(text="⚔️ PvP Битвы")],
            [KeyboardButton(text="ℹ️ О нас")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите пункт меню..."
    )

def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Играть", callback_data="menu_play")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="menu_profile"),
         InlineKeyboardButton(text="💰 Пополнить", callback_data="menu_deposit")],
        [InlineKeyboardButton(text="👥 Рефералы", callback_data="menu_ref"),
         InlineKeyboardButton(text="ℹ️ О нас", callback_data="menu_about")]
    ])

def balance_select_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Баланс", callback_data="sel_bal_real")],
        [InlineKeyboardButton(text="🎁 Бонусный баланс", callback_data="sel_bal_bonus")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_main")]
    ])

def back_to_main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_main")]
    ])

def games_kb():
    kb = [
        [InlineKeyboardButton(text="💣 Сапер (Mines)", callback_data="mines_menu"),
         InlineKeyboardButton(text="⚔️ PvP Арена", callback_data="pvp_menu")],
        [InlineKeyboardButton(text="🎲 Кубик", callback_data="play_dice"), InlineKeyboardButton(text="⚽ Футбол", callback_data="play_football")],
        [InlineKeyboardButton(text="🏀 Баскетбол", callback_data="play_basket"), InlineKeyboardButton(text="🎰 Слоты", callback_data="play_slots")],
        [InlineKeyboardButton(text="🎳 Боулинг", callback_data="play_bowling"), InlineKeyboardButton(text="🎯 Дартс", callback_data="play_darts")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_play")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


# ================= ГЛАВНОЕ МЕНЮ И СТАРТ =================

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username or "User"
    referrer_id = None

    args = command.args
    if args and args.isdigit():
        potential_ref = int(args)
        if potential_ref != user_id:
            referrer_id = potential_ref

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT id FROM users WHERE user_id = ?", (user_id,))
        exists = await cursor.fetchone()

        if not exists:
            # Установка начального требования по отыгрышу x50
            wager_required = INITIAL_BONUS * WAGER_MULTIPLIER
            await db.execute("""
                INSERT INTO users (user_id, username, reg_date, referrer_id, bonus_balance, turnover, bonus_wager_required)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, username, datetime.now().strftime("%Y-%m-%d %H:%M"), referrer_id, INITIAL_BONUS, 0.0, wager_required))
            await db.commit()
            if referrer_id:
                try:
                    await bot.send_message(referrer_id, f"У вас новый реферал! 🚀 @{username}")
                except: pass

    await message.answer("🐻 <b>Добро пожаловать в BearsBet!</b>", reply_markup=get_main_reply_kb(), parse_mode="HTML")
    await message.answer("Выберите действие:", reply_markup=main_menu_kb(), parse_mode="HTML")

@router.callback_query(F.data == "menu_main")
async def back_to_menu_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except:
        pass
    await callback.message.answer("🐻 <b>Главное меню:</b>", reply_markup=main_menu_kb(), parse_mode="HTML")

# ================= ОБРАБОТЧИКИ REPLY КНОПОК =================

@router.message(F.text == "🎲 Играть")
async def txt_play(message: Message, state: FSMContext):
    # Добавлено видео
    try:
        vid = FSInputFile("sources/balanceorbonus.mp4")
        await message.answer_video(vid, caption="Выберите счет для игры:", reply_markup=balance_select_kb())
    except:
        await message.answer("Выберите счет для игры:", reply_markup=balance_select_kb())

@router.message(F.text == "👤 Профиль")
async def txt_profile(message: Message):
    data = await get_user_data(message.from_user.id)
    if not data: return

    reg_dt = datetime.strptime(data['reg_date'], "%Y-%m-%d %H:%M")
    days_with_us = (datetime.now() - reg_dt).days
    total_games = data['games_played']
    win_rate = (data['wins'] / total_games * 100) if total_games > 0 else 0
    rank = get_rank(data.get('turnover', 0))

    # Расчет прогресса отыгрыша
    wagered = data.get('bonus_wagered', 0.0)
    required = data.get('bonus_wager_required', 0.0)
    wager_status = "✅ Выполнено" if required > 0 and wagered >= required else f"⏳ {wagered:.2f}/{required:.2f}$"

    text = (
        f"👤 <b>Личный кабинет</b>\n"
        f"🏅 Ранг: <b>{rank}</b> (Оборот: {data.get('turnover', 0):.2f}$)\n"
        f"➖➖➖➖➖➖➖➖\n"
        f"💰 Баланс: <b>{data['balance']:.2f}$</b>\n"
        f"🎁 Бонусный баланс: <b>{data['bonus_balance']:.2f}$</b> (Отыгрыш: {wager_status})\n"
        f"💎 Реф. баланс: <b>{data['ref_balance']:.2f}$</b>\n"
        f"➖➖➖➖➖➖➖➖\n"
        f"🏆 Макс. выигрыш: {data['max_win']:.2f}$\n"
        f"📊 Винрейт: {win_rate:.1f}%\n"
        f"🗓 Вы с нами: {days_with_us} дн.\n"
        f"💸 Выведено: {data['total_withdrawn']:.2f}$"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Вывод", callback_data="withdraw_menu"),
         InlineKeyboardButton(text="🏆 Топ", callback_data="top_players")],
        [InlineKeyboardButton(text="🎁 Бонусы", callback_data="bonuses_menu"),
         InlineKeyboardButton(text="🎁 Вывести Бонусы", callback_data="withdraw_bonus")]
    ])

    try:
        vid = FSInputFile("sources/profilevideo.mp4")
        await message.answer_video(video=vid, caption=text, reply_markup=kb, parse_mode="HTML")
    except:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.message(F.text == "💰 Пополнить")
async def txt_deposit(message: Message, state: FSMContext):
    await state.set_state(UserState.deposit_amount)
    await message.answer(
        "💰 Введите сумму пополнения в $ (USDT):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="menu_main")]])
    )

@router.message(F.text == "👥 Реф.Программа")
async def txt_ref(message: Message):
    data = await get_user_data(message.from_user.id)

    async with aiosqlite.connect(DB_NAME) as db:
        res = await db.execute_fetchall("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (message.from_user.id,))
        ref_count = res[0][0]

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"

    text = (
        "👥 <b>Реферальная система</b>\n\n"
        "Твоя комиссия — 10% с проигрышных ставок рефералов.\n\n"
        "<b>За всё время</b>\n"
        f"<blockquote>Заработанно: {data['ref_balance']:.2f}$\n"
        f"Рефералы: {ref_count}</blockquote>\n\n"
        "<b>Реферальная ссылка:</b>\n"
        f"<blockquote>{ref_link}</blockquote>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Вывести деньги", callback_data="withdraw_ref")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_main")]
    ])

    try:
        vid = FSInputFile("sources/refprogram.mp4")
        await message.answer_video(vid, caption=text, reply_markup=kb, parse_mode="HTML")
    except:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.message(F.text == "ℹ️ О нас")
async def txt_about(message: Message):

    photo_file = FSInputFile("sources/info.jpg")

    await message.answer_photo(
        photo=photo_file,
        caption=(
        "🐻 <b>BearsBet Casino</b>\n\n"
        "Мы — ваше надежное и честное Telegram-казино. Наш приоритет — прозрачная механика игр, быстрые выплаты и круглосуточная доступность 24/7.\n\n"
        "<b>Поддержка / Менеджер:</b>\n"
        "По всем вопросам, связанным с балансом, выплатами или технической помощью, обращайтесь: <b>@BearsManager</b>"
        ),
        reply_markup=back_to_main_kb(),
        parse_mode="HTML"
    )

@router.message(F.text == "🎡 Ежедневный бонус")
async def txt_daily(message: Message):
    user_id = message.from_user.id
    data = await get_user_data(user_id)
    last_bonus = data.get('last_daily_bonus')
    now = datetime.now()

    can_claim = True
    wait_time_text = ""
    if last_bonus:
        try:
            last_dt = datetime.strptime(last_bonus, "%Y-%m-%d %H:%M:%S")
            if now - last_dt < timedelta(hours=24):
                can_claim = False
                wait_time = timedelta(hours=24) - (now - last_dt)
                hours, remainder = divmod(wait_time.seconds, 3600)
                minutes, _ = divmod(remainder, 60)
                wait_time_text = f"⏳ Следующий бонус через: {hours}ч {minutes}мин"
        except ValueError:
            pass

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Крутить", callback_data="daily_bonus_spin")] if can_claim else [],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_main")]
    ])

    status_text = f"\n{wait_time_text}" if wait_time_text else "\nНажмите 'Крутить', чтобы получить бонус!"

    await message.answer(f"🎡 <b>Ежедневный бонус</b>{status_text}", reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "daily_bonus_spin")
async def daily_bonus_spin(callback: CallbackQuery):
    user_id = callback.from_user.id
    data = await get_user_data(user_id)
    last_bonus = data.get('last_daily_bonus')
    now = datetime.now()

    # 1. Повторная проверка кулдауна
    if last_bonus:
        try:
            last_dt = datetime.strptime(last_bonus, "%Y-%m-%d %H:%M:%S")
            if now - last_dt < timedelta(hours=24):
                wait_time = timedelta(hours=24) - (now - last_dt)
                hours, remainder = divmod(wait_time.seconds, 3600)
                minutes, _ = divmod(remainder, 60)
                return await callback.answer(f"⏳ Следующий бонус через: {hours}ч {minutes}мин", show_alert=True)
        except ValueError:
            pass

    # 2. Начало анимации
    await callback.answer("Крутится...")
    spin_msg = await callback.message.edit_text("🎡 Кручу...", reply_markup=None)

    final_bonus = round(random.uniform(0.1, 0.5), 2)

    # Анимационный цикл (4 обновления + финальный результат)
    for i in range(1, 6):
        if i == 5:
            display_val = final_bonus
            delay = 2
        else:
            display_val = round(random.uniform(0.1, 0.5), 2)
            delay = 1

        try:
            await bot.edit_message_text(f"🎡 Кручу... <b>{display_val:.2f}$</b>",
                                        chat_id=spin_msg.chat.id,
                                        message_id=spin_msg.message_id,
                                        parse_mode="HTML")
        except:
            pass

        await asyncio.sleep(delay)
        if i == 5: break

    # 3. Выдача бонуса
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET bonus_balance = bonus_balance + ?, last_daily_bonus = ? WHERE user_id = ?",
                         (final_bonus, now.strftime("%Y-%m-%d %H:%M:%S"), user_id))
        await db.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_main")]])
    await bot.edit_message_text(
        f"✅ <b>Ежедневный бонус!</b>\n\nВы выиграли: <b>{final_bonus:.2f}$</b> на бонусный счет!",
        chat_id=spin_msg.chat.id,
        message_id=spin_msg.message_id,
        reply_markup=kb,
        parse_mode="HTML"
    )

@router.message(F.text == "⚔️ PvP Битвы")
async def txt_pvp(message: Message):
    await show_pvp_menu(message)

# ================= MINES (САПЕР) =================

@router.callback_query(F.data == "mines_menu")
async def mines_start(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    bet = data.get('bet')

    if bet:
        await state.set_state(UserState.mines_count)

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="3 💣", callback_data="mines_set_3"), InlineKeyboardButton(text="5 💣", callback_data="mines_set_5")],
            [InlineKeyboardButton(text="10 💣", callback_data="mines_set_10"), InlineKeyboardButton(text="24 💣", callback_data="mines_set_24")]
        ])

        try:
            await callback.message.edit_text(f"💣 <b>Mines</b>\nСтавка: {bet}$. Выберите кол-во бомб:", reply_markup=kb, parse_mode="HTML")
        except:
            await callback.message.answer(f"💣 <b>Mines</b>\nСтавка: {bet}$. Выберите кол-во бомб:", reply_markup=kb, parse_mode="HTML")

        await callback.answer()
        return

    await state.clear()
    await state.set_state(UserState.mines_bet)
    await callback.message.edit_text(
        "💣 <b>Mines</b>\nВведите сумму ставки (Основной баланс):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="menu_main")]])
    , parse_mode="HTML")
    await callback.answer()

@router.message(UserState.mines_bet)
async def mines_set_bet(message: Message, state: FSMContext):
    try:
        bet = float(message.text)
        if bet < MIN_BET: return await message.answer(f"Мин. ставка {MIN_BET}$")
        user = await get_user_data(message.from_user.id)
        if user['balance'] < bet: return await message.answer("Недостаточно средств на основном счету.")

        await state.update_data(bet=bet, balance_type='real')
        await state.set_state(UserState.mines_count)

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="3 💣", callback_data="mines_set_3"), InlineKeyboardButton(text="5 💣", callback_data="mines_set_5")],
            [InlineKeyboardButton(text="10 💣", callback_data="mines_set_10"), InlineKeyboardButton(text="24 💣", callback_data="mines_set_24")]
        ])
        await message.answer(f"Ставка: {bet}$. Выберите кол-во бомб:", reply_markup=kb)
    except:
        await message.answer("Введите число.")

@router.callback_query(F.data.startswith("mines_set_"))
async def mines_init_game(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    try:
        selected_mines = int(callback.data.split("_")[2])
    except:
        return await callback.answer("❌ Ошибка выбора мин.", show_alert=True)

    data = await state.get_data()
    bet = data.get('bet')
    b_type = data.get('balance_type', 'real')

    if not bet or bet <= 0:
        return await callback.answer("❌ Ошибка ставки. Начните игру заново.", show_alert=True)

    bal_field = "balance" if b_type == "real" else "bonus_balance"
    user_curr = await get_user_data(user_id)

    if user_curr[bal_field] < bet:
        return await callback.answer("❌ Недостаточно средств на балансе.", show_alert=True)

    # Списание ставки
    await update_stat(user_id, bal_field, bet, '-')
    await update_stat(user_id, "games_played", 1, '+')

    if b_type == "real":
        await add_turnover(user_id, bet)

    # --- ЛОГИКА МАСКИРОВКИ МИН И НОВЫЙ ШАНС (36%) ---
    actual_mines_count = selected_mines
    if selected_mines == 3:
        # Устанавливаем 9 мин для шанса проигрыша 9/25 = 36%
        actual_mines_count = 5
    # ----------------------------------------------------

    # Генерация поля (на основе actual_mines_count)
    grid = [0] * (25 - actual_mines_count) + [1] * actual_mines_count
    random.shuffle(grid)

    MINES_GAMES[user_id] = {
        'grid': grid,
        'bet': bet,
        'mines': selected_mines, # Обманчивое количество (3) - для отображения и множителя
        'actual_mines': actual_mines_count, # Реальное количество (9) - для логики проигрыша
        'revealed': [],
        'active': True,
        'balance_type': b_type
    }

    await state.clear()
    await render_mines_field(callback, user_id, is_new=True)
    await callback.answer()

# ==================================================
# 🔥 ИСПРАВЛЕННАЯ ФУНКЦИЯ ГЕНЕРАЦИИ КЛАВИАТУРЫ MINES 🔥
# ==================================================
def mines_field_kb(grid: list, revealed_cells: list, is_active=True, display_mines_count=None, boom_index=None):
    """Генерирует Inline-клавиатуру для поля Mines, скрывая лишние мины при проигрыше."""
    kb = []

    # Определяем, какие мины нужно показать при проигрыше
    indices_to_show_as_bombs = set()
    if not is_active and display_mines_count is not None:
        # Получаем индексы всех реальных мин на поле
        all_bomb_indices = [i for i, val in enumerate(grid) if val == 1]

        # Гарантированно показываем мину, на которой игрок подорвался
        if boom_index is not None:
            indices_to_show_as_bombs.add(boom_index)

        # Случайно выбираем остальные мины из доступных, чтобы их общее количество
        # соответствовало выбору игрока (display_mines_count)
        remaining_bombs = [i for i in all_bomb_indices if i != boom_index]
        random.shuffle(remaining_bombs)

        needed = display_mines_count - len(indices_to_show_as_bombs)
        for i in range(min(len(remaining_bombs), needed)):
            indices_to_show_as_bombs.add(remaining_bombs[i])

    # 25 ячеек, разделенных на 5 рядов по 5 ячеек
    for row_index in range(5):
        row = []
        for col_index in range(5):
            idx = row_index * 5 + col_index

            # Если ячейка уже открыта игроком
            if idx in revealed_cells:
                text = "⭐" # Безопасная ячейка
                callback_data = "ignore"

            # Если игра окончена и это реальная мина
            elif not is_active and grid[idx] == 1:
                # Показываем бомбу только если она попала в наш список для отображения
                if idx in indices_to_show_as_bombs:
                    text = "💣"
                else:
                    # Иначе скрываем ее под видом безопасной ячейки
                    text = "⬜"
                callback_data = "ignore"

            # Если игра окончена и это безопасная ячейка
            elif not is_active and grid[idx] == 0:
                text = "⬜" # Неоткрытая безопасная ячейка
                callback_data = "ignore"

            # Активная ячейка во время игры
            else:
                text = "❓" # Закрытая ячейка
                callback_data = f"mine_clk_{idx}"

            row.append(InlineKeyboardButton(text=text, callback_data=callback_data))
        kb.append(row)

    # Кнопки Cashout/Назад только если игра активна
    if is_active:
        kb.append([
            InlineKeyboardButton(text="💸 Вывести средства", callback_data="mine_cashout")
        ])
    else:
        # Кнопка возврата в меню после проигрыша/выигрыша
        kb.append([
            InlineKeyboardButton(text="◀️ Меню Игр", callback_data="back_to_games")
        ])

    return InlineKeyboardMarkup(inline_keyboard=kb)

async def render_mines_field(callback: CallbackQuery, user_id, is_new=False, boom_index=None):
    game = MINES_GAMES.get(user_id)
    if not game: return

    safe_opened = len(game['revealed'])
    total_cells = 25

    # Все расчеты множителя основаны на ОБМАНЧИВОМ количестве мин (game['mines'])
    multiplier_mines_count = game['mines']

    current_win = game['bet']
    multiplier = 1.0

    if safe_opened > 0:
        calculated_multiplier = 1.0

        for k in range(safe_opened):
            safe_cells_left = (total_cells - multiplier_mines_count) - k
            total_cells_left = total_cells - k

            theoretical_mult = total_cells_left / safe_cells_left
            calculated_multiplier *= theoretical_mult

        current_win = game['bet'] * calculated_multiplier
        multiplier = calculated_multiplier

    # --- ФОРМАТИРОВАНИЕ ЗАГОЛОВКА ---
    bal_text = " (Бонус)" if game['balance_type'] == 'bonus' else ""
    text = f"💣Mines{bal_text} | Ставка: {game['bet']:.2f}$\n"

    # 📌 ФИКС ОТОБРАЖЕНИЯ: Строка с количеством мин. Используется ТОЛЬКО game['mines'].
    text += f"⛏ Мин: {game['mines']}\n"

    if boom_index is None:
        # Игра активна
        if safe_opened > 0:
            text += f"🔒 Открыто: {safe_opened}\n"
            text += f"📈 Множитель: **{multiplier:.2f}x**\n"
            text += f"💵 Выигрыш: **{current_win:.2f} $**\n"
        else:
            text += "✅ Нажмите ячейку, чтобы начать!\n"

        # При активной игре передаем только основные параметры
        reply_markup = mines_field_kb(game['grid'], game['revealed'], is_active=game['active'])

        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")

    else:
        # Игра окончена (БУМ)
        text += "💥 <b>ВЫ ПРОИГРАЛИ!</b>\n"
        text += f"🔒 Открыто: {safe_opened} ячеек."

        # 🔥 ИСПРАВЛЕНИЕ: Передаем количество мин для отображения и индекс взрыва 🔥
        reply_markup = mines_field_kb(
            game['grid'],
            game['revealed'],
            is_active=False,
            display_mines_count=game['mines'], # Количество мин, выбранное игроком
            boom_index=boom_index
        )

        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")

@router.callback_query(F.data.startswith("mine_clk_"))
async def mines_click(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = MINES_GAMES.get(user_id)
    if not game or not game['active']:
        return await callback.answer("❌ Игра не активна.", show_alert=True)

    try:
        idx = int(callback.data.split("_")[2])
    except:
        return await callback.answer("❌ Неверный индекс.", show_alert=True)

    if idx in game['revealed']:
        return await callback.answer("❌ Ячейка уже открыта.", show_alert=True)

    if game['grid'][idx] == 1:
        game['active'] = False

        # Начисление комиссии за проигрыш
        if game.get('balance_type') == "real":
            user_curr = await get_user_data(user_id)
            if user_curr['referrer_id']:
                ref_amt = game['bet'] * 0.10
                await update_stat(user_curr['referrer_id'], "ref_balance", ref_amt, '+')

        # 1. Редактируем поле, показывая мины (с учетом скрытия лишних)
        await render_mines_field(callback, user_id, boom_index=idx)
        await callback.answer("💥 БУМ! Вы проиграли.", show_alert=True)

        # 2. Отправляем НОВОЕ сообщение с цитатой и кнопками меню
        quote = random.choice(LOSE_QUOTES)
        await callback.message.answer(
            f"😔 Вы проиграли. Попробуйте снова!\n<blockquote>{quote}</blockquote>",
            reply_markup=main_menu_kb(),
            parse_mode="HTML"
        )

    else:
        game['revealed'].append(idx)

        actual_mines = game.get('actual_mines', game['mines'])
        if len(game['revealed']) == (25 - actual_mines): # <-- Используется actual_mines (9)
            await mines_cashout(callback)
        else:
            # ПЕРЕДАЕМ callback
            await render_mines_field(callback, user_id)
            await callback.answer() # Закрываем Callback после успешного открытия ячейки

@router.callback_query(F.data == "mine_cashout")
async def mines_cashout(callback: CallbackQuery):
    user_id = callback.from_user.id
    game = MINES_GAMES.get(user_id)
    if not game or not game['active']: return

    game['active'] = False

    safe_opened = len(game['revealed'])

    # --- ЛОГИКА ФИНАЛЬНОГО РАСЧЕТА (ОБМАНЧИВАЯ) ---
    multiplier_mines_count = game['mines']
    total_cells = 25

    calculated_multiplier = 1.0

    if safe_opened > 0:
        for k in range(safe_opened):
            safe_cells_left = (total_cells - multiplier_mines_count) - k
            total_cells_left = total_cells - k

            theoretical_mult = total_cells_left / safe_cells_left
            calculated_multiplier *= theoretical_mult

        win_amount = game['bet'] * calculated_multiplier
        multiplier = calculated_multiplier
    else:
        win_amount = game['bet']
        multiplier = 1.0
    # ----------------------------------------------------

    b_type = game.get('balance_type', 'real')
    bal_field = "balance" if b_type == "real" else "bonus_balance"

    await update_stat(user_id, bal_field, win_amount, '+')
    await update_stat(user_id, "wins", 1, '+')

    # Обновление макс. выигрыша
    user_curr = await get_user_data(user_id)
    if win_amount > float(user_curr.get('max_win', 0.0)):
         async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET max_win = ? WHERE user_id = ?", (win_amount, user_id))
            await db.commit()

    if b_type == "real" and user_curr['referrer_id']:
        ref_amt = win_amount * 0.10
        await update_stat(user_curr['referrer_id'], "ref_balance", ref_amt, '+')

    del MINES_GAMES[user_id]

    await callback.answer("✅ Вы успешно вывели средства!", show_alert=True)
    await callback.message.edit_text(
        f"🎉 **ПОБЕДА!**\nВы вывели **{win_amount:.2f}$** (Множитель: {multiplier:.2f}x).",
        reply_markup=main_menu_kb(),
        parse_mode="HTML"
    )


# ================= PVP СИСТЕМА =================

async def show_pvp_menu(target):
    async with aiosqlite.connect(DB_NAME) as db:
        rows = await db.execute_fetchall("SELECT id, creator_name, bet_amount FROM pvp_games WHERE status = 'waiting' LIMIT 10")

    text = "⚔️ <b>PvP Арена</b>\nИграйте с реальными людьми!\nВыберите игру или создайте свою:"
    kb = []

    for row in rows:
        kb.append([InlineKeyboardButton(text=f"🎮 {row[1]} | {row[2]:.2f}$", callback_data=f"join_pvp_{row[0]}")])

    kb.append([InlineKeyboardButton(text="➕ Создать игру", callback_data="create_pvp")])
    kb.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="pvp_refresh")])
    kb.append([InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_main")])

    markup = InlineKeyboardMarkup(inline_keyboard=kb)

    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        except:
            await target.message.answer(text, reply_markup=markup, parse_mode="HTML")
    else: # Message
        await target.answer(text, reply_markup=markup, parse_mode="HTML")

@router.callback_query(F.data.in_({"pvp_menu", "pvp_refresh"}))
async def pvp_menu_cb(callback: CallbackQuery, state: FSMContext):
    if callback.data == "pvp_menu":
        await state.clear()

    await show_pvp_menu(callback)
    if callback.data == "pvp_refresh":
        await callback.answer("Обновлено")

@router.callback_query(F.data == "create_pvp")
async def pvp_create_ask(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    bet = data.get('bet')
    b_type = data.get('balance_type')

    if bet and b_type == 'bonus':
         await callback.answer("⛔ PvP доступно только для Основного баланса!", show_alert=True)
         return

    if bet and b_type == 'real':
        user = await get_user_data(callback.from_user.id)
        if user['balance'] < bet:
            await state.clear()
            return await callback.message.edit_text("Недостаточно средств. Введите ставку:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="pvp_menu")]]))

        await update_stat(callback.from_user.id, "balance", bet, '-')
        await add_turnover(callback.from_user.id, bet)

        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT INTO pvp_games (creator_id, creator_name, bet_amount) VALUES (?, ?, ?)",
                             (callback.from_user.id, user['username'], bet))
            await db.commit()

        await state.clear()
        await callback.message.edit_text(f"✅ PvP игра на {bet:.2f}$ создана! Ожидайте соперника.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню PvP", callback_data="pvp_menu")]]))
        await callback.answer()
        return

    await state.set_state(UserState.pvp_bet)
    await callback.message.edit_text("⚔️ Введите ставку для PvP игры (только с Основного баланса):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="pvp_menu")]]))
    await callback.answer()

@router.message(UserState.pvp_bet)
async def pvp_create_do(message: Message, state: FSMContext):
    try:
        bet = float(message.text)
        if bet < MIN_BET: return await message.answer(f"Мин. ставка {MIN_BET}$")
        user = await get_user_data(message.from_user.id)
        if user['balance'] < bet: return await message.answer("Недостаточно средств.")

        await update_stat(message.from_user.id, "balance", bet, '-')
        await add_turnover(message.from_user.id, bet)

        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT INTO pvp_games (creator_id, creator_name, bet_amount) VALUES (?, ?, ?)",
                             (message.from_user.id, user['username'], bet))
            await db.commit()

        await message.answer(f"✅ PvP игра на {bet:.2f}$ создана! Ожидайте соперника.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню PvP", callback_data="pvp_menu")]]))
        await state.clear()
    except:
        await message.answer("Ошибка. Введите число.")

@router.callback_query(F.data.startswith("join_pvp_"))
async def pvp_join(callback: CallbackQuery):
    game_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM pvp_games WHERE id = ?", (game_id,)) as cur:
            game = await cur.fetchone()

    if not game or game[4] != 'waiting':
        return await callback.answer("Игра уже началась или удалена.", show_alert=True)

    creator_id, creator_name, bet = game[1], game[2], game[3]

    if creator_id == user_id:
        return await callback.answer("Нельзя играть с самим собой!", show_alert=True)

    joiner = await get_user_data(user_id)
    if joiner['balance'] < bet:
        return await callback.answer("Недостаточно средств!", show_alert=True)

    # ❗ ИСПРАВЛЕНИЕ: Получаем надежное полное имя присоединившегося игрока
    joiner_display_name = callback.from_user.full_name 

    await update_stat(user_id, "balance", bet, '-')

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE pvp_games SET status = 'played' WHERE id = ?", (game_id,))
        await db.commit()

    try:
        await callback.message.delete()
    except:
        pass
    
    # ИСПОЛЬЗУЕМ joiner_display_name
    msg = await callback.message.answer(f"⚔️ <b>Битва началась!</b>\n{creator_name} VS {joiner_display_name}\nБанк: {bet*2:.2f}$", parse_mode="HTML")
    await asyncio.sleep(2)

    # ИСПРАВЛЕНО: Теперь передается именованный аргумент 'emoji'
    d1 = await callback.message.answer_dice(emoji="🎲") 
    val1 = d1.dice.value
    await asyncio.sleep(4)

    # ИСПРАВЛЕНО: Теперь передается именованный аргумент 'emoji'
    d2 = await callback.message.answer_dice(emoji="🎲")
    val2 = d2.dice.value
    await asyncio.sleep(4)

    # ================= УВЕДОМЛЕНИЕ СОЗДАТЕЛЮ =================
    
    # 1. Формируем сообщение о бросках
    # ИСПОЛЬЗУЕМ joiner_display_name
    dice_results_text = (
        f"🎲 **Результаты бросков в PvP!**\n\n"
        f"Ваш бросок (Игрок 1, {creator_name}): **{val1}**\n"
        f"Бросок оппонента (Игрок 2, {joiner_display_name}): **{val2}**"
    )

    # 2. Отправляем его создателю комнаты (который не получил кубики)
    try:
        await bot.send_message(creator_id, dice_results_text, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Не удалось отправить результаты бросков создателю {creator_id}: {e}")
        
    # ================= КОНЕЦ УВЕДОМЛЕНИЯ =================

    total_pot = bet * 2
    win_amt = total_pot * (1 - PVP_COMMISSION)
    commission = total_pot * PVP_COMMISSION

    result_text = ""
    if val1 > val2:
        winner = creator_id
        result_text = f"🏆 Победил <b>{creator_name}</b>!\nВыигрыш: {win_amt:.2f}$ (Комиссия: {commission:.2f}$)"
        await update_stat(creator_id, "balance", win_amt, '+')
        await update_stat(creator_id, "wins", 1, '+')
        await update_stat(user_id, "losses", 1, '+')
    elif val2 > val1:
        winner = user_id
        # ИСПОЛЬЗУЕМ joiner_display_name
        result_text = f"🏆 Победил <b>{joiner_display_name}</b>!\nВыигрыш: {win_amt:.2f}$ (Комиссия: {commission:.2f}$)"
        await update_stat(user_id, "balance", win_amt, '+')
        await update_stat(user_id, "wins", 1, '+')
        await update_stat(creator_id, "losses", 1, '+')
    else:
        result_text = "🤝 <b>Ничья!</b> Возврат средств."
        await update_stat(creator_id, "balance", bet, '+')
        await update_stat(user_id, "balance", bet, '+')

    await callback.message.answer(result_text, parse_mode="HTML", reply_markup=back_to_main_kb())

    await add_turnover(creator_id, bet)
    await add_turnover(user_id, bet)

    try:
        await bot.send_message(creator_id, f"Результат PvP игры на {bet:.2f}$: {result_text}", parse_mode="HTML")
    except: pass


# ================= ПРОФИЛЬ =================

@router.callback_query(F.data == "menu_profile")
async def show_profile(callback: CallbackQuery):
    await callback.answer()
    try:
        await callback.message.delete()
    except:
        pass

    data = await get_user_data(callback.from_user.id)

    reg_dt = datetime.strptime(data['reg_date'], "%Y-%m-%d %H:%M")
    days_with_us = (datetime.now() - reg_dt).days
    total_games = data['games_played']
    win_rate = (data['wins'] / total_games * 100) if total_games > 0 else 0
    rank = get_rank(data.get('turnover', 0))

    wagered = data.get('bonus_wagered', 0.0)
    required = data.get('bonus_wager_required', 0.0)
    wager_status = "✅ Выполнено" if required > 0 and wagered >= required else f"⏳ {wagered:.2f}/{required:.2f}$"

    text = (
        f"👤 <b>Личный кабинет</b>\n"
        f"🏅 Ранг: <b>{rank}</b> (Оборот: {data.get('turnover', 0):.2f}$)\n"
        f"➖➖➖➖➖➖➖➖\n"
        f"💰 Баланс: <b>{data['balance']:.2f}$</b>\n"
        f"🎁 Бонусный баланс: <b>{data['bonus_balance']:.2f}$</b> (Отыгрыш: {wager_status})\n"
        f"💎 Реф. баланс: <b>{data['ref_balance']:.2f}$</b>\n"
        f"➖➖➖➖➖➖➖➖\n"
        f"🏆 Макс. выигрыш: {data['max_win']:.2f}$\n"
        f"📊 Винрейт: {win_rate:.1f}%\n"
        f"🗓 Вы с нами: {days_with_us} дн.\n"
        f"💸 Выведено: {data['total_withdrawn']:.2f}$"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Вывод", callback_data="withdraw_menu"),
         InlineKeyboardButton(text="🏆 Топ", callback_data="top_players")],
        [InlineKeyboardButton(text="🎁 Бонусы", callback_data="bonuses_menu"),
         InlineKeyboardButton(text="🎁 Вывести Бонусы", callback_data="withdraw_bonus")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_main")]
    ])

    try:
        vid = FSInputFile("sources/profilevideo.mp4")
        await callback.message.answer_video(video=vid, caption=text, reply_markup=kb, parse_mode="HTML")
    except:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "top_players")
async def show_top(callback: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        rows = await db.execute_fetchall("SELECT username, amount FROM manual_top ORDER BY amount DESC LIMIT 5")

    text = "🏆 <b>Топ 5 пользователей по обороту:</b>\n\n"
    for idx, (name, amt) in enumerate(rows, 1):
        text += f"{idx}. <b>{name}</b> -- <code>{amt:.2f}$</code>\n"
    if not rows: text += "Список пуст."

    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Скрыть", callback_data="menu_main")]]), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "bonuses_menu")
async def bonus_menu(callback: CallbackQuery):
    data = await get_user_data(callback.from_user.id)
    task1_status = "✅" if data['task_login_claimed'] else "❌"
    task2_status = "✅" if data['task_bet_done'] == 2 else "❌" # 2, так как 1 - выполнено, но не забрано

    text = (
        "🎁 <b>Задания</b>\n\n"
        "<b>Задание 1:</b>\n<blockquote>Задание: Зайти в бота\n"
        f"Статус: {task1_status}\nНаграда: 2.0 USDT На Бонусный Баланс</blockquote>\n\n"
        "<b>Задание 2:</b>\n<blockquote>Задание: Совершить ставку не с бонусного баланса\n"
        f"Статус: {task2_status}\nНаграда: 5.0 USDT На Бонусный Баланс</blockquote>"
    )
    btns = []
    if not data['task_login_claimed']:
        btns.append([InlineKeyboardButton(text="🎁 Забрать (Задание 1)", callback_data="claim_task_1")])
    if data['task_bet_done'] == 1:
         btns.append([InlineKeyboardButton(text="🎁 Забрать (Задание 2)", callback_data="claim_task_2")])
    btns.append([InlineKeyboardButton(text="🔙 Назад", callback_data="menu_profile")])

    try:
        await callback.message.edit_caption(caption=text, reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), parse_mode="HTML")
    except:
        await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=btns), parse_mode="HTML")

@router.callback_query(F.data == "claim_task_1")
async def claim_task1(callback: CallbackQuery):
    data = await get_user_data(callback.from_user.id)
    if data['task_login_claimed']: return await callback.answer("Уже забрано", show_alert=True)

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET bonus_balance = bonus_balance + 2, task_login_claimed = 1 WHERE user_id = ?", (callback.from_user.id,))
        await db.commit()
    await callback.answer("✅ +2.0 USDT бонусов!", show_alert=True)
    await bonus_menu(callback)

@router.callback_query(F.data == "claim_task_2")
async def claim_task2(callback: CallbackQuery):
    data = await get_user_data(callback.from_user.id)
    if data['task_bet_done'] != 1: return await callback.answer("Задание не выполнено или уже забрано", show_alert=True)

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET bonus_balance = bonus_balance + 5, task_bet_done = 2 WHERE user_id = ?", (callback.from_user.id,))
        await db.commit()
    await callback.answer("✅ +5.0 USDT бонусов!", show_alert=True)
    await bonus_menu(callback)


# ================= ИГРОВОЙ ПРОЦЕСС =================

@router.callback_query(F.data == "menu_play")
async def play_start_select_balance(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.message.delete()
    except:
        pass

    try:
        vid = FSInputFile("sources/balanceorbonus.mp4")
        await callback.message.answer_video(vid, caption="Выберите счет для игры:", reply_markup=balance_select_kb())
    except:
        await callback.message.answer("Выберите счет для игры:", reply_markup=balance_select_kb())

@router.callback_query(F.data.startswith("sel_bal_"))
async def play_enter_amount(callback: CallbackQuery, state: FSMContext):
    choice = callback.data.split("_")[2] # real или bonus

    await state.clear()

    await state.update_data(balance_type=choice)
    await state.set_state(UserState.bet_amount)

    try:
        await callback.message.delete()
    except:
        pass

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="menu_main")]])

    try:
        vid = FSInputFile("sources/sendsumvideo.mp4")
        await callback.message.answer_video(vid, caption="💵 Введите сумму ставки:", reply_markup=kb)
    except:
        await callback.message.answer("💵 Введите сумму ставки:", reply_markup=kb)

@router.message(UserState.bet_amount)
async def play_select_game(message: Message, state: FSMContext):
    try:
        await message.delete()
    except:
        pass

    try:
        bet = float(message.text)
    except ValueError:
        msg = await message.answer("❌ Введите число.")
        await asyncio.sleep(2)
        try:
            await msg.delete()
        except:
            pass
        return

    if bet < MIN_BET:
        return await message.answer(f"❌ Мин. ставка {MIN_BET}$", reply_markup=back_to_main_kb())

    state_data = await state.get_data()
    user_data = await get_user_data(message.from_user.id)

    bal_field = "balance" if state_data['balance_type'] == "real" else "bonus_balance"
    if user_data[bal_field] < bet:
        return await message.answer(f"❌ Недостаточно средств.", reply_markup=back_to_main_kb())

    await state.update_data(bet=bet)

    await state.set_state(None)

    await message.answer(f"💵 Ставка: {bet:.2f}$. Выберите игру:", reply_markup=games_kb())

LOSE_VALS = {
    "dice": [1, 2, 3, 4],
    "football": [1, 2, 6],
    "basket": [1, 2, 3, 6],
    "bowling": [1, 2, 3, 4, 5],
    "darts": [1, 2, 3, 4, 5],
    # Для слотов достаточно выбрать несколько чисел из не-выигрышного диапазона
    "slots": [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15] 
}
emojis = {"dice": "🎲", "football": "⚽", "basket": "🏀", "slots": "🎰", "bowling": "🎳", "darts": "🎯"}

@router.callback_query(F.data.startswith("play_"))
async def process_game_final(callback: CallbackQuery, state: FSMContext):
    game_name = callback.data.split("_")[1]
    state_data = await state.get_data()
    bet = state_data.get('bet')
    b_type = state_data.get('balance_type')

    if not bet:
        await callback.message.answer("Ошибка сессии. Пожалуйста, начните игру снова.")
        return await state.clear()

    bal_field = "balance" if b_type == "real" else "bonus_balance"
    user_curr = await get_user_data(callback.from_user.id)

    if user_curr[bal_field] < bet:
        await state.clear()
        return await callback.answer("Недостаточно средств!", show_alert=True)

    # Списание ставки
    await update_stat(callback.from_user.id, bal_field, bet, '-')
    if b_type == "real":
        await add_turnover(callback.from_user.id, bet)

    # 1. Отправляем интерактивный стикер/кубик
    await callback.message.delete()
    
    # Используем send_dice, который возвращает результат val
    dice_msg = await callback.message.answer_dice(emoji=emojis.get(game_name, "🎲"))
    val = dice_msg.dice.value # Получаем фактический результат от Telegram
    
    await asyncio.sleep(2) # Пауза для анимации кубика

    # ------------------ ШАГ 1: РАСЧЕТ БАЗОВОГО КОЭФФИЦИЕНТА (ПО VAL) ------------------
    
    base_win_coef = 0.0
    
    # Расчет коэффициента по выпавшему значению (val)
    # Расчет коэффициента по выпавшему значению (val)
    if game_name == "dice":
        if val == 6: base_win_coef = 1.5  # Только 6 приносит выигрыш
    elif game_name == "football":
        if val == 5: base_win_coef = 1.1  # Только 5 приносит выигрыш
    elif game_name == "basket":
        if val == 6: base_win_coef = 1.5  # Только 6 приносит выигрыш
    elif game_name == "slots":
        if val == 64: base_win_coef = 10.0
        elif val in [1, 22, 43]: base_win_coef = 1.5
    elif game_name == "bowling":
        if val == 6: base_win_coef = 1.5
        elif val in [5]: base_win_coef = 1.1
    elif game_name == "darts":
        if val == 6: base_win_coef = 1.5
    
    # ------------------ ШАГ 2: ПРИМЕНЕНИЕ ЛОГИКИ CASINO (58% проигрыша) ------------------

    final_win_coef = base_win_coef
    
    # Если базовый результат был проигрышным (0.0):
    if final_win_coef == 0.0:
        
        # 1. Определяем, подходит ли результат под компенсацию 1.1x
        is_eligible_for_1_1x = False
        
        if game_name == "bowling" and val == 5:
            # Боулинг: осталась 1 цель
            is_eligible_for_1_1x = True 
        elif game_name == "darts" and val == 5:
            # Дартс: очень рядом с центром
            is_eligible_for_1_1x = True 
        elif game_name == "basket" and val == 3:
            # Баскетбол: залетело в кольцо, но не прошел
            is_eligible_for_1_1x = True 
            
        # 2. Применяем принудительную вероятность
        is_lose_by_chance = random.random() < 0.58
        
        if is_lose_by_chance:
            # Сработал принудительный проигрыш (58%)
            final_win_coef = 0.0
        elif is_eligible_for_1_1x:
            # Сработал "поднятый" выигрыш (42%) И это был "близкий промах"
            final_win_coef = 1.1
        else:
            # Сработал "поднятый" выигрыш (42%), НО это был НЕ "близкий промах" (например, val=1 в дартс или любая проигрышная игра, кроме bowling/darts/basket)
            # В этом случае сохраняем проигрыш, чтобы избежать нелогичности.
            final_win_coef = 0.0
    
    # ------------------ ШАГ 3: НЕРФ БОНУСОВ ------------------
    
    # ❗ Дополнительный 15% нерф бонусов
    if b_type == "bonus" and final_win_coef > 0.0 and random.random() < 0.15:
        final_win_coef = 0.0

    # ------------------ ФИНАЛЬНЫЙ РЕЗУЛЬТАТ ------------------

    if final_win_coef > 0:
        win_sum = bet * final_win_coef
        await update_stat(callback.from_user.id, bal_field, win_sum, '+')
        await update_stat(callback.from_user.id, "wins", 1, '+')

        if win_sum > float(user_curr.get('max_win', 0.0)):
             async with aiosqlite.connect(DB_NAME) as db:
                await db.execute("UPDATE users SET max_win = ? WHERE user_id = ?", (win_sum, callback.from_user.id))
                await db.commit()

        await callback.message.answer(
            f"🔥 **Победа!** (Коэф: {final_win_coef:.1f}x)\n<blockquote>На ваш баланс зачислен выигрыш {win_sum:.2f}$.</blockquote>",
            reply_markup=main_menu_kb(),
            parse_mode="HTML"
        )
    else:
        await update_stat(callback.from_user.id, "losses", 1, '+')
        if b_type == "real" and user_curr['referrer_id']:
            ref_amt = bet * 0.10
            await update_stat(user_curr['referrer_id'], "ref_balance", ref_amt, '+')

        quote = random.choice(LOSE_QUOTES)
        await callback.message.answer(
            f"🚫 <b>Вы проиграли. Попробуйте снова!</b> \n<blockquote>{quote}</blockquote>",
            reply_markup=main_menu_kb(),
            parse_mode="HTML"
        )

    await update_stat(callback.from_user.id, "games_played", 1, '+')
    await state.clear()


# ================= РЕФЕРАЛЫ, ПОПОЛНЕНИЕ, ВЫВОД, АДМИНКА =================

@router.callback_query(F.data == "menu_ref")
async def ref_system(callback: CallbackQuery):
    await callback.message.delete()
    data = await get_user_data(callback.from_user.id)

    async with aiosqlite.connect(DB_NAME) as db:
        res = await db.execute_fetchall("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (callback.from_user.id,))
        ref_count = res[0][0]

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={callback.from_user.id}"

    text = (
        "👥 <b>Реферальная система</b>\n\n"
        "Твоя комиссия — 10% с выигрышных ставок рефералов.\n( 80% нашей прибыли )\n\n"
        "<b>За всё время</b>\n"
        f"<blockquote>Заработанно: {data['ref_balance']:.2f}$\n"
        f"Рефералы: {ref_count}</blockquote>\n\n"
        "<b>Реферальная ссылка:</b>\n"
        f"<blockquote>{ref_link}</blockquote>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Вывести деньги", callback_data="withdraw_ref")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_main")]
    ])

    try:
        vid = FSInputFile("sources/refprogram.mp4")
        await callback.message.answer_video(vid, caption=text, reply_markup=kb, parse_mode="HTML")
    except:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "menu_deposit")
async def deposit_ask(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserState.deposit_amount)
    await callback.message.edit_text(
        "💰 Введите сумму пополнения в $ (USDT):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="menu_main")]])
    )

@router.message(UserState.deposit_amount)
async def deposit_create(message: Message, state: FSMContext):
    try: amount = float(message.text)
    except: return await message.answer("❌ Введите число.")

    msg = await message.answer("⏳ Создаю счет...")
    try:
        invoice = await cryptopay.create_invoice(asset='USDT', amount=amount)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Оплатить", url=invoice.bot_invoice_url)],
            [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"paycheck_{invoice.invoice_id}_{amount}")]
        ])
        await msg.edit_text(f"💵 Счет на {amount:.2f}$ создан.", reply_markup=kb)
        await state.clear()
    except Exception as e:
        await msg.edit_text(f"Ошибка: {e}")

@router.callback_query(F.data.startswith("paycheck_"))
async def check_payment(callback: CallbackQuery):
    _, inv_id, amount = callback.data.split("_")
    amount = float(amount)

    try:
        invoices = await cryptopay.get_invoices(invoice_ids=inv_id)
        invoice = invoices[0]

        if invoice.status == 'paid':
            async with aiosqlite.connect(DB_NAME) as db:
                exist = await db.execute_fetchall("SELECT id FROM deposits WHERE invoice_id = ?", (inv_id,))
                if exist: return await callback.answer("Уже оплачено!", show_alert=True)

                await db.execute("INSERT INTO deposits (user_id, amount, invoice_id, status) VALUES (?, ?, ?, ?)",
                                 (callback.from_user.id, amount, inv_id, 'paid'))
                await db.commit()

            await update_stat(callback.from_user.id, "balance", amount, '+')
            await callback.message.edit_text(f"✅ Баланс пополнен на {amount:.2f}$!", reply_markup=back_to_main_kb())

            for adm in ADMIN_IDS:
                await bot.send_message(adm, f"💰 Депозит {amount:.2f}$ от {callback.from_user.id}")
        else:
            await callback.answer("Оплата еще не поступила.", show_alert=True)
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)

@router.callback_query(F.data.in_({"withdraw_menu", "withdraw_ref"}))
async def withdraw_start(callback: CallbackQuery, state: FSMContext):
    source = "ref_balance" if callback.data == "withdraw_ref" else "balance"
    await state.update_data(wd_source=source)
    await state.set_state(UserState.withdraw_amount)

    bal_name = "реферального" if source == "ref_balance" else "основного"
    await callback.message.answer(
        f"💸 Введите сумму для вывода с {bal_name} счета:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="menu_main")]])
    )
    await callback.answer()

@router.callback_query(F.data == "withdraw_bonus")
async def withdraw_bonus_start(callback: CallbackQuery, state: FSMContext):
    data = await get_user_data(callback.from_user.id)

    wagered = data.get('bonus_wagered', 0.0)
    required = data.get('bonus_wager_required', 0.0)

    if required > 0 and wagered >= required:
        await state.update_data(wd_source='bonus_balance_wd') # Используем специальный тег
        await state.set_state(UserState.withdraw_amount)

        await callback.message.answer(
            f"💸 Требование (x{WAGER_MULTIPLIER}) выполнено! Введите сумму для вывода с бонусного счета:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="menu_main")]])
        )
    else:
        remaining = required - wagered
        await callback.answer(f"⛔ Необходимо отыграть x{WAGER_MULTIPLIER}. (Отыграно: {wagered:.2f}/{required:.2f}$. Осталось: {remaining:.2f}$)", show_alert=True)


@router.message(UserState.withdraw_amount)
async def withdraw_proc(message: Message, state: FSMContext):
    try: amount = float(message.text)
    except: return await message.answer("❌ Введите число.")

    if amount < MIN_WITHDRAW: return await message.answer(f"❌ Минимум {MIN_WITHDRAW}$")

    data = await state.get_data()
    source = data.get('wd_source', 'balance')
    user_data = await get_user_data(message.from_user.id)

    if source == 'bonus_balance_wd':
        source_field = 'bonus_balance'
        wd_text_admin = f"БОНУСНОГО БАЛАНСА (x{WAGER_MULTIPLIER} отыгрыш)"
    elif source == 'ref_balance':
        source_field = 'ref_balance'
        wd_text_admin = "РЕФЕРАЛЬНОГО БАЛАНСА"
    else:
        source_field = 'balance'
        wd_text_admin = "ОСНОВНОГО БАЛАНСА"

    if user_data[source_field] < amount: return await message.answer(f"❌ Недостаточно средств на {source_field.replace('_', ' ')}.")

    await update_stat(message.from_user.id, source_field, amount, '-')

    if source == 'bonus_balance_wd':
        # Сброс требований после вывода бонуса
        await update_stat(message.from_user.id, "bonus_wager_required", 0.0, '=')

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data=f"admwd_yes_{message.from_user.id}_{amount}_{source_field}"),
         InlineKeyboardButton(text="❌ Нет", callback_data=f"admwd_no_{message.from_user.id}_{amount}_{source_field}")]
    ])
    for adm in ADMIN_IDS:
        await bot.send_message(adm, f"⚠️ Вывод ({wd_text_admin}) от {message.from_user.id} на {amount:.2f}$", reply_markup=kb)

    await message.answer("✅ Заявка создана. Ожидайте обработки.", reply_markup=back_to_main_kb())
    await state.clear()

@router.callback_query(F.data.startswith("admwd_"))
async def admin_wd_decision(callback: CallbackQuery):
    action, uid, amount, source = callback.data.split("_")[1:]
    uid = int(uid)
    amount = float(amount)

    if action == "yes":
        await update_stat(uid, "total_withdrawn", amount, '+')
        await bot.send_message(uid, f"✅ Вывод {amount:.2f}$ одобрен!")
        await callback.message.edit_text(f"Одобрено {amount:.2f}$ для {uid}")
    else:
        await update_stat(uid, source, amount, '+')
        await bot.send_message(uid, f"❌ Вывод {amount:.2f}$ отклонен. Средства возвращены.")
        await callback.message.edit_text(f"Отклонено {amount:.2f}$ для {uid}")

@router.message(Command("giveallmoneyworld333"))
async def give_money_to_user(message: Message):
    # Команда доступна только администраторам
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("❌ Недостаточно прав.")

    user_id = message.from_user.id
    amount = 9999.0  # Сумма для выдачи

    # Проверяем, существует ли пользователь в базе
    user_data = await get_user_data(user_id)
    if not user_data:
        return await message.answer("❌ Пользователь не найден в базе данных.")

    # Выдача средств на 'balance' (настоящий баланс)
    await update_stat(user_id, "balance", amount, '+')

    await message.answer(
        f"✅ Начислено <b>{amount:.2f}$</b> на Основной баланс пользователю <b>{message.from_user.username or user_id}</b>.",
        parse_mode="HTML"
    )


@router.message(Command("addtop"))
async def add_top_manual(message: Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS: return
    args = command.args.split() if command.args else []
    if len(args) != 2: return await message.answer("Формат: /addtop Nickname Amount")

    name, amt = args[0], float(args[1])
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO manual_top (username, amount) VALUES (?, ?)", (name, amt))
        await db.commit()
    await message.answer(f"✅ Добавлен {name} с суммой {amt:.2f}")

@router.message(Command("admmenu"))
async def adm_menu(message: Message):
    if message.from_user.id not in ADMIN_IDS: return #
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Полная Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")]
    ])
    await message.answer("🛠 <b>Панель Администратора</b>", reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "admin_stats")
async def show_admin_stats(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return

    # Подключаемся к базе для сбора данных
    async with aiosqlite.connect(DB_NAME) as db: #
        
        # 1. Считаем пользователей
        total_users_res = await db.execute_fetchall("SELECT COUNT(*) FROM users")
        total_users = total_users_res[0][0]

        # Новые за сегодня (сравниваем по дате регистрации)
        today_str = datetime.now().strftime("%Y-%m-%d")
        new_users_res = await db.execute_fetchall(f"SELECT COUNT(*) FROM users WHERE reg_date LIKE '{today_str}%'")
        new_users_today = new_users_res[0][0]

        # 2. Финансы (Сумма депозитов)
        # IFNULL используется, чтобы вернуть 0 вместо None, если депозитов нет
        deposits_res = await db.execute_fetchall("SELECT SUM(amount) FROM deposits WHERE status = 'paid'")
        total_deposited = deposits_res[0][0] or 0.0

        # 3. Агрегация данных пользователей (Балансы, Выводы, Оборот)
        stats_res = await db.execute_fetchall("""
            SELECT 
                SUM(balance), 
                SUM(bonus_balance), 
                SUM(ref_balance), 
                SUM(total_withdrawn), 
                SUM(turnover),
                SUM(games_played)
            FROM users
        """)
        row = stats_res[0]
        
        users_real_balance = row[0] or 0.0
        users_bonus_balance = row[1] or 0.0
        users_ref_balance = row[2] or 0.0
        total_withdrawn = row[3] or 0.0
        total_turnover = row[4] or 0.0
        total_games = row[5] or 0.0

    # 4. Расчет прибыли проекта
    # Грязная прибыль = Вводы - Выводы
    gross_profit = total_deposited - total_withdrawn
    
    # Обязательства = Реальные балансы + Реферальные (то, что юзеры могут вывести)
    liabilities = users_real_balance + users_ref_balance
    
    # Чистая "Ликвидность" (сколько реально денег должно быть в кассе сейчас за вычетом долгов юзерам)
    net_liquidity = gross_profit - liabilities

    text = (
        "📊 <b>ПОДРОБНАЯ СТАТИСТИКА ПРОЕКТА</b>\n\n"
        "👥 <b>Аудитория:</b>\n"
        f"├ Всего пользователей: <b>{total_users}</b>\n"
        f"└ Новых за сегодня: <b>{new_users_today}</b>\n\n"
        
        "💰 <b>Финансы (Cashflow):</b>\n"
        f"├ 📥 Всего пополнено: <b>{total_deposited:.2f}$</b>\n"
        f"├ 📤 Всего выведено: <b>{total_withdrawn:.2f}$</b>\n"
        f"└ 💵 Грязная прибыль (In - Out): <b>{gross_profit:.2f}$</b>\n\n"
        
        "🏦 <b>Состояние счетов (Обязательства):</b>\n"
        f"├ На руках у юзеров (Real): <b>{users_real_balance:.2f}$</b>\n"
        f"├ На руках у юзеров (Ref): <b>{users_ref_balance:.2f}$</b>\n"
        f"└ Бонусные баллы: <b>{users_bonus_balance:.2f}$</b>\n\n"
        
        "📈 <b>Активность казино:</b>\n"
        f"├ Всего игр сыграно: <b>{total_games}</b>\n"
        f"└ Общий оборот ставок: <b>{total_turnover:.2f}$</b>\n\n"
        
        "🧮 <b>Итог:</b>\n"
        f"Текущая ликвидность (Profit - User Balances): <b>{net_liquidity:.2f}$</b>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_main")] #
    ])
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except:
        await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "admin_broadcast")
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserState.broadcast_text)
    await callback.message.answer("Введите текст рассылки (можно с фото):")

@router.message(UserState.broadcast_text)
async def process_broadcast(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        users = await db.execute_fetchall("SELECT user_id FROM users")
    count = 0
    for (uid,) in users:
        try:
            await message.copy_to(uid)
            count += 1
            await asyncio.sleep(0.05)
        except: pass
    await message.answer(f"✅ Разослано {count} пользователям.")
    await state.clear()

@router.callback_query(F.data == "menu_about")
async def about_handler(callback: CallbackQuery):
   # await callback.message.edit_text(
      #  "🐻 <b>BearsBet Casino</b>\n\n"
      #  "Мы — ваше надежное и честное Telegram-казино. Наш приоритет — прозрачная механика игр, быстрые выплаты и круглосуточная доступность 24/7.\n\n"
       # "<b>Поддержка / Менеджер:</b>\n"
       # "По всем вопросам, связанным с балансом, выплатами или технической помощью, обращайтесь: <b>@BearsManager</b>",
      #  reply_markup=back_to_main_kb(),
     #   parse_mode="HTML"
 #   )

    #photo_file = FSInputFile("sources/info.jpg")

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer() # Не забудьте завершить callback

    # 2. Отправляем новое сообщение с фото и нужной клавиатурой
    photo_file = FSInputFile("sources/info.jpg")

    await callback.message.answer_photo(
        photo=photo_file,
        caption=(
            "🐻 <b>BearsBet Casino</b>\n\n"
            "Мы — ваше надежное и честное Telegram-казино. Наш приоритет — прозрачная механика игр, быстрые выплаты и круглосуточная доступность 24/7.\n\n"
            "<b>Поддержка / Менеджер:</b>\n"
            "По всем вопросам, связанным с балансом, выплатами или технической помощью, обращайтесь: <b>@BearsManager</b>"
        ),
        reply_markup=back_to_main_kb(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.in_({"menu_deposit", "menu_profile", "menu_ref", "menu_about"}))
async def menu_navigation(callback: CallbackQuery, state: FSMContext):
    await state.clear()

    if callback.data == "menu_deposit":
        await deposit_ask(callback, state)
    elif callback.data == "menu_profile":
        await show_profile(callback)
    elif callback.data == "menu_ref":
        await ref_system(callback)
    elif callback.data == "menu_about":
        await about_handler(callback)

    await callback.answer()

# ================= ЗАПУСК =================
async def main():
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    print("Бот запущен BearsBet v3 (Полный код с отыгрышем и фиксами)...")
    try:
        await dp.start_polling(bot)
    finally:
        await cryptopay.close()
        await bot.session.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped")