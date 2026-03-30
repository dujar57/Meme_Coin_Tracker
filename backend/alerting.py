"""
Alerting : notifications Telegram/Discord/email.
À appeler quand un token atteint TP/SL ou une condition d'alerte.
"""
import os
import httpx
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()


async def send_telegram(message: str) -> bool:
    """Envoie un message via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
                timeout=5.0,
            )
            return r.status_code == 200
    except Exception:
        return False


async def send_discord(message: str) -> bool:
    """Envoie un message via Discord webhook."""
    if not DISCORD_WEBHOOK_URL:
        return False
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                DISCORD_WEBHOOK_URL,
                json={"content": message},
                timeout=5.0,
            )
            return r.status_code in (200, 204)
    except Exception:
        return False


async def send_alert(token_name: str, condition: str, price: float, target: float) -> None:
    """
    Envoie une alerte (TP/SL atteint) via les canaux configurés.
    Ex: send_alert("BONK", "TP", 0.00002, 0.000015)
    """
    msg = f"🚨 {token_name}: {condition} atteint! Prix actuel: ${price:.10g}, Cible: ${target:.10g}"
    await send_telegram(msg)
    await send_discord(msg)
