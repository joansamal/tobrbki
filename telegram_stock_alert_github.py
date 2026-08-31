"""
Bot de alertas de precios de acciones para Telegram - versión GitHub Actions.

Este script se ejecuta UNA VEZ cada vez que lo llama GitHub Actions
(cada 5-10 minutos, según el workflow configurado), revisa los precios,
y si alguno cumple la condición, envía un mensaje de Telegram.

El token y el chat_id se leen de variables de entorno (GitHub Secrets),
NO del archivo config.json, para no exponerlos en un repo público.

Requiere: requests, yfinance
"""

import os
import json
import logging
import requests
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

CONFIG_FILE = "config.json"


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


def get_current_price(ticker):
    try:
        stock = yf.Ticker(ticker)
        data = stock.history(period="1d", interval="1m")
        if data.empty:
            data = stock.history(period="5d")
        return float(data["Close"].iloc[-1])
    except Exception as e:
        logging.error(f"Error obteniendo precio de {ticker}: {e}")
        return None


def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    config = load_config()
    alerts = config["alerts"]
    config_changed = False

    for alert in alerts:
        if alert.get("triggered"):
            continue

        ticker = alert["ticker"]
        target = alert["target_price"]
        direction = alert.get("direction", "above")

        price = get_current_price(ticker)
        if price is None:
            continue

        logging.info(f"{ticker}: precio actual {price:.2f}, objetivo {target} ({direction})")

        condition_met = (
            (direction == "above" and price >= target)
            or (direction == "below" and price <= target)
        )

        if condition_met:
            emoji = "📈" if direction == "above" else "📉"
            comparador = "≥" if direction == "above" else "≤"
            message = (
                f"{emoji} ¡Alerta de precio!\n"
                f"{ticker} alcanzó {price:.2f}\n"
                f"Objetivo: {comparador} {target}"
            )
            sent_ok = send_telegram_message(token, chat_id, message)
            if sent_ok:
                alert["triggered"] = True
                config_changed = True

    if config_changed:
        save_config(config)


if __name__ == "__main__":
    main()
