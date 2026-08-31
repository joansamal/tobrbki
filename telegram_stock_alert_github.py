bash

cat > /mnt/user-data/outputs/telegram_stock_alert_github.py << 'PYEOF'
"""
Bot de alertas de precios de acciones para Telegram - versión con comandos.

Se ejecuta cada pocos minutos vía GitHub Actions. En cada ejecución:
1. Revisa si le escribiste algún comando nuevo por Telegram y lo procesa
   (añadir alertas, listar, borrar, resetear) - así no hace falta tocar
   config.json a mano.
2. Revisa el precio de cada alerta activa y avisa si se cumple la condición.

Comandos disponibles (escríbelos en el chat/grupo donde está el bot):
  /addabove TICKER PRECIO      -> avisa cuando el precio suba a PRECIO o más
  /addbelow TICKER PRECIO      -> avisa cuando el precio baje a PRECIO o menos
  /adddrop TICKER PORCENTAJE   -> avisa cuando el precio caiga ese % desde ahora
  /list                        -> lista todas las alertas activas
  /remove ID                   -> elimina una alerta por su ID
  /reset ID                    -> vuelve a activar una alerta ya disparada
  /help                        -> muestra la lista de comandos

El token y el chat_id se leen de variables de entorno (GitHub Secrets).
Requiere: requests, yfinance
"""

import os
import json
import logging
import requests
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

CONFIG_FILE = "config.json"

HELP_TEXT = (
    "Comandos disponibles:\n"
    "/addabove TICKER PRECIO - avisa si sube a ese precio o más\n"
    "/addbelow TICKER PRECIO - avisa si baja a ese precio o menos\n"
    "/adddrop TICKER PORCENTAJE - avisa si cae ese % desde ahora\n"
    "/list - lista todas las alertas\n"
    "/remove ID - elimina una alerta\n"
    "/reset ID - reactiva una alerta ya disparada\n"
    "/help - muestra esta ayuda"
)


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def send_telegram_message(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        response = requests.post(url, data=payload, timeout=10)
        if not response.ok:
            logging.error(f"Telegram respondió con error {response.status_code}: {response.text}")
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        logging.error(f"Error enviando mensaje a Telegram: {e}")
        return False


def get_telegram_updates(token, offset):
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"offset": offset, "timeout": 0}
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json().get("result", [])
    except requests.RequestException as e:
        logging.error(f"Error obteniendo updates de Telegram: {e}")
        return []


def get_current_price(ticker):
    try:
        stock = yf.Ticker(ticker)
        data = stock.history(period="1d", interval="1m")
        if data.empty:
            data = stock.history(period="5d")
        if data.empty:
            return None
        return float(data["Close"].iloc[-1])
    except Exception as e:
        logging.error(f"Error obteniendo precio de {ticker}: {e}")
        return None


def next_alert_id(config):
    existing_ids = [a.get("id", 0) for a in config["alerts"]]
    return (max(existing_ids) + 1) if existing_ids else 1


