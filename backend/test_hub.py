import os
import dirigera
from dotenv import load_dotenv

load_dotenv()

ip_address = os.getenv("DIRIGERA_IP")
token = os.getenv("DIRIGERA_TOKEN")


hub = dirigera.Hub(
    token=token,
    ip_address=ip_address
)

lights = hub.get_lights()

for light in lights:
    print(light.attributes.custom_name)

target = None

for light in lights:
    if light.attributes.custom_name == "Bulb 1":
        target = light
        break

if target is None:
    print("Light not found")
else:
    target.set_light(True)
    target = target.reload()
    print("Current state:", target.attributes.is_on)