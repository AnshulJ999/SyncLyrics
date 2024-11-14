from os import path
from typing import Any
import json 

from benedict import benedict


DEFAULT_STATE = {
    "theme": "dark",
    "representationMethods": {
        "terminal": False
    },
}

state = None # memory cache for state to avoid reading from disk


def reset_state(): 
    """
    This function resets the state to the default state.
    """

    set_state(DEFAULT_STATE)


def set_state(new_state: dict):
    """
    This function sets the state to the given state.

    Args:
        new_state (dict): The new state.
    """

    global state
    with open("state.json", "w") as f: json.dump(new_state, f, indent=4)
    state = None # clear memory cache


def get_state() -> dict:
    """
    This function returns the current state.

    Returns:
        dict: The current state.
    """

    global state
    if state is not None: return state # memory cache
    if not path.exists("state.json"): reset_state()
    with open("state.json", "r") as f: return json.load(f)


def set_attribute_js_notation(state: dict, attribute: str, value: Any) -> dict:
    """
    This function sets the given attribute to the given value in the given state.

    Args:
        state (dict): The state to set the attribute in.
        attribute (str): The attribute to set in js notation.
        value (Any): The value to set the attribute to.

    Returns:
        dict: The state with the attribute set to the value.
    """

    state = benedict(state, keypath_separator=".")
    state[attribute] = value
    return state.dict()


def get_attribute_js_notation(state: dict, attribute: str) -> Any:
    """
    This function returns the value of the given attribute in the given state.

    Args:
        state (dict): The state to get the attribute from.
        attribute (str): The attribute to get in js notation.

    Returns:
        Any: The value of the attribute.
    """

    state = benedict(state, keypath_separator=".")
    return state[attribute]
