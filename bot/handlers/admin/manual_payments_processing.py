import logging
from aiogram import Router, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.utils.admin import is_admin
from bot.utils.text import safe_edit_or_send, escape_html
from database.requests import (
    get_pending_manual_payments, get_manual_payment_by_id, update_manual_payment_status,
    get_tariff_by_id # Убедись, что эта функция есть
)
from bot.keyboards.admin import home_button
from bot.handlers.user.payments.keys_config import start_new_key_config
from database.requests import create_pending_order, update_payment_key_id, extend_vpn_key
from bot.services.vpn_api import push_key_to_panel

logger = logging.getLogger(__name__)
router = Router()

# Добавь кнопку "Платежи вручную" в клавиатуру главного меню админки
# и этот хендлер.

@router.callback_query(F.data == "admin_manual_payments_list")
async def show_manual_payments_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    pending_payments = get_pending_manual_payments() # <-- Создать эту функцию
    
    if not pending_payments:
        await safe_edit_or_send(
            callback.message,
            "✅ Нет активных заявок на ручную оплату.",
            reply_markup=InlineKeyboardBuilder().row(home_button()).as_markup()
        )
        await callback.answer()
        return
        
    builder = InlineKeyboardBuilder()
    for payment in pending_payments:
        username = f"@{payment['username']}" if payment['username'] else f"ID: {payment['user_telegram_id']}"
        label = f"Заявка №{payment['id']} от {username}"
        builder.row(InlineKeyboardButton(text=label, callback_data=f"admin_view_manual_payment:{payment['id']}"))
    
    builder.row(home_button())
    
    await safe_edit_or_send(
        callback.message,
        f"👇 <b>Заявки на ручную оплату ({len(pending_payments)} шт.):</b>",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_view_manual_payment:"))
async def view_manual_payment(callback: CallbackQuery, bot: Bot):
    if not is_admin(callback.from_user.id):
        return

    payment_id = int(callback.data.split(':')[1])
    payment = get_manual_payment_by_id(payment_id) # <-- Создать эту функцию
    
    if not payment or payment['status'] != 'pending':
        await callback.answer("⚠️ Заявка уже обработана или не найдена.", show_alert=True)
        # Обновляем список, чтобы убрать обработанную заявку
        await show_manual_payments_list(callback)
        return

    tariff = get_tariff_by_id(payment['tariff_id'])
    
    caption = (
        f"📝 <b>Заявка на оплату №{payment['user_telegram_id']}</b>\n\n"
        f"<b>Пользователь:</b> {payment['username'] or 'N/A'} (<code>{payment['user_telegram_id']}</code>)\n"
        f"<b>Тариф:</b> {tariff['name'] if tariff else 'Не найден'}\n"
        f"<b>Сумма:</b> {payment['amount']} ₽\n"
        f"<b>Дата:</b> {payment['created_at']}\n\n"
        "Проверьте скриншот и подтвердите или отклоните платеж."
    )
    
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"admin_approve_payment:{payment_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin_reject_payment:{payment_id}")
    )
    builder.row(InlineKeyboardButton(text="⬅️ К списку заявок", callback_data="admin_manual_payments_list"))

    # Отправляем фото с подписью и кнопками
    await bot.send_photo(
        chat_id=callback.from_user.id,
        photo=payment['screenshot_file_id'],
        caption=caption,
        reply_markup=builder.as_markup()
    )
    await callback.message.delete() # Удаляем старое сообщение со списком
    await callback.answer()

