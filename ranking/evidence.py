"""Map job requirements to candidate-provided evidence without inventing gaps."""

from __future__ import annotations

import re
from typing import Any

ALIASES = {
    "c": ("embedded c", "c programming", "c language"), "systemverilog": ("sv",),
    "can": ("can bus",), "rtos": ("real-time operating system", "real time operating system"),
    "i2c": ("i²c",), "device drivers": ("device driver", "linux drivers"),
    "verilog": ("rtl verilog",), "linux": ("embedded linux",),
}
RELATED = {
    "linux": ("yocto", "buildroot"),
    "fpga": ("programmable logic", "vivado", "quartus"),
    "verilog": ("systemverilog",),
}
KNOWN_REQUIREMENTS = ("C", "C++", "SystemVerilog", "CAN", "RTOS", "SPI", "I2C", "UART", "Linux",
                      "device drivers", "Yocto", "UVM", "SVA", "RTL", "FPGA", "Verilog", "Python")


def _terms(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    if isinstance(value, str):
        result.append((value, prefix or value))
    elif isinstance(value, list):
        for item in value:
            result.extend(_terms(item, prefix))
    elif isinstance(value, dict):
        label = str(value.get("name") or value.get("title") or prefix)
        for key, item in value.items():
            if key not in {"description"}:
                result.extend(_terms(item, label))
        if value.get("description"):
            result.append((str(value["description"]), label or str(value["description"])))
    return result


def _matches(requirement: str, text: str) -> bool:
    options = (requirement, *ALIASES.get(requirement.lower(), ()))
    return any(re.search(rf"(?<!\w){re.escape(option)}(?!\w)", text, re.I) for option in options)


def extract_requirements(title: str, description: str, explicit: list[str] | None = None) -> list[str]:
    found = list(explicit or [])
    for requirement in KNOWN_REQUIREMENTS:
        if _matches(requirement, f"{title} {description}") and requirement not in found:
            found.append(requirement)
    return found


def map_evidence(requirements: list[str], profile: dict[str, Any]) -> list[dict[str, str]]:
    expert = _terms(profile.get("expert_skills", []))
    developing = _terms(profile.get("developing_skills", []))
    projects = _terms(profile.get("projects_and_technologies", []))
    degree = _terms(profile.get("degree", []), "degree")
    output = []
    for requirement in requirements:
        state, evidence = "no_profile_evidence", ""
        for candidates, candidate_state in ((expert, "strong_evidence"), (projects, "strong_evidence"),
                                             (developing, "developing"), (degree, "related_evidence")):
            hit = next(((text, label) for text, label in candidates if _matches(requirement, text)), None)
            if hit:
                state, evidence = candidate_state, hit[1] or hit[0]
                break
        if state == "no_profile_evidence":
            # Related evidence must be an explicit semantic relationship. Raw
            # substring containment made one-letter skills (notably C) evidence
            # for I2C, CAN, and "device drivers".
            related = next(((text, label) for text, label in expert + projects
                            if any(_matches(term, text) for term in RELATED.get(requirement.lower(), ()))), None)
            if related:
                state, evidence = "related_evidence", related[1] or related[0]
        output.append({"requirement": requirement, "state": state, "evidence": evidence})
    return output
