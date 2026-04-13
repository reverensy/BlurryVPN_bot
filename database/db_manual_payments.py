# --- НОВЫЙ ФАЙЛ: database/db_manual_payments.py ---

import sqlite3
import logging
from typing import List, Dict, Any, Optional
from .connection import get_db

logger = logging.getLogger(__name__)

# Этот __all__ нужен, чтобы requests.py мог импортировать все функции через *
__all__ = [
    'create_manual_payment_request',
    'get_pending_manual_payments',
    'get_manual_payment_by_id',
    'update_manual_payment_status',
    'get_pending_manual_payments_by_user'
]

def get_pending_manual_payments_by_user(user_telegram_id: int) -> list:
    """Возвращает список активных заявок для конкретного пользователя."""
    with get_db() as conn:
        from .db_users import get_user_internal_id
        internal_user_id = get_user_internal_id(user_telegram_id)
        if not internal_user_id:
            return []
            
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM manual_payments WHERE user_id = ? AND status = 'pending'",
            (internal_user_id,)
        )
        return cursor.fetchall()

def create_manual_payment_request(user_telegram_id: int, username: str, tariff_id: int, amount: float, screenshot_file_id: str, key_id_to_renew: Optional[int] = None) -> int:
    """Создает новую заявку на ручную оплату и возвращает ее ID."""
    with get_db() as conn:
        from .db_users import get_or_create_user
        user_data, _ = get_or_create_user(user_telegram_id, username)
        internal_user_id = user_data['id']

        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO manual_payments (user_id, username, tariff_id, amount, screenshot_file_id, key_id_to_renew)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (internal_user_id, username, tariff_id, amount, screenshot_file_id, key_id_to_renew)
        )
        return cursor.lastrowid

def get_pending_manual_payments() -> List[Dict[str, Any]]:
    """Возвращает список необработанных заявок."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT mp.id, mp.username, mp.amount, mp.created_at, u.telegram_id as user_telegram_id
            FROM manual_payments mp
            JOIN users u ON mp.user_id = u.id
            WHERE mp.status = 'pending' 
            ORDER BY mp.created_at ASC
        """)
        return [dict(row) for row in cursor.fetchall()]

def get_manual_payment_by_id(payment_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает данные заявки по ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT mp.*, u.telegram_id as user_telegram_id, u.id as user_internal_id
            FROM manual_payments mp
            JOIN users u ON mp.user_id = u.id
            WHERE mp.id = ?
        """, (payment_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def update_manual_payment_status(payment_id: int, status: str, admin_id: int) -> bool:
    """Обновляет статус заявки."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE manual_payments 
            SET status = ?, admin_id = ?, processed_at = CURRENT_TIMESTAMP 
            WHERE id = ?
            """,
            (status, admin_id, payment_id)
        )
        return cursor.rowcount > 0