"""User-facing tracking switches.

The one list of settings: persisted in data/state.json under "settings", read each tick by tracker.Tracker through
ctx["settings"], rendered by the dashboard from INFO, and swept by lab.py. A new key needs its handling added in
tracker.py / radiomap.py / phone_filter.py in the same change.
"""

DEFAULTS = {
    "shadow_guard": True,
    "motion": True,
    "radio_motion": True,
    "furniture": True,
    "smoothing": True,
    "csi_motion": False,
    "robust_rssi": True,
    "pdr": False,
    "fuse_body": False,
    "holder_body": True,
    "device_free": False,
    "mark_anchor": True,
}

INFO = {
    "shadow_guard": (
        "Ignore body blocking",
        "When a board reads far weaker than the map predicts (a person standing in the way), it pulls the estimate "
        "less. Bodies can only weaken a signal, never strengthen it.",
    ),
    "motion": (
        "Use phone motion",
        "Standing still pins the position where you stopped; walking lets it move. Needs Motion enabled on the phone "
        "page. This is the single largest accuracy gain.",
    ),
    "radio_motion": (
        "Sense movement from the signal",
        "A moving phone's signal jitters and a still one's does not. Lets phones without Motion hold still when they "
        "stop, and notices slow walking that phone sensors miss.",
    ),
    "furniture": ("Furniture", "Nobody stands inside a bookshelf: scanned furniture makes those spots unlikely."),
    "smoothing": (
        "Smooth the dot",
        "Display only: glides the dot and keeps it steady when you stop. Accuracy numbers are unaffected.",
    ),
    "csi_motion": (
        "CSI motion (experimental)",
        "Guesses still/moving from how much the Wi-Fi channel shape changes, for phones without Motion. Unproven on "
        "real hardware.",
    ),
    "robust_rssi": (
        "Ignore signal fades",
        "Uses the upper quarter of each board's readings rather than the mean, so a brief deep fade (the signal "
        "cancelling itself) is not mistaken for distance.",
    ),
    "pdr": (
        "Steps + compass",
        "While walking, each step moves the dot the way the phone faces (the compass offset is learned from the "
        "walks between \"I'm here\" marks). Needs Motion enabled on the phone page.",
    ),
    "holder_body": (
        "Account for your own body",
        "Your body shadows the boards behind you. With the compass it knows which way you face; without it, every "
        "direction is considered.",
    ),
    "fuse_body": (
        "Body sensing helps phone",
        "Also uses how your body disturbs the board-to-board links. Only valid when you are alone in the room. Needs "
        "the empty-room recording and then some marks or a walk.",
    ),
    "device_free": (
        "Track without a phone (device-free)",
        "Finds one person carrying no phone from how they disturb the links between boards. Record the empty room "
        "first (phone page, More). Experimental.",
    ),
    "mark_anchor": (
        "Snap to \"I'm here\"",
        "When you mark where you stand on the phone page, your dot moves there and tracking carries on from that "
        "known spot.",
    ),
}

# Not swept by the phone-accuracy lab: display-only, or unrelated to phone tracking.
LAB_SKIP = {"smoothing", "device_free"}

# Settings that make the boards ping each other; luna_server puts sense=1 in LUNA_SUB when any is on.
SENSE_KEYS = ("fuse_body", "device_free")


def merged(stored) -> dict:
    out = dict(DEFAULTS)
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in DEFAULTS:
                out[key] = bool(value)
    return out
