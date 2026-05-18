"""Pure helpers for the Flip Multiple pyRevit command."""

MODE_WORK_PLANE = "work_plane"
MODE_FRONT_BACK = "front_back"
MODE_LEFT_RIGHT = "left_right"

MODE_LABELS = {
    MODE_WORK_PLANE: "Flip Work-plane",
    MODE_FRONT_BACK: "Flip Front/Back",
    MODE_LEFT_RIGHT: "Flip Left/Right",
}


def get_mode_label(mode_key):
    return MODE_LABELS.get(mode_key, str(mode_key or ""))


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
