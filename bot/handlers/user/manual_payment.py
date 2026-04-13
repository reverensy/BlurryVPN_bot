# --- НОВЫЙ ФАЙЛ: bot/handlers/user/payment_manual.py ---
import logging
from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.states.user_states import UserStates # <-- Нужно будет создать этот файл/класс
from bot.utils.text import escape_html, safe_edit_or_send
from config import ADMIN_IDS
from database.requests import (
    get_all_tariffs, get_tariff_by_id, get_setting,
    create_manual_payment_request # <-- Нужно будет создать эту функцию
)
from bot.keyboards.user import tariff_select_kb
from bot.keyboards.admin import home_only_kb

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data.startswith('manual_tariffs'))
async def manual_tariffs_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор тарифа для ручной оплаты."""
    await state.clear()
    tariffs = get_all_tariffs(include_hidden=False)
    
    await safe_edit_or_send(
        callback.message, 
        '✍️ <b>Ручная оплата</b>\n\nВыберите тариф:', 
        reply_markup=tariff_select_kb(tariffs, payment_method='manual_pay') # Используем кастомный префикс
    )
    await callback.answer()

@router.callback_query(F.data.startswith('manual_pay:'))
async def manual_pay_handler(callback: CallbackQuery, state: FSMContext):
    """Показ реквизитов и ожидание скриншота."""
    tariff_id = int(callback.data.split(':')[1])
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return
        
    price = tariff['price_rub'] # Предполагаем, что цена в рублях
    requisites = get_setting('manual_payment_requisites', 'Реквизиты не настроены.')

    await state.set_state(UserStates.awaiting_screenshot)
    await state.update_data(tariff_id=tariff_id, amount=price)

    text = (
        f"✍️ <b>Ручная оплата</b>\n\n"
        f"<b>Тариф:</b> {escape_html(tariff['name'])}\n"
        f"<b>Сумма к оплате:</b> {price} ₽\n\n"
        f"<b>Пожалуйста, выполните перевод по следующим реквизитам:</b>\n\n"
        f"{requisites}\n\n"
        f"После успешной оплаты, <b>отправьте скриншот (чек) в этот чат</b>. "
        f"Ваш платеж будет проверен администратором в ручном режиме."
    )
    
    await safe_edit_or_send(callback.message, text, reply_markup=home_only_kb())
    await callback.answer()


@router.message(UserStates.awaiting_screenshot, F.photo)
async def process_screenshot(message: Message, state: FSMContext, bot: Bot):
    """Обработка получения скриншота."""
    data = await state.get_data()
    tariff_id = data.get('tariff_id')
    amount = data.get('amount')
    
    screenshot_file_id = message.photo[-1].file_id
    user = message.from_user
    
    # Сохраняем заявку в БД
    payment_id = create_manual_payment_request(
        user_id=user.id,
        username=user.username,
        tariff_id=tariff_id,
        amount=amount,
        screenshot_file_id=screenshot_file_id
    )

    await state.clear()
    await message.answer(
        "✅ <b>Спасибо! Ваша заявка принята.</b>\n\n"
        "Администратор скоро проверит ваш платеж. Вы получите уведомление о результате.",
        reply_markup=home_only_kb(), parse_mode='HTML'
    )
    
    # Уведомление админов
    admin_text = (
        f"⚠️ <b>Новая заявка на ручную оплату!</b>\n\n"
        f"От: {user.full_name} (@{user.username})\n"
        f"ID пользователя: <code>{user.id}</code>\n"
        f"Заявка №: <code>{payment_id}</code>\n\n"
        f"Для обработки перейдите в Админ-панель."
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text)
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")