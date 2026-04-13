# bot/handlers/user/manual_payment.py (бывший demo.py)

import logging
from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

# НОВЫЕ ИМПОРТЫ
from bot.states.user_states import UserStates # Нужно будет создать
from config import ADMIN_IDS
from database.requests import (
    get_all_tariffs, get_tariff_by_id, get_key_details_for_user, get_setting,
    create_manual_payment_request, get_pending_manual_payments_by_user # Нужно будет создать
)
from bot.utils.text import escape_html, safe_edit_or_send
from bot.keyboards.user import tariff_select_kb, renew_tariff_select_kb
from bot.keyboards.admin import home_only_kb

logger = logging.getLogger(__name__)
router = Router()

@router.callback_query(F.data.startswith('manual_tariffs'))
async def manual_tariffs_handler(callback: CallbackQuery, state: FSMContext):
    """Выбор тарифа для ручной оплаты."""
    await state.clear()
    order_id = None
    if ':' in callback.data:
        order_id = callback.data.split(':')[1]

    tariffs = get_all_tariffs(include_hidden=False)
    
    await safe_edit_or_send(
        callback.message, 
        '✍️ <b>Ручная оплата</b>\n\nВыберите тариф, который хотите оплатить:', 
        # ↓↓↓ ИСПРАВЛЕННЫЙ ВЫЗОВ ↓↓↓
        reply_markup=tariff_select_kb(tariffs, order_id=order_id, is_manual=True) 
    )
    await callback.answer()

# Переделываем renew_demo_tariffs_handler
@router.callback_query(F.data.startswith('renew_manual_tariffs:'))
async def renew_manual_tariffs_handler(callback: CallbackQuery):
    """Выбор тарифа для ручной оплаты (Продление)."""
    key_id = int(callback.data.split(':')[1])
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not key:
        await callback.answer('❌ Ключ не найден', show_alert=True)
        return
        
    from bot.utils.groups import get_tariffs_for_renewal
    tariffs = get_tariffs_for_renewal(key.get('tariff_id', 0))
    if not tariffs:
        await callback.answer('Нет доступных тарифов для продления', show_alert=True)
        return
        
    await safe_edit_or_send(
        callback.message, 
        f"✍️ <b>Ручная оплата (продление)</b>\n\n🔑 Ключ: <b>{escape_html(key['display_name'])}</b>\n\nВыберите тариф для продления:", 
        reply_markup=renew_tariff_select_kb(tariffs, key_id, payment_method='renew_manual_pay') # Новый префикс
    )
    await callback.answer()

# Переделываем demo_pay_handler
@router.callback_query(F.data.startswith('manual_pay:'))
async def manual_pay_handler(callback: CallbackQuery, state: FSMContext):
    """Показ реквизитов и ожидание скриншота (Новый ключ)."""
    tariff_id = int(callback.data.split(':')[1])
    tariff = get_tariff_by_id(tariff_id)
    if not tariff:
        await callback.answer('❌ Тариф не найден', show_alert=True)
        return

    price_rub = float(tariff.get('price_rub') or 0)
    requisites = get_setting('manual_payment_requisites', 'Реквизиты не настроены.')
    
    await state.set_state(UserStates.awaiting_screenshot)
    await state.update_data(tariff_id=tariff_id, amount=price_rub, key_id_to_renew=None)

    text = (
        f"✍️ <b>Ручная оплата</b>\n\n"
        f"<b>Тариф:</b> {escape_html(tariff['name'])}\n"
        f"<b>Сумма к оплате:</b> {int(price_rub)} ₽\n\n"
        f"<b>Пожалуйста, выполните перевод по следующим реквизитам:</b>\n\n"
        f"<blockquote>{requisites}</blockquote>\n\n"
        f"После успешной оплаты, <b>отправьте скриншот (чек) в этот чат</b>. "
        f"Платеж будет проверен администратором."
    )
    
    await safe_edit_or_send(callback.message, text, reply_markup=home_only_kb())
    await callback.answer()

# Переделываем renew_demo_pay_handler
@router.callback_query(F.data.startswith('renew_manual_pay:'))
async def renew_manual_pay_handler(callback: CallbackQuery, state: FSMContext):
    """Показ реквизитов и ожидание скриншота (Продление)."""
    parts = callback.data.split(':')
    key_id = int(parts[1])
    tariff_id = int(parts[2])
    
    tariff = get_tariff_by_id(tariff_id)
    key = get_key_details_for_user(key_id, callback.from_user.id)
    if not tariff or not key:
        await callback.answer('❌ Ошибка тарифа или ключа', show_alert=True)
        return

    price_rub = float(tariff.get('price_rub') or 0)
    requisites = get_setting('manual_payment_requisites', 'Реквизиты не настроены.')
    
    await state.set_state(UserStates.awaiting_screenshot)
    await state.update_data(tariff_id=tariff_id, amount=price_rub, key_id_to_renew=key_id)

    text = (
        f"✍️ <b>Ручная оплата (продление)</b>\n\n"
        f"🔑 <b>Ключ для продления:</b> {escape_html(key['display_name'])}\n"
        f"📦 <b>Продление на:</b> {escape_html(tariff['name'])}\n"
        f"<b>Сумма к оплате:</b> {int(price_rub)} ₽\n\n"
        f"<b>Пожалуйста, выполните перевод по следующим реквизитам:</b>\n\n"
        f"<blockquote>{requisites}</blockquote>\n\n"
        f"После успешной оплаты, <b>отправьте скриншот (чек) в этот чат</b>."
    )
    
    await safe_edit_or_send(callback.message, text, reply_markup=home_only_kb())
    await callback.answer()


# НОВЫЙ ОБРАБОТЧИК ДЛЯ ПОЛУЧЕНИЯ СКРИНШОТА
@router.message(UserStates.awaiting_screenshot, F.photo)
@router.message(UserStates.awaiting_screenshot, F.photo)
async def process_screenshot(message: Message, state: FSMContext, bot: Bot):
    """Обработка получения скриншота."""
    user = message.from_user

    # Шаг 1: Проверяем на спам СРАЗУ
    existing_pending = get_pending_manual_payments_by_user(user.id)
    if existing_pending:
        await message.answer(
            "⏳ <b>Ваша предыдущая заявка еще на рассмотрении.</b>\n\n"
            "Пожалуйста, дождитесь ее обработки, прежде чем создавать новую.",
            parse_mode="HTML"
        )
        return  # Выходим из функции, ничего больше не делаем

    # Шаг 2: Только если все чисто, достаем данные и создаем заявку
    data = await state.get_data()
    tariff_id = data.get('tariff_id')
    amount = data.get('amount')
    key_id_to_renew = data.get('key_id_to_renew')
    
    screenshot_file_id = message.photo[-1].file_id
    
    payment_id = create_manual_payment_request(
        user_telegram_id=user.id,
        username=user.username,
        tariff_id=tariff_id,
        amount=amount,
        screenshot_file_id=screenshot_file_id,
        key_id_to_renew=key_id_to_renew
    )

    await state.clear()
    
    # Шаг 3: Отправляем ответы
    await message.answer(
        "✅ <b>Спасибо! Ваша заявка принята.</b>\n\n"
        "Администратор скоро проверит ваш платеж. Вы получите уведомление о результате.",
        reply_markup=home_only_kb(), 
        parse_mode='HTML'
    )
    
    user_mention = f"@{user.username}" if user.username else f"ID: {user.id}"
    admin_text = (
        f"⚠️ <b>Новая заявка на ручную оплату №{payment_id}!</b>\n\n"
        f"От: {user.full_name} ({user_mention})\n"
        f"Сумма: {amount} ₽\n\n"
        f"Для обработки перейдите в Админ-панель ➝ Платежи вручную."
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text, parse_mode='HTML')
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")