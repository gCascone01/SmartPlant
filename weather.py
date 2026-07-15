import requests

city = "Patras"
current_weather = requests.get(f"http://wttr.in/{city}?format=%C").text

print(current_weather)