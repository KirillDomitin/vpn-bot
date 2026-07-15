import asyncio
import logging

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import config
import database
import amnezia

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()


class AdminStates(StatesGroup):
    waiting_add_name = State()
    waiting_remove_name = State()
    waiting_reset_name = State()


def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Добавить пользователя", callback_data="admin_add")],
        [InlineKeyboardButton(text="Список пользователей", callback_data="admin_list")],
        [InlineKeyboardButton(text="Сбросить ключ", callback_data="admin_reset")],
        [InlineKeyboardButton(text="Удалить пользователя", callback_data="admin_remove")],
        [InlineKeyboardButton(text="Получить мой ключ", callback_data="admin_mykey")],
    ])


def back_button():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="admin_back")],
    ])


# --- /start ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id

    if user_id == config.ADMIN_ID:
        await message.answer("Панель администратора", reply_markup=admin_keyboard())
        return

    user = None
    username = message.from_user.username
    if username:
        user = await database.get_user_by_name(username.lower())
    if not user:
        user = await database.get_user_by_name(str(user_id))
    if not user:
        await message.answer("Вы не в списке. Обратитесь к администратору.")
        return

    if user["config_issued"]:
        await message.answer("Ваш VPN-ключ уже выдан. Обратитесь к администратору для перевыпуска.")
        return

    await database.bind_telegram_id(user["name"], user_id)
    await issue_key(message, user)


async def issue_key(message: Message, user: dict):
    await message.answer("Генерирую VPN-ключ, подождите...")
    try:
        vpn_url, client_ip, private_key, public_key = await amnezia.create_client(user["name"])
        await database.mark_issued(user["name"], client_ip, private_key, public_key)
        await message.answer("Ваш VPN-ключ готов! Скопируйте и вставьте в приложение AmneziaVPN:")
        await message.answer(vpn_url)
        logger.info("Key issued to %s (tg:%s)", user["name"], message.from_user.id)
    except Exception as e:
        logger.exception("Failed to create client for %s", user["name"])
        await message.answer("Ошибка при создании ключа: {}".format(e))


# --- Admin callbacks ---

@router.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Панель администратора", reply_markup=admin_keyboard())


