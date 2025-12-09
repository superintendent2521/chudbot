import os
import json

FILENAME = "warns.json"



def load_warns():
    """Read and return the warns JSON as a Python dict."""
    if not os.path.exists(FILENAME):
        return {}
    with open(FILENAME, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_warns(data):
    """Write the warns dict back to the JSON file."""
    with open(FILENAME, "w") as f:
        json.dump(data, f, indent=4)

def add_warn(user_id, warn_text):
    """Add a warning string for a user."""
    data = load_warns()

    if user_id not in data:
        data[user_id] = {"warns": []}  # initialize as a list

    data[user_id]["warns"].append(warn_text)  # append the warning text
    save_warns(data)
    return data[user_id]["warns"]

def get_warns(user_id):
    """Return a list of a user's warnings (empty list if none)."""
    data = load_warns()
    return data.get(user_id, {"warns": []})["warns"]
