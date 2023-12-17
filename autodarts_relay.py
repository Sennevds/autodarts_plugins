import json
import threading
import time
import gpiozero
import os
import rel
import requests
import websocket
from keycloak import KeycloakOpenID
from const import AUTODART_BOARDS_URL, AUTODART_URL, AUTODART_AUTH_URL, AUTODART_CLIENT_ID, \
    AUTODART_REALM_NAME, AUTODART_AUTH_TICKET_URL, AUTODART_WEBSOCKET_URL

AUTODART_USER_EMAIL = os.environ.get("USER_NAME")
AUTODART_USER_PASSWORD = os.environ.get("USER_PASSWORD")
AUTODART_USER_BOARD_ID = os.environ.get("USER_BOARD_ID")
TOGGLE_LIGHT_TIME_OUT = 60 * os.environ.get("TIMEOUT", default=1)
playing = False
accessToken: str
currentMatch: str
RELAY_PIN = os.environ.get("RELAY_PIN", default=11)
relay = gpiozero.OutputDevice(RELAY_PIN, active_high=True, initial_value=False)


def turn_off_light():
    relay.off()


t = threading.Timer(TOGGLE_LIGHT_TIME_OUT, turn_off_light)


def on_message_autodarts(ws, message):
    def process(*args):
        try:
            m = json.loads(message)
            if (
                    "data" in m
                    and "message" in m["data"]
                    and (
                    "Successfully subscribed" in m["data"]["message"]
                    or "Successfully unsubscribed" in m["data"]["message"]
            )
            ):
                return

            elif m["channel"] == "autodarts.boards":
                data = m["data"]
                listen_to_newest_match(data, ws)

        except Exception as e:
            print("WS-Message failed: ", e)

    threading.Thread(target=process).start()


def on_error(ws, error):
    print(error)


def on_close_autodarts(ws, close_status_code, close_msg):
    time.sleep(3)
    connect_autodarts()


def listen_to_newest_match(m, ws):
    global currentMatch
    global t
    global playing
    if "event" not in m:
        return

    if m["event"] == "start":
        currentMatch = m["id"]
        print("Listen to match: " + currentMatch)
        paramsSubscribeMatchesEvents = {
            "channel": "autodarts.matches",
            "type": "subscribe",
            "topic": currentMatch + ".state",
        }
        ws.send(json.dumps(paramsSubscribeMatchesEvents))
        if relay.value == 0:
            relay.on()
        playing = True
        t.cancel()

    elif m["event"] == "finish" or m["event"] == "delete":
        print("Stop listening to match: " + m["id"])

        paramsUnsubscribeMatchEvents = {
            "type": "unsubscribe",
            "channel": "autodarts.matches",
            "topic": m["id"] + ".state",
        }
        ws.send(json.dumps(paramsUnsubscribeMatchEvents))
        playing = False
        if not t.is_alive():
            t = threading.Timer(TOGGLE_LIGHT_TIME_OUT, turn_off_light)
        t.start()


def on_open_autodarts(ws):
    try:
        receive_token_autodarts()
        global accessToken
        res = requests.get(
            AUTODART_BOARDS_URL + AUTODART_USER_BOARD_ID,
            headers={"Authorization": "Bearer " + accessToken},
        )
        json_resp = res.json()
        if "matchId" in json_resp:
            match_id = res.json()["matchId"]
            if match_id is not None and match_id != "":
                m = {"event": "start", "id": match_id}
                listen_to_newest_match(m, ws)
    except Exception as e:
        print("Fetching matches failed", e)
    try:
        print("Receiving live information from " + AUTODART_URL)

        paramsSubscribeMatchesEvents = {
            "channel": "autodarts.boards",
            "type": "subscribe",
            "topic": AUTODART_USER_BOARD_ID + ".matches",
        }
        ws.send(json.dumps(paramsSubscribeMatchesEvents))

    except Exception as e:
        print("WS-Open-boards failed: ", e)


def receive_token_autodarts():
    try:
        global accessToken

        # Configure client
        keycloak_openid = KeycloakOpenID(
            server_url=AUTODART_AUTH_URL,
            client_id=AUTODART_CLIENT_ID,
            realm_name=AUTODART_REALM_NAME,
            verify=True,
        )
        token = keycloak_openid.token(AUTODART_USER_EMAIL, AUTODART_USER_PASSWORD)
        accessToken = token["access_token"]
        # ppi(token)
    except Exception as e:
        print("Receive token failed", e)


def connect_autodarts():
    def process(*args):
        global accessToken

        receive_token_autodarts()
        # Get Ticket
        ticket = requests.post(
            AUTODART_AUTH_TICKET_URL, headers={"Authorization": "Bearer " + accessToken}
        )

        websocket.enableTrace(False)
        ws = websocket.WebSocketApp(
            AUTODART_WEBSOCKET_URL + ticket.text,
            on_open=on_open_autodarts,
            on_message=on_message_autodarts,
            on_error=on_error,
            on_close=on_close_autodarts,
        )

        ws.run_forever()

    threading.Thread(target=process).start()


if __name__ == "__main__":
    connect_autodarts()
    rel.signal(2, rel.abort)  # Keyboard Interrupt
    rel.dispatch()
