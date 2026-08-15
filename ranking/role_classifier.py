"""Deterministic engineering role-family classification."""

from __future__ import annotations

import re
from dataclasses import dataclass

from models.job import Job


ROLE_RULES: dict[str, tuple[tuple[str, int], ...]] = {
    "bsp_drivers": ((r"\bdevice drivers?\b", 8), (r"\bBSP\b|board support package", 9), (r"kernel module", 7), (r"bootloader", 3)),
    "embedded_linux": ((r"embedded linux", 9), (r"\bYocto\b|Buildroot", 6), (r"Linux kernel", 5), (r"device tree", 5)),
    "embedded_firmware": ((r"embedded (?:software|firmware)", 8), (r"\bfirmware\b", 6), (r"\bMCU\b|microcontroller", 6), (r"\bRTOS\b|real[- ]time operating", 5), (r"bare metal", 5), (r"\bSPI\b|\bI2C\b|\bUART\b|\bCAN bus\b", 2)),
    "firmware": ((r"\bfirmware\b", 7), (r"low[- ]level software", 3)),
    "kernel": ((r"\bkernel engineer", 9), (r"kernel development", 7), (r"kernel internals", 6)),
    "controls_motor_control": ((r"motor control", 10), (r"control systems?", 7), (r"PID control", 6), (r"field oriented control|\bFOC\b", 7)),
    "asic_design_verification": ((r"design verification", 10), (r"\bUVM\b", 7), (r"SystemVerilog", 5), (r"functional coverage|constrained random|scoreboard", 5)),
    "asic_physical_design": ((r"physical design", 10), (r"place and route|static timing analysis|\bSTA\b", 6)),
    "asic_dft": ((r"design for test|\bDFT\b", 10), (r"scan insertion|ATPG", 7)),
    "formal_verification": ((r"formal verification", 10), (r"model checking|formal property", 7)),
    "post_silicon_validation": ((r"post[- ]silicon", 10), (r"silicon validation", 8)),
    "asic_rtl": ((r"RTL design|RTL engineer", 10), (r"\bASIC\b", 4), (r"Verilog|SystemVerilog", 3), (r"synthesis", 3)),
    "fpga": ((r"\bFPGA\b", 10), (r"Vivado|Quartus", 5), (r"programmable logic", 5)),
    "hardware_test_validation": ((r"hardware (?:test|validation)", 10), (r"validation engineer", 6), (r"oscilloscope|logic analyzer|JTAG", 2)),
    "computer_vision_ml": ((r"computer vision", 10), (r"machine learning|deep learning", 6), (r"PyTorch|TensorFlow", 4)),
    "systems": ((r"systems (?:software|engineer|programming)", 7), (r"distributed systems", 5), (r"operating systems", 4)),
    "general_software": ((r"software engineer|software developer", 5), (r"\bPython\b|\bJava\b|C\+\+", 1)),
}

CONTRADICTIONS = {
    "general_software": (r"firmware|embedded|FPGA|RTL|ASIC|device driver",),
    "asic_rtl": (r"verification only|software only",),
}


@dataclass(frozen=True)
class RoleClassification:
    role_family: str
    role_subfamily: str | None
    evidence: tuple[str, ...]
    scores: dict[str, int]


def classify_role(job: Job) -> RoleClassification:
    title, body = job.title, job.description
    scores: dict[str, int] = {}
    evidence: dict[str, list[str]] = {}
    for family, rules in ROLE_RULES.items():
        total = 0
        hits: list[str] = []
        for pattern, weight in rules:
            title_hits = len(re.findall(pattern, title, re.I))
            body_hits = len(re.findall(pattern, body, re.I))
            if title_hits or body_hits:
                total += weight * (2 if title_hits else 1) + min(body_hits, 2)
                hits.append(pattern)
        for pattern in CONTRADICTIONS.get(family, ()):
            if re.search(pattern, f"{title} {body}", re.I):
                total -= 8
        scores[family] = max(0, total)
        evidence[family] = hits
    # An embedded-software title is refined by concrete platform work in the
    # description. These are cross-field signals, not company/title special cases.
    if re.search(r"embedded software", title, re.I):
        if re.search(r"\b(?:device drivers?|BSP|board support package|kernel modules?)\b", body, re.I):
            scores["bsp_drivers"] += 10
        elif re.search(r"\bLinux\b|\bJetson\b|\bYocto\b|\bBuildroot\b", body, re.I):
            scores["embedded_linux"] += 10
    family = max(scores, key=scores.get)
    ordered = sorted(scores.values(), reverse=True)
    # A weak isolated body keyword is not enough to classify the entire role.
    if scores[family] < 6 or (scores[family] == ordered[1] and not re.search("|".join(p for p, _ in ROLE_RULES[family]), title, re.I)):
        family = "other"
    subfamily = "bsp_drivers" if family == "embedded_linux" and scores["bsp_drivers"] >= 6 else None
    return RoleClassification(family, subfamily, tuple(evidence.get(family, ())), scores)