@router.callback_query(F.data.startswith("admin_approve_payment:"))
async def approve_payment(callback: CallbackQuery, bot: Bot, state: FSMContext):
    admin_id = callback.from_user.id
    if not is_admin(admin_id): return

    payment_id = int(callback.data.split(':')[1])
    payment = get_manual_payment_by_id(payment_id)

    if not payment or payment['status'] != 'pending':
        await callback.answer("⚠️ Заявка уже обработана или не найдена.", show_alert=True)
        return

    tariff = get_tariff_by_id(payment['tariff_id'])
    if not tariff:
        await callback.answer("❌ Ошибка: тариф для этой заявки был удален!", show_alert=True)
        return

    # ========== НАЧАЛО: ВЫДАЧА КЛЮЧА ==========
    from database.requests import get_or_create_user, create_initial_vpn_key, create_pending_order, complete_order
    from bot.handlers.user.payments.keys_config import start_new_key_config
    
    user_id = payment['user_telegram_id']
    
    # Получаем или создаем пользователя в БД
    (user, _) = get_or_create_user(user_id, None)  # username можно не передавать или получить отдельно
    internal_user_id = user['id']
    
    # Параметры тарифа
    duration_days = tariff['duration_days']
    traffic_limit_bytes = (tariff.get('traffic_limit_gb', 0) or 0) * 1024 ** 3
    
    # Создаем VPN ключ
    key_id = create_initial_vpn_key(
        internal_user_id, 
        tariff['id'], 
        duration_days, 
        traffic_limit=traffic_limit_bytes
    )
    
    # Создаем заказ (pending -> complete)
    (_, order_id) = create_pending_order(
        user_id=internal_user_id, 
        tariff_id=tariff['id'], 
        payment_type='manual',  # или 'manual_payment'
        vpn_key_id=key_id
    )
    complete_order(order_id)
    
    # Сохраняем данные для отправки конфига
    await state.update_data(new_key_order_id=order_id, new_key_id=key_id)
    # ========== КОНЕЦ: ВЫДАЧА КЛЮЧА ==========

    # Обновляем статус нашей ручной заявки
    update_manual_payment_status(payment_id, 'approved', admin_id)

    # Редактируем сообщение админа
    await callback.message.edit_caption(
        caption=callback.message.caption + "\n\n<b>✅ ОДОБРЕНО</b> вами.",
        reply_markup=None,
        parse_mode="HTML"
    )
    
    # Уведомляем пользователя (НЕ отправляем ключ тут, а запускаем процесс создания)
    try:
        # Сначала короткое уведомление
        await bot.send_message(
            payment['user_telegram_id'],
            f"✅ <b>Ваш платеж на сумму {payment['amount']} ₽ подтвержден!</b>\n\n"
            f"Тариф «{tariff['name']}» активирован.",
            parse_mode="HTML"
        )
        
        # А теперь запускаем процесс выдачи ключа (как в триале)
        # Нам нужно отправить сообщение пользователю, но у нас нет message объекта
        # Поэтому получим его через bot
        from aiogram.types import Message
        from aiogram import F
        
        # Создаем фейковое сообщение или отправляем новое
        msg = await bot.send_message(
            payment['user_telegram_id'],
            "🔄 Генерирую ваш VPN-ключ..."
        )
        
        # Запускаем выдачу конфига (эта функция сама отправит ключ/файл)
        await start_new_key_config(msg, state, order_id, key_id)
        
    except Exception as e:
        logger.warning(f"Не удалось выдать ключ пользователю {payment['user_telegram_id']}: {e}")
        await bot.send_message(
            payment['user_telegram_id'],
            "⚠️ Произошла ошибка при генерации ключа. Пожалуйста, обратитесь в поддержку."
        )
    
    await callback.answer("✅ Заявка одобрена. Ключ выдан.", show_alert=True)

@router.callback_query(F.data.startswith("admin_reject_payment:"))
async def reject_payment(callback: CallbackQuery, bot: Bot):
    admin_id = callback.from_user.id
    if not is_admin(admin_id): return

    payment_id = int(callback.data.split(':')[1])
    payment = get_manual_payment_by_id(payment_id)

    if not payment or payment['status'] != 'pending':
        await callback.answer("⚠️ Заявка уже обработана или не найдена.", show_alert=True)
        return
    # ... (проверки)

    update_manual_payment_status(payment_id, 'rejected', admin_id)

    await callback.message.edit_caption(
        caption=callback.message.caption + "\n\n<b>❌ ОТКЛОНЕНО</b> вами.",
        reply_markup=None, parse_mode='HTML'
    )
    
    # Уведомляем пользователя
    try:
        await bot.send_message(
            payment['user_telegram_id'],
            f"❌ <b>Ваш платеж на сумму {payment['amount']} ₽ был отклонен.</b>\n\n"
            "Возможные причины: неверная сумма, нечитаемый скриншот, средства не поступили. "
            "Пожалуйста, свяжитесь с поддержкой для уточнения деталей.", parse_mode='HTML'
        )
    except Exception as e:
        logger.warning(f"Не удалось уведомить пользователя {payment['user_telegram_id']} об отклонении: {e}")