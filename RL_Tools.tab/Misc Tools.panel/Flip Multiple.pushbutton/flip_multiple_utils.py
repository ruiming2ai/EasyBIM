"""Pure helpers for the Flip Multiple pyRevit command."""

MODE_WORK_PLANE = "work_plane"
MODE_FRONT_BACK = "front_back"
MODE_LEFT_RIGHT = "left_right"
MODE_UP_DOWN = "up_down"

MODE_LABELS = {
    MODE_WORK_PLANE: "Flip Work-plane",
    MODE_FRONT_BACK: "Flip Front/Back",
    MODE_LEFT_RIGHT: "Flip Left/Right",
    MODE_UP_DOWN: "Flip Up/Down",
}
MODE_KEYS_IN_ORDER = (
    MODE_WORK_PLANE,
    MODE_FRONT_BACK,
    MODE_LEFT_RIGHT,
    MODE_UP_DOWN,
)
MODE_HELPER_TEXTS = {
    MODE_WORK_PLANE: (
        "Use the family instance's native work-plane flip. "
        "Only families that expose work-plane flip are compatible."
    ),
    MODE_FRONT_BACK: (
        "Use a geometric flip across a vertical plane through the instance origin, "
        "derived from the instance facing direction."
    ),
    MODE_LEFT_RIGHT: (
        "Use a geometric flip across a vertical plane through the instance origin, "
        "derived from the instance hand direction."
    ),
    MODE_UP_DOWN: (
        "Use a geometric flip across a horizontal plane through the instance origin, "
        "derived from the instance local vertical direction."
    ),
}
EMPTY_SELECTION_STATUS_TEXT = "No elements selected. Click Select to add elements from the model."


def get_mode_labels():
    return [MODE_LABELS[mode_key] for mode_key in MODE_KEYS_IN_ORDER]


def get_mode_label(mode_key):
    return MODE_LABELS.get(mode_key, str(mode_key or ""))


def get_mode_key_from_label(mode_label):
    normalized = str(mode_label or "").strip()
    for mode_key in MODE_KEYS_IN_ORDER:
        if MODE_LABELS.get(mode_key) == normalized:
            return mode_key
    return None


def get_mode_helper_text(mode_key):
    return MODE_HELPER_TEXTS.get(mode_key, "Choose one flip mode.")


def build_status_text(selection_count, status_text):
    explicit = str(status_text or "").strip()
    if explicit:
        return explicit
    if selection_count:
        return "Ready. {} element(s) selected for flipping.".format(int(selection_count))
    return EMPTY_SELECTION_STATUS_TEXT


def build_completion_message(mode_label, selected_count, flipped_count):
    del mode_label, selected_count, flipped_count
    return "Flip Multiple completed."


def collect_incompatible_type_labels(entries):
    labels = set()

    for entry in entries or []:
        family_name = str(entry.get("family_name") or "").strip()
        type_name = str(entry.get("type_name") or "").strip()
        label = str(entry.get("label") or "").strip()

        if family_name and type_name:
            labels.add("{} : {}".format(family_name, type_name))
        elif family_name:
            labels.add(family_name)
        elif type_name:
            labels.add(type_name)
        elif label:
            labels.add(label)
        else:
            labels.add("Unknown family type")

    return sorted(labels)


def build_incompatibility_message(
    mode_label,
    selected_count,
    incompatible_type_labels,
    non_family_count,
):
    lines = [
        "Flip Multiple cannot run.",
        "Mode: {}".format(mode_label),
        "Selected elements: {}".format(selected_count),
    ]

    if non_family_count:
        lines.append("Non-family-instance elements: {}".format(non_family_count))

    if incompatible_type_labels:
        lines.append("")
        lines.append("Incompatible family types:")
        lines.extend(incompatible_type_labels)

    if non_family_count and not incompatible_type_labels:
        lines.append("")
        lines.append("Remove non-family-instance elements from the selection and try again.")

    return "\n".join(lines)