def process_commands(config, token, chat_id):
    """Revisa mensajes nuevos de Telegram y ejecuta comandos. Devuelve True si config cambió."""
    changed = False
    offset = config.get("last_update_id", 0) + 1
    updates = get_telegram_updates(token, offset)

    for update in updates:
        update_id = update.get("update_id", 0)
        if update_id > config.get("last_update_id", 0):
            config["last_update_id"] = update_id
            changed = True

        message = update.get("message")
        if not message or "text" not in message:
            continue

        # Solo aceptar comandos que vengan del chat configurado (seguridad)
        if str(message["chat"]["id"]) != str(chat_id):
            continue

        text = message["text"].strip()
        if not text.startswith("/"):
            continue

        parts = text.split()
        command = parts[0].lower()
        reply = None

        try:
            if command == "/help" or command == "/start":
                reply = HELP_TEXT

            elif command == "/list":
                if not config["alerts"]:
                    reply = "No tienes ninguna alerta configurada."
                else:
                    lines = ["Tus alertas:"]
                    for a in config["alerts"]:
                        estado = "✅ disparada" if a.get("triggered") else "⏳ activa"
                        if a["type"] == "drop":
                            lines.append(
                                f"#{a['id']} {a['ticker']} cae {a['drop_percent']}% "
                                f"desde {a['reference_price']:.2f} ({estado})"
                            )
                        else:
                            simbolo = "≥" if a["type"] == "above" else "≤"
                            lines.append(
                                f"#{a['id']} {a['ticker']} {simbolo} {a['target_price']} ({estado})"
                            )
                    reply = "\n".join(lines)

            elif command == "/addabove" and len(parts) == 3:
                ticker = parts[1].upper()
                price = float(parts[2])
                new_id = next_alert_id(config)
                config["alerts"].append({
                    "id": new_id, "ticker": ticker, "type": "above",
                    "target_price": price, "triggered": False
                })
                changed = True
                reply = f"✅ Alerta #{new_id} creada: {ticker} ≥ {price}"

            elif command == "/addbelow" and len(parts) == 3:
                ticker = parts[1].upper()
                price = float(parts[2])
                new_id = next_alert_id(config)
                config["alerts"].append({
                    "id": new_id, "ticker": ticker, "type": "below",
                    "target_price": price, "triggered": False
                })
                changed = True
                reply = f"✅ Alerta #{new_id} creada: {ticker} ≤ {price}"

            elif command == "/adddrop" and len(parts) == 3:
                ticker = parts[1].upper()
                percent = float(parts[2])
                current_price = get_current_price(ticker)
                if current_price is None:
                    reply = f"⚠️ No pude obtener el precio actual de {ticker}. Revisa el ticker."
                else:
                    new_id = next_alert_id(config)
                    config["alerts"].append({
                        "id": new_id, "ticker": ticker, "type": "drop",
                        "drop_percent": percent, "reference_price": current_price,
                        "triggered": False
                    })
                    changed = True
                    reply = (
                        f"✅ Alerta #{new_id} creada: te aviso si {ticker} cae {percent}% "
                        f"desde {current_price:.2f}"
                    )

            elif command == "/remove" and len(parts) == 2:
                target_id = int(parts[1])
                before = len(config["alerts"])
                config["alerts"] = [a for a in config["alerts"] if a.get("id") != target_id]
                if len(config["alerts"]) < before:
                    changed = True
                    reply = f"🗑️ Alerta #{target_id} eliminada."
                else:
                    reply = f"⚠️ No encontré ninguna alerta con ID #{target_id}."

            elif command == "/reset" and len(parts) == 2:
                target_id = int(parts[1])
                found = False
                for a in config["alerts"]:
                    if a.get("id") == target_id:
                        a["triggered"] = False
                        if a.get("type") == "drop":
                            # Al reactivar, toma el precio actual como nueva referencia
                            price = get_current_price(a["ticker"])
                            if price:
                                a["reference_price"] = price
                        found = True
                        changed = True
                if found:
                    reply = f"🔄 Alerta #{target_id} reactivada."
                else:
                    reply = f"⚠️ No encontré ninguna alerta con ID #{target_id}."

            else:
                reply = "No entendí ese comando. Escribe /help para ver la lista."

        except (ValueError, IndexError):
            reply = "⚠️ Formato incorrecto. Escribe /help para ver ejemplos."

        if reply:
            send_telegram_message(token, chat_id, reply)

    return changed


def check_alerts(config, token, chat_id):
    """Revisa cada alerta activa y envía aviso si se cumple. Devuelve True si config cambió."""
    changed = False

    for alert in config["alerts"]:
        if alert.get("triggered"):
            continue

        ticker = alert["ticker"]
        price = get_current_price(ticker)
        if price is None:
            continue

        condition_met = False
        message = None

        if alert["type"] == "above":
            target = alert["target_price"]
            logging.info(f"{ticker}: precio actual {price:.2f}, objetivo ≥ {target}")
            if price >= target:
                condition_met = True
                message = f"📈 ¡Alerta!\n{ticker} alcanzó {price:.2f}\nObjetivo: ≥ {target}"

        elif alert["type"] == "below":
            target = alert["target_price"]
            logging.info(f"{ticker}: precio actual {price:.2f}, objetivo ≤ {target}")
            if price <= target:
                condition_met = True
                message = f"📉 ¡Alerta!\n{ticker} alcanzó {price:.2f}\nObjetivo: ≤ {target}"

        elif alert["type"] == "drop":
            ref = alert["reference_price"]
            drop_pct = alert["drop_percent"]
            threshold = ref * (1 - drop_pct / 100)
            current_drop_pct = (ref - price) / ref * 100
            logging.info(
                f"{ticker}: precio actual {price:.2f}, referencia {ref:.2f}, "
                f"caída actual {current_drop_pct:.2f}%, objetivo caída {drop_pct}%"
            )
            if price <= threshold:
                condition_met = True
                message = (
                    f"📉 ¡Alerta de caída!\n{ticker} bajó {current_drop_pct:.1f}% "
                    f"(de {ref:.2f} a {price:.2f})"
                )

        if condition_met and message:
            sent_ok = send_telegram_message(token, chat_id, message)
            if sent_ok:
                alert["triggered"] = True
                changed = True

    return changed


def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    config = load_config()
    config.setdefault("last_update_id", 0)
    config.setdefault("alerts", [])

    commands_changed = process_commands(config, token, chat_id)
    alerts_changed = check_alerts(config, token, chat_id)

    if commands_changed or alerts_changed:
        save_config(config)


if __name__ == "__main__":
    main()
PYEOF
echo "Archivo actualizado correctamente"
