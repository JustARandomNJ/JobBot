"""Local deterministic interview-preparation topic banks."""

from __future__ import annotations

import re

EMBEDDED = {
    "C/C++": r"\bC\+*\b|firmware", "pointers": r"pointer|\bC\b", "memory layout": r"memory|embedded",
    "volatile": r"volatile|register|firmware", "bit operations": r"bitwise|register|firmware", "endianness": r"endian|protocol",
    "interrupts": r"interrupt|ISR|embedded", "concurrency": r"concurr|thread|RTOS", "mutexes/semaphores": r"mutex|semaphore|RTOS",
    "RTOS scheduling": r"RTOS|real[- ]time", "SPI": r"\bSPI\b", "I2C": r"\bI2C\b", "UART": r"\bUART\b", "CAN": r"\bCAN\b",
    "memory-mapped I/O": r"memory.mapped|register|firmware", "JTAG/GDB": r"JTAG|GDB|debug", "hardware debugging": r"debug|bring.?up",
    "bootloaders": r"bootloader", "device drivers": r"device driver|BSP", "embedded Linux": r"embedded linux|Yocto|device tree",
}
ASIC = {
    "Verilog": r"Verilog|RTL", "SystemVerilog": r"SystemVerilog|UVM|SVA", "blocking vs nonblocking": r"Verilog|RTL",
    "FSMs": r"FSM|state machine|RTL", "setup/hold": r"setup|hold|timing", "clock-domain crossing": r"clock.domain|CDC",
    "synchronizers": r"synchronizer|CDC", "RTL design": r"RTL|digital design", "testbenches": r"testbench|verification",
    "SVA": r"\bSVA\b|assertion", "UVM": r"\bUVM\b", "functional coverage": r"functional coverage",
    "constrained random": r"constrained random", "scoreboards": r"scoreboard|UVM", "synthesis": r"synthesis",
    "timing": r"timing|STA", "FPGA architecture": r"FPGA|Vivado|Quartus",
}


def prep_topics(role_family: str, title: str, description: str) -> list[str]:
    text = f"{title} {description}"
    banks = []
    if role_family in {"firmware", "embedded_firmware", "embedded_linux", "bsp_drivers", "kernel", "controls_motor_control"}: banks.append(EMBEDDED)
    if role_family in {"fpga", "asic_rtl", "asic_design_verification", "formal_verification", "asic_dft"}: banks.append(ASIC)
    return [topic for bank in banks for topic, pattern in bank.items() if re.search(pattern, text, re.I)]
