from tools import *
import random


def good(weather):
    """
    Return a list of 'happy' expression paths.
    It may include extra sun-related expressions if the weather is sunny and it's daytime.
    """

    selected_expressions = []

    expressions_list = [
        "expressions/happy.gif",
        "expressions/upside_down.gif",
        "expressions/slightly_happy.gif",
        "expressions/grin.gif",
        "expressions/wink.gif",
        "expressions/sun.gif",
        "expressions/cowboy.gif",
        "expressions/nerd-face.gif",
        "expressions/sunglasses-face.gif",
        "expressions/grinning.gif"
    ]

    selected_expressions.append(random.choice(expressions_list))

    # Add extra expression based on first choice and weather
    if selected_expressions[0] == "expressions/upside_down.gif":
        selected_expressions.append("expressions/upside_down.gif")
        selected_expressions.append("expressions/happy.gif")
    elif selected_expressions[0] == "expressions/sun.gif" and is_day() and is_sunny(weather):
        selected_expressions.append("expressions/sun.gif")
        selected_expressions.append("expressions/happy.gif")

    else:

        expressions_list = [
            "expressions/happy.gif",
            "expressions/slightly_happy.gif",
            "expressions/grin.gif",
            "expressions/wink.gif"
        ]

        selected_expressions[0] = random.choice(expressions_list)

    return selected_expressions


def is_sunny(weather):
    """
    Return True if the given weather description implies sunny conditions.
    """
    weather = weather.lower()  

    if weather is not None and any(word in weather for word in ["clear", "sun", "sunny", "bright"]):
        return True
    else:
        return False


def angry():
    """
    Return a list of expressions for an 'angry' mood.
    """

    selected_expressions = []

    expressions_list = [
        "expressions/unamused.gif",
        "expressions/raised-eyebrow.gif",
        "expressions/rolling-eyes.gif",
        "expressions/thinking-face.gif"
    ]

    selected_expressions.append(random.choice(expressions_list))

    if selected_expressions[0] == "expressions/raised-eyebrow.gif":
        selected_expressions.append("expressions/raised-eyebrow.gif")
    elif selected_expressions[0] == "expressions/thinking-face.gif":
        selected_expressions.append("expressions/thinking-face.gif")

    return selected_expressions


def neutral():

    """
    Return a single neutral expression.
    """
    neutral_expressions  = [
        "expressions/neutral.gif",
        "expressions/expressionless.gif"
    ]

    return random.choice(neutral_expressions)


def sad():
    """
    Return a list of 'sad' expressions, always ending with the main sad gif.
    """

    selected_expressions = []

    sad_expressions  = [
        "expressions/pensive.gif",
        "expressions/worried.gif",
        "expressions/concerned.gif",
        "expressions/exhale.gif"
    ]

    selected_expressions.append(random.choice(sad_expressions))

    selected_expressions.append("expressions/sad.gif")

    return selected_expressions


def cry():
    """
    Return a single expression for 'crying' / very sad mood.
    """
    cry_expressions  = [
        "expressions/leaves.gif",
        "expressions/cry.gif",
        "expressions/dizzy-face.gif",
        "expressions/x-eyes.gif"
    ]

    return random.choice(cry_expressions)


def cry_static():
    """
    Return a static crying PNG (used after animated crying).
    """
    return "expressions/cry_static.png"


def low_sun_exposure():
    """
    Return expressions for low sun exposure (needs more light).
    """

    expressions  = [
        "expressions/moon.png",
        "expressions/cloud.gif",
        "expressions/cloud.gif"
    ]

    return expressions 


def water():
    """
    Return expressions related to low soil moisture / need for water.
    """
    expressions  = [
        "expressions/droplet.gif",
        "expressions/droplet.gif",
        "expressions/droplet.gif",
        "expressions/leafs.gif",
        "expressions/leafs.gif",
        "expressions/Battery-low.gif"
    ]

    return expressions 


def low_humidity():
    """
    Return expression related to low air humidity.
    """
    return "expressions/low_humidity.png"


def warning():
    """
    Return generic warning expression.
    """
    return "expressions/warning.gif"


def high_sun_exposure():
    """
    Return expressions for high sun exposure.
    """
    expressions  = [
        "expressions/warning.gif",
        "expressions/warning.gif",
        "expressions/sun.png",
        "expressions/dotted-line.gif",
        "expressions/dizzy-face.gif"
    ]

    return expressions 


def thanks():
    """
    Return expressions used after the user has watered.
    """
    expressions  = [
        "expressions/good.gif",
        "expressions/good.gif",
        "expressions/plant.gif",
        "expressions/battery-full.gif",
        "expressions/relieved.gif",
    ]

    return expressions 


def talk():
    """
    Return the 'talk' image shown while the plant is replying.
    """
    return "expressions/talk.png"


def cold():
    """
    Return expressions related to cold temperature.
    It may duplicate the selected cold gif.
    """
    selected_expressions = []

    cold_expressions  = [
        "expressions/cold.gif",
        "expressions/cold_2.gif",
        "expressions/cold_3.gif",
        "expressions/x-eyes.gif"
    ]

    selected_expressions.append(random.choice(cold_expressions))

    if selected_expressions[0] == "expressions/cold.gif":
        selected_expressions.append("expressions/cold.gif")
    elif selected_expressions[0] == "expressions/cold_3.gif":
        selected_expressions.append("expressions/cold_3.gif")
    elif selected_expressions[0] == "expressions/cold_2.gif":
        selected_expressions.append("expressions/cold_2.gif")

    return selected_expressions


def hot():
    """
    Return expressions related to hot temperature.
    """
    expressions  = [
        "expressions/hot.gif",
        "expressions/fire.gif",
        "expressions/fire.gif",
        "expressions/fire.gif"
    ]

    return expressions 
