"""Catalog of sound files bundled with the Home Assistant iOS Companion app.

The Companion app ships a fixed library of ``.wav`` files that any
notification can play by setting ``data.push.sound = "<filename>"``.
This module exposes that library — grouped by voice — so the Doorman
panel can render a proper dropdown instead of asking users to type a
filename by hand.

Source: https://github.com/home-assistant/iOS/tree/main/Sources/App/Resources/Sounds
The list is manually kept in sync; the Companion release cadence is
slow and additions rarely happen, so a hardcoded catalog is a good
tradeoff against making the panel depend on a network fetch.

Two sentinel values are recognised by the panel and by
``notifications.py``:

* ``""`` (empty string) — treat as "use the iOS Companion default sound",
  omitting ``data.push.sound`` from the payload entirely.
* ``"__custom__"`` — never sent to the device; the panel treats it as
  "show a text field so the user can enter a side-loaded filename."
"""
from __future__ import annotations

from typing import Final

# Sentinel used by the panel — "let the user type a filename". Never
# reaches the notify payload; the frontend swaps it for the entered text.
CUSTOM_SOUND_SENTINEL: Final[str] = "__custom__"

# Grouped list. Each entry is (group_label, [(filename, display_name), ...]).
# Displays strip the ``US-EN-`` and voice prefix since those are encoded
# by the group label itself — "Alexa / Front Door Opened" reads better
# than "Alexa / US-EN-Alexa-Front-Door-Opened.wav".
IOS_SOUND_GROUPS: Final[list[tuple[str, list[tuple[str, str]]]]] = [
    (
        "Alexa",
        [
            ("US-EN-Alexa-Back-Door-Opened.wav", "Back Door Opened"),
            ("US-EN-Alexa-Back-Door-Unlocked.wav", "Back Door Unlocked"),
            ("US-EN-Alexa-Basement-Door-Opened.wav", "Basement Door Opened"),
            ("US-EN-Alexa-Basement-Door-Unlocked.wav", "Basement Door Unlocked"),
            ("US-EN-Alexa-Boyfriend-Is-Arriving.wav", "Boyfriend Is Arriving"),
            ("US-EN-Alexa-Daughter-Is-Arriving.wav", "Daughter Is Arriving"),
            ("US-EN-Alexa-Front-Door-Opened.wav", "Front Door Opened"),
            ("US-EN-Alexa-Front-Door-Unlocked.wav", "Front Door Unlocked"),
            ("US-EN-Alexa-Garage-Door-Opened.wav", "Garage Door Opened"),
            ("US-EN-Alexa-Girlfriend-Is-Arriving.wav", "Girlfriend Is Arriving"),
            ("US-EN-Alexa-Good-Morning.wav", "Good Morning"),
            ("US-EN-Alexa-Good-Night.wav", "Good Night"),
            ("US-EN-Alexa-Husband-Is-Arriving.wav", "Husband Is Arriving"),
            ("US-EN-Alexa-Mail-Has-Arrived.wav", "Mail Has Arrived"),
            ("US-EN-Alexa-Motion-At-Back-Door.wav", "Motion At Back Door"),
            ("US-EN-Alexa-Motion-At-Front-Door.wav", "Motion At Front Door"),
            ("US-EN-Alexa-Motion-Detected-Generic.wav", "Motion Detected (Generic)"),
            ("US-EN-Alexa-Motion-In-Back-Yard.wav", "Motion In Back Yard"),
            ("US-EN-Alexa-Motion-In-Basement.wav", "Motion In Basement"),
            ("US-EN-Alexa-Motion-In-Front-Yard.wav", "Motion In Front Yard"),
            ("US-EN-Alexa-Motion-In-Garage.wav", "Motion In Garage"),
            ("US-EN-Alexa-Patio-Door-Opened.wav", "Patio Door Opened"),
            ("US-EN-Alexa-Patio-Door-Unlocked.wav", "Patio Door Unlocked"),
            ("US-EN-Alexa-Smoke-Detected-Generic.wav", "Smoke Detected (Generic)"),
            ("US-EN-Alexa-Smoke-Detected-In-Basement.wav", "Smoke Detected In Basement"),
            ("US-EN-Alexa-Smoke-Detected-In-Garage.wav", "Smoke Detected In Garage"),
            ("US-EN-Alexa-Smoke-Detected-In-Kitchen.wav", "Smoke Detected In Kitchen"),
            ("US-EN-Alexa-Son-Is-Arriving.wav", "Son Is Arriving"),
            ("US-EN-Alexa-Water-Detected-Generic.wav", "Water Detected (Generic)"),
            ("US-EN-Alexa-Water-Detected-In-Basement.wav", "Water Detected In Basement"),
            ("US-EN-Alexa-Water-Detected-In-Garage.wav", "Water Detected In Garage"),
            ("US-EN-Alexa-Water-Detected-In-Kitchen.wav", "Water Detected In Kitchen"),
            ("US-EN-Alexa-Welcome-Home.wav", "Welcome Home"),
            ("US-EN-Alexa-Wife-Is-Arriving.wav", "Wife Is Arriving"),
        ],
    ),
    (
        "Daisy",
        [
            ("US-EN-Daisy-Back-Door-Motion.wav", "Back Door Motion"),
            ("US-EN-Daisy-Back-Door-Open.wav", "Back Door Open"),
            ("US-EN-Daisy-Front-Door-Motion.wav", "Front Door Motion"),
            ("US-EN-Daisy-Front-Door-Open.wav", "Front Door Open"),
            ("US-EN-Daisy-Front-Window-Open.wav", "Front Window Open"),
            ("US-EN-Daisy-Garage-Door-Open.wav", "Garage Door Open"),
            ("US-EN-Daisy-Guest-Bath-Leak.wav", "Guest Bath Leak"),
            ("US-EN-Daisy-Kitchen-Sink-Leak.wav", "Kitchen Sink Leak"),
            ("US-EN-Daisy-Kitchen-Window-Open.wav", "Kitchen Window Open"),
            ("US-EN-Daisy-Laundry-Room-Leak.wav", "Laundry Room Leak"),
            ("US-EN-Daisy-Master-Bath-Leak.wav", "Master Bath Leak"),
            ("US-EN-Daisy-Master-Bedroom-Window-Open.wav", "Master Bedroom Window Open"),
            ("US-EN-Daisy-Office-Window-Open.wav", "Office Window Open"),
            ("US-EN-Daisy-Refrigerator-Leak.wav", "Refrigerator Leak"),
            ("US-EN-Daisy-Water-Heater-Leak.wav", "Water Heater Leak"),
        ],
    ),
    (
        "Morgan Freeman",
        [
            ("US-EN-Morgan-Freeman-Back-Door-Closed.wav", "Back Door Closed"),
            ("US-EN-Morgan-Freeman-Back-Door-Locked.wav", "Back Door Locked"),
            ("US-EN-Morgan-Freeman-Back-Door-Opened.wav", "Back Door Opened"),
            ("US-EN-Morgan-Freeman-Back-Door-Unlocked.wav", "Back Door Unlocked"),
            ("US-EN-Morgan-Freeman-Basement-Door-Closed.wav", "Basement Door Closed"),
            ("US-EN-Morgan-Freeman-Basement-Door-Locked.wav", "Basement Door Locked"),
            ("US-EN-Morgan-Freeman-Basement-Door-Opened.wav", "Basement Door Opened"),
            ("US-EN-Morgan-Freeman-Basement-Door-Unlocked.wav", "Basement Door Unlocked"),
            ("US-EN-Morgan-Freeman-Boss-Is-Arriving.wav", "Boss Is Arriving"),
            ("US-EN-Morgan-Freeman-Boyfriend-Is-Arriving.wav", "Boyfriend Is Arriving"),
            ("US-EN-Morgan-Freeman-Cleaning-Supplies-Closet-Opened.wav", "Cleaning Supplies Closet Opened"),
            ("US-EN-Morgan-Freeman-Coworker-Is-Arriving.wav", "Coworker Is Arriving"),
            ("US-EN-Morgan-Freeman-Daughter-Is-Arriving.wav", "Daughter Is Arriving"),
            ("US-EN-Morgan-Freeman-Friend-Is-Arriving.wav", "Friend Is Arriving"),
            ("US-EN-Morgan-Freeman-Front-Door-Closed.wav", "Front Door Closed"),
            ("US-EN-Morgan-Freeman-Front-Door-Locked.wav", "Front Door Locked"),
            ("US-EN-Morgan-Freeman-Front-Door-Opened.wav", "Front Door Opened"),
            ("US-EN-Morgan-Freeman-Front-Door-Unlocked.wav", "Front Door Unlocked"),
            ("US-EN-Morgan-Freeman-Garage-Door-Closed.wav", "Garage Door Closed"),
            ("US-EN-Morgan-Freeman-Garage-Door-Opened.wav", "Garage Door Opened"),
            ("US-EN-Morgan-Freeman-Girlfriend-Is-Arriving.wav", "Girlfriend Is Arriving"),
            ("US-EN-Morgan-Freeman-Good-Morning.wav", "Good Morning"),
            ("US-EN-Morgan-Freeman-Good-Night.wav", "Good Night"),
            ("US-EN-Morgan-Freeman-Liquor-Cabinet-Opened.wav", "Liquor Cabinet Opened"),
            ("US-EN-Morgan-Freeman-Motion-Detected.wav", "Motion Detected"),
            ("US-EN-Morgan-Freeman-Motion-In-Basement.wav", "Motion In Basement"),
            ("US-EN-Morgan-Freeman-Motion-In-Bedroom.wav", "Motion In Bedroom"),
            ("US-EN-Morgan-Freeman-Motion-In-Game-Room.wav", "Motion In Game Room"),
            ("US-EN-Morgan-Freeman-Motion-In-Garage.wav", "Motion In Garage"),
            ("US-EN-Morgan-Freeman-Motion-In-Kitchen.wav", "Motion In Kitchen"),
            ("US-EN-Morgan-Freeman-Motion-In-Living-Room.wav", "Motion In Living Room"),
            ("US-EN-Morgan-Freeman-Motion-In-Theater.wav", "Motion In Theater"),
            ("US-EN-Morgan-Freeman-Motion-In-Wine-Cellar.wav", "Motion In Wine Cellar"),
            ("US-EN-Morgan-Freeman-Patio-Door-Closed.wav", "Patio Door Closed"),
            ("US-EN-Morgan-Freeman-Patio-Door-Locked.wav", "Patio Door Locked"),
            ("US-EN-Morgan-Freeman-Patio-Door-Opened.wav", "Patio Door Opened"),
            ("US-EN-Morgan-Freeman-Patio-Door-Unlocked.wav", "Patio Door Unlocked"),
            ("US-EN-Morgan-Freeman-Roommate-Is-Arriving.wav", "Roommate Is Arriving"),
            ("US-EN-Morgan-Freeman-Searching-For-Car-Keys.wav", "Searching For Car Keys"),
            ("US-EN-Morgan-Freeman-Setting-The-Mood.wav", "Setting The Mood"),
            ("US-EN-Morgan-Freeman-Smartthings-Detected-A-Flood.wav", "SmartThings Detected A Flood"),
            ("US-EN-Morgan-Freeman-Smartthings-Detected-Carbon-Monoxide.wav", "SmartThings Detected Carbon Monoxide"),
            ("US-EN-Morgan-Freeman-Smartthings-Detected-Smoke.wav", "SmartThings Detected Smoke"),
            ("US-EN-Morgan-Freeman-Smoke-Detected-In-Basement.wav", "Smoke Detected In Basement"),
            ("US-EN-Morgan-Freeman-Smoke-Detected-In-Garage.wav", "Smoke Detected In Garage"),
            ("US-EN-Morgan-Freeman-Smoke-Detected-In-Kitchen.wav", "Smoke Detected In Kitchen"),
            ("US-EN-Morgan-Freeman-Someone-Is-Arriving.wav", "Someone Is Arriving"),
            ("US-EN-Morgan-Freeman-Son-Is-Arriving.wav", "Son Is Arriving"),
            ("US-EN-Morgan-Freeman-Starting-Movie-Mode.wav", "Starting Movie Mode"),
            ("US-EN-Morgan-Freeman-Starting-Party-Mode.wav", "Starting Party Mode"),
            ("US-EN-Morgan-Freeman-Starting-Romance-Mode.wav", "Starting Romance Mode"),
            ("US-EN-Morgan-Freeman-Turning-Off-All-The-Lights.wav", "Turning Off All The Lights"),
            ("US-EN-Morgan-Freeman-Turning-Off-The-Air-Conditioner.wav", "Turning Off The Air Conditioner"),
            ("US-EN-Morgan-Freeman-Turning-Off-The-Bar-Lights.wav", "Turning Off The Bar Lights"),
            ("US-EN-Morgan-Freeman-Turning-Off-The-Chandelier.wav", "Turning Off The Chandelier"),
            ("US-EN-Morgan-Freeman-Turning-Off-The-Family-Room-Lights.wav", "Turning Off The Family Room Lights"),
            ("US-EN-Morgan-Freeman-Turning-Off-The-Hallway-Lights.wav", "Turning Off The Hallway Lights"),
            ("US-EN-Morgan-Freeman-Turning-Off-The-Kitchen-Light.wav", "Turning Off The Kitchen Light"),
            ("US-EN-Morgan-Freeman-Turning-Off-The-Light.wav", "Turning Off The Light"),
            ("US-EN-Morgan-Freeman-Turning-Off-The-Lights.wav", "Turning Off The Lights"),
            ("US-EN-Morgan-Freeman-Turning-Off-The-Mood-Lights.wav", "Turning Off The Mood Lights"),
            ("US-EN-Morgan-Freeman-Turning-Off-The-TV.wav", "Turning Off The TV"),
            ("US-EN-Morgan-Freeman-Turning-On-The-Air-Conditioner.wav", "Turning On The Air Conditioner"),
            ("US-EN-Morgan-Freeman-Turning-On-The-Bar-Lights.wav", "Turning On The Bar Lights"),
            ("US-EN-Morgan-Freeman-Turning-On-The-Chandelier.wav", "Turning On The Chandelier"),
            ("US-EN-Morgan-Freeman-Turning-On-The-Family-Room-Lights.wav", "Turning On The Family Room Lights"),
            ("US-EN-Morgan-Freeman-Turning-On-The-Hallway-Lights.wav", "Turning On The Hallway Lights"),
            ("US-EN-Morgan-Freeman-Turning-On-The-Kitchen-Light.wav", "Turning On The Kitchen Light"),
            ("US-EN-Morgan-Freeman-Turning-On-The-Light.wav", "Turning On The Light"),
            ("US-EN-Morgan-Freeman-Turning-On-The-Lights.wav", "Turning On The Lights"),
            ("US-EN-Morgan-Freeman-Turning-On-The-Mood-Lights.wav", "Turning On The Mood Lights"),
            ("US-EN-Morgan-Freeman-Turning-On-The-TV.wav", "Turning On The TV"),
            ("US-EN-Morgan-Freeman-Vacate-The-Premises.wav", "Vacate The Premises"),
            ("US-EN-Morgan-Freeman-Water-Detected-In-Basement.wav", "Water Detected In Basement"),
            ("US-EN-Morgan-Freeman-Water-Detected-In-Garage.wav", "Water Detected In Garage"),
            ("US-EN-Morgan-Freeman-Water-Detected-In-Kitchen.wav", "Water Detected In Kitchen"),
            ("US-EN-Morgan-Freeman-Welcome-Home.wav", "Welcome Home"),
            ("US-EN-Morgan-Freeman-Wife-Is-Arriving.wav", "Wife Is Arriving"),
        ],
    ),
]


def catalog_for_ws() -> list[dict]:
    """Return the sound catalog in a JSON-friendly shape for the panel."""
    return [
        {
            "group": group,
            "sounds": [{"value": filename, "label": label} for filename, label in items],
        }
        for group, items in IOS_SOUND_GROUPS
    ]
