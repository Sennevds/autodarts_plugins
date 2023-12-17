import threading
import websocket
import json
import rel
import time
from slack_sdk import WebClient
import os
from keycloak import KeycloakOpenID
import requests

from const import AUTODART_STATS_URL, AUTODART_BOARDS_URL, AUTODART_URL, AUTODART_AUTH_URL, AUTODART_CLIENT_ID, \
    AUTODART_REALM_NAME, AUTODART_AUTH_TICKET_URL, AUTODART_WEBSOCKET_URL

AUTODART_USER_EMAIL = os.environ.get("USER_NAME")
AUTODART_USER_PASSWORD = os.environ.get("USER_PASSWORD")
AUTODART_USER_BOARD_ID = os.environ.get("USER_BOARD_ID")
accessToken: str
currentMatch: str
webclient_token = os.environ.get("WEBCLIENT_TOKEN")
client = WebClient(token=webclient_token)


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
            if m["channel"] == "autodarts.matches":
                data = m["data"]

            elif m["channel"] == "autodarts.boards":
                data = m["data"]

                listen_to_newest_match(data, ws)

            elif m["channel"] == "autodarts.lobbies":
                data = m["data"]
        except Exception as e:
            print("WS-Message failed: ", e)

    threading.Thread(target=process).start()


def on_error(ws, error):
    print(error)


def on_close_autodarts(ws, close_status_code, close_msg):
    time.sleep(3)
    connect_autodarts()


def format_names(names):
    # check if the input is a list of strings
    if isinstance(names, list) and all(isinstance(name, str) for name in names):
        # if the list is empty, return an empty string
        if not names:
            return ""
        # if the list has only one name, return that name
        elif len(names) == 1:
            return names[0]
        # if the list has more than one name, join them with commas except the last two, which are joined with 'and'
        else:
            return ", ".join(names[:-2]) + ", " + names[-2] + " and " + names[-1]
    else:
        # if the input is not a list of strings, raise an exception
        raise TypeError("The input must be a list of strings")


def combine_names(names_and_averages):
    if not names_and_averages:
        return ""

    if len(names_and_averages) == 1:
        name, average = names_and_averages[0]
        return f"{name}({average})"

    # Remove leading and trailing whitespaces from each name
    names_and_averages = [
        (name.strip(), average) for name, average in names_and_averages
    ]

    # Combine all names except the last two
    combined_names = ", ".join(
        [f"{name}({average})" for name, average in names_and_averages[:-1]]
    )
    # combined_names = ", ".join(names[:-1])

    # Add the last name separated by 'and'
    last_name, last_average = names_and_averages[-1]
    combined_names += f" and {last_name}({last_average})"
    # combined_names += f" and {names[-1]}"

    return combined_names


def extract_match_info(match_data):
    match_info = {}
    # Extract information
    winner_index = match_data["winner"]
    match_info["duration"] = match_data["duration"]
    match_info["variant"] = match_data["variant"]
    match_info["total_legs"] = len(match_data["legStats"])
    # player_names = [player["name"] for player in match_data["players"]]

    # Check if the winner is determined by most legs
    if winner_index == -1:
        # Count the number of legs won by each player
        legs_won_count = [0] * len(match_data["players"])

        for leg_stat in match_data["legStats"]:
            winner_index = leg_stat["winner"]
            legs_won_count[winner_index] += 1

        # Determine the overall winner based on legs won
        overall_winner_index = max(
            range(len(legs_won_count)), key=legs_won_count.__getitem__
        )
        match_info["winner"] = match_data["players"][overall_winner_index]["name"]
        match_info["legs_won_by_winner"] = legs_won_count[overall_winner_index]
    else:
        # Get the name of the winner
        match_info["winner"] = match_data["players"][winner_index]["name"]
        match_info["legs_won_by_winner"] = len(
            [
                leg_stat
                for leg_stat in match_data["legStats"]
                if leg_stat["winner"] == winner_index
            ]
        )

    averages = []
    match_stats = match_data.get("matchStats", [])
    for player in match_data["players"]:
        name = player["name"]
        average = round(match_stats[player["index"]]["average"], 2)
        averages.append((name, average))

    match_info["player_averages"] = averages
    return match_info


def get_match_stats(id):
    receive_token_autodarts()
    global accessToken
    res = requests.get(
        AUTODART_STATS_URL.format(id),
        headers={"Authorization": "Bearer " + accessToken},
    )
    match_info = extract_match_info(res.json())
    send_text = f"{combine_names(match_info['player_averages'])} just played a match of {match_info['variant']}, they played {match_info['total_legs']} legs.\n{match_info['winner']} won with {match_info['legs_won_by_winner']} legs\nhttps://play.autodarts.io/history/matches/{id}"
    client.chat_postMessage(channel="#dartsclub", text=send_text, unfurl_links=False)

    print()


def listen_to_newest_match(m, ws):
    global currentMatch
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

    elif m["event"] == "finish" or m["event"] == "delete":
        isFinish = m["event"] == "finish"
        print("Stop listening to match: " + m["id"])

        paramsUnsubscribeMatchEvents = {
            "type": "unsubscribe",
            "channel": "autodarts.matches",
            "topic": m["id"] + ".state",
        }
        ws.send(json.dumps(paramsUnsubscribeMatchEvents))

        if isFinish:
            get_match_stats(m["id"])


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
    # get_match_stats('803190e1-57eb-4bb0-8250-ba8d2e2c4b71')
    connect_autodarts()
    rel.signal(2, rel.abort)  # Keyboard Interrupt
    rel.dispatch()
