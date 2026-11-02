"""Everything the shop 'knows'. Single source of truth: the availability logic AND the
agent's prompt both read from here.

!! All prices, hours and policies below are made-up placeholders for the exercise.
   Edit them to change the shop; nothing else needs to change."""
from dataclasses import dataclass
from datetime import time


@dataclass(frozen=True)
class Service:
    key: str
    name: str
    minutes: int
    price: int  # USD, starting price for a sedan
    blurb: str


SERVICES: dict[str, Service] = {
    s.key: s
    for s in [
        Service("express_wash", "Express Wash & Dry", 30, 35, "Exterior hand wash and dry."),
        Service("exterior_wash_wax", "Exterior Wash & Wax", 60, 90, "Hand wash, clay bar and hand wax."),
        Service("interior_detail_wash", "Interior Detail & Wash", 60, 140,
                "Full interior vacuum, wipe-down, steam clean and an exterior wash."),
        Service("full_detail", "Full Detail", 120, 240, "Interior detail plus exterior wash, clay and wax."),
    ]
}

BAYS = 1                  # how many cars can be worked on at the same time
SLOT_STEP_MIN = 30        # appointments start on the :00 and :30
MIN_NOTICE_MIN = 60       # can't book something starting in under an hour
LATE_GRACE_MIN = 15       # we hold the slot this long for a late customer
LARGE_VEHICLE_SURCHARGE = 20

# Monday=0 ... Sunday=6. A missing day means closed.
OPEN_HOURS: dict[int, tuple[time, time]] = {
    0: (time(9), time(18)),
    1: (time(9), time(18)),
    2: (time(9), time(18)),
    3: (time(9), time(18)),
    4: (time(9), time(18)),
    5: (time(9), time(16)),
}

PREP = [
    "Remove personal belongings and valuables from the car.",
    "Take child seats out of the car.",
    "Empty the trunk if you want it cleaned.",
    "Arrive on time; we hold your slot for 15 minutes.",
    "Heavily soiled interiors (mold, biohazards) may be declined or need a custom quote.",
]

VEHICLES_OK = "Sedans, coupes, hatchbacks, SUVs, pickup trucks and passenger vans/minivans."
VEHICLES_NOT_OK = "RVs/motorhomes, boats, motorcycles, buses, semi-trucks and commercial trucks."

# Things the agent must NOT resolve itself.
HANDOFF_TOPICS = [
    "complaints or disputes about a charge, refund requests",
    "custom quotes: ceramic coating, paint correction, anything not on the service list",
    "vehicles we're not sure we can do (anything outside the supported list)",
    "damage claims or a car that came back damaged",
    "callers who are angry, threatening, or asking for a manager",
]


def _t(t: time) -> str:
    return t.strftime("%-I:%M %p")


def hours_text() -> str:
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return "\n".join(
        f"- {d}: {_t(OPEN_HOURS[i][0])} to {_t(OPEN_HOURS[i][1])}" if i in OPEN_HOURS else f"- {d}: closed"
        for i, d in enumerate(days)
    )


def knowledge_base() -> str:
    services = "\n".join(
        f"- {s.name}: from ${s.price}, about {s.minutes} min. {s.blurb}" for s in SERVICES.values()
    )
    prep = "\n".join(f"- {p}" for p in PREP)
    return (
        f"SERVICES (starting prices are for a sedan; SUVs, trucks and vans add ${LARGE_VEHICLE_SURCHARGE}):\n{services}\n\n"
        f"HOURS (last appointment must finish before closing):\n{hours_text()}\n\n"
        f"PREP REQUIREMENTS:\n{prep}\n\n"
        f"VEHICLES WE DETAIL: {VEHICLES_OK}\nVEHICLES WE DON'T: {VEHICLES_NOT_OK}\n\n"
        f"LATE POLICY: we hold the slot for {LATE_GRACE_MIN} minutes past the start time.\n"
    )
