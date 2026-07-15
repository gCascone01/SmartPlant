from astral import LocationInfo
from astral.sun import sun
from datetime import datetime, timezone, timedelta
import pytz


def is_day():

    city = LocationInfo("Athens", "Greece",
                        "Europe/Athens", 37.983810, 23.727539)

    now = datetime.now(pytz.timezone("Europe/Athens"))

    sun_now = sun(city.observer, date=datetime.now(timezone.utc).date(),
                  tzinfo=city.timezone)

    if now < sun_now['sunrise']:
        return False
    elif now < sun_now['sunset'] - timedelta(hours=1):
        return True
    else:
        return False