@router.callback_query(F.data == "admin_add")
async def admin_add(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != config.ADMIN_ID:
        return
    await callback.message.edit_text("Введите @username или числовой ID пользователя:", reply_markup=back_button())
    await state.set_state(AdminStates.waiting_add_name)


@router.message(AdminStates.waiting_add_name)
async def admin_add_name(message: Message, state: FSMContext):
    if message.from_user.id != config.ADMIN_ID:
        return
    raw = message.text.strip()
    if raw.startswith("@"):
        identifier = raw.lstrip("@").lower()
        display = "@{}".format(identifier)
    elif raw.isdigit():
        identifier = raw
        display = "ID {}".format(identifier)
    else:
        identifier = raw.lower()
        display = "@{}".format(identifier)
    if not identifier:
        await message.answer("Некорректный ввод.", reply_markup=admin_keyboard())
        await state.clear()
        return
    ok = await database.add_user(identifier)
    if ok:
        await message.answer(
            "Пользователь {} добавлен.\n\n"
            "Теперь он может нажать /start и получить ключ.".format(display),
            reply_markup=admin_keyboard(),
        )
    else:
        await message.answer(
            "Пользователь {} уже существует.".format(display),
            reply_markup=admin_keyboard(),
        )
    await state.clear()


@router.callback_query(F.data == "admin_list")
async def admin_list(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        return
    users = await database.list_users()
    if not users:
        await callback.message.edit_text("Список пуст.", reply_markup=admin_keyboard())
        return

    lines = []
    for u in users:
        status = "выдан" if u["config_issued"] else "ожидает"
        ip_info = " ({})".format(u["client_ip"]) if u["client_ip"] else ""
        label = "ID {}".format(u["name"]) if u["name"].isdigit() else "@{}".format(u["name"])
        lines.append("  {} — {}{}".format(label, status, ip_info))

    await callback.message.edit_text(
        "Пользователи:\n\n" + "\n".join(lines),
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data == "admin_reset")
async def admin_reset(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != config.ADMIN_ID:
        return
    users = await database.list_users()
    issued = [u for u in users if u["config_issued"]]
    if not issued:
        await callback.message.edit_text("Нет выданных ключей.", reply_markup=admin_keyboard())
        return

    buttons = []
    for u in issued:
        buttons.append([InlineKeyboardButton(
            text="{} ({})".format(u["name"], u["client_ip"] or ""),
            callback_data="reset_{}".format(u["name"]),
        )])
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="admin_back")])
    await callback.message.edit_text(
        "Выберите пользователя для сброса ключа:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("reset_"))
async def admin_do_reset(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        return
    name = callback.data[6:]
    user = await database.get_user_by_name(name)
    if not user:
        await callback.message.edit_text("Пользователь не найден.", reply_markup=admin_keyboard())
        return

    if user["public_key"]:
        try:
            await amnezia.remove_peer(user["public_key"])
        except Exception as e:
            logger.exception("Failed to remove peer for %s", name)
            await callback.message.edit_text(
                "Ошибка при удалении пира: {}".format(e),
                reply_markup=admin_keyboard(),
            )
            return

    await database.reset_user_key(name)
    await callback.message.edit_text(
        "Ключ \"{}\" сброшен. При следующем входе получит новый.".format(name),
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data == "admin_remove")
async def admin_remove(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != config.ADMIN_ID:
        return
    users = await database.list_users()
    if not users:
        await callback.message.edit_text("Список пуст.", reply_markup=admin_keyboard())
        return

    buttons = []
    for u in users:
        status = "выдан" if u["config_issued"] else "ожидает"
        buttons.append([InlineKeyboardButton(
            text="{} — {}".format(u["name"], status),
            callback_data="del_{}".format(u["name"]),
        )])
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="admin_back")])
    await callback.message.edit_text(
        "Выберите пользователя для удаления:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("del_"))
async def admin_do_remove(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        return
    name = callback.data[4:]
    user = await database.remove_user_by_name(name)
    if not user:
        await callback.message.edit_text("Пользователь не найден.", reply_markup=admin_keyboard())
        return

    if user["public_key"]:
        try:
            await amnezia.remove_peer(user["public_key"])
        except Exception as e:
            logger.exception("Failed to remove peer for %s", name)

    await callback.message.edit_text(
        "Пользователь \"{}\" удалён.".format(name),
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data == "admin_mykey")
async def admin_mykey(callback: CallbackQuery):
    if callback.from_user.id != config.ADMIN_ID:
        return
    username = callback.from_user.username
    if not username:
        await callback.message.edit_text(
            "У вас не установлен username в Telegram.",
            reply_markup=admin_keyboard(),
        )
        return
    username = username.lower()
    user = await database.get_user_by_name(username)
    if not user:
        await database.add_user(username)
        await database.bind_telegram_id(username, config.ADMIN_ID)
        user = await database.get_user_by_name(username)

    if user["config_issued"]:
        await callback.message.edit_text(
            "Ваш ключ уже выдан. Сбросьте через \"Сбросить ключ\" для перевыпуска.",
            reply_markup=admin_keyboard(),
        )
        return

    await database.bind_telegram_id(username, config.ADMIN_ID)
    await callback.message.edit_text("Генерирую ключ...")
    try:
        vpn_url, client_ip, private_key, public_key = await amnezia.create_client(username)
        await database.mark_issued(username, client_ip, private_key, public_key)
        await callback.message.answer("Ваш VPN-ключ готов!")
        await callback.message.answer(vpn_url)
        await callback.message.answer("Панель администратора", reply_markup=admin_keyboard())
    except Exception as e:
        logger.exception("Failed to create admin key")
        await callback.message.edit_text(
            "Ошибка: {}".format(e),
            reply_markup=admin_keyboard(),
        )


async def main():
    await database.init_db()
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    logger.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
