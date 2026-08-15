from ranking.analytics import historical_conversion, summarize
from ranking.prep import prep_topics


def test_analytics_counts_rates_and_small_sample():
    rows = [{"status": "applied", "role_family": "firmware"}, {"status": "rejected", "role_family": "firmware"},
            {"status": "technical interview", "role_family": "fpga"}, {"status": "offer", "role_family": "fpga"}]
    data = summarize(rows)
    assert data["total"] == 4 and data["pending"] == 1 and data["rejected"] == 1
    assert data["interview_rate"] == 50 and data["small_sample"]


def test_historical_conversion_is_neutral_for_tiny_samples_and_smoothed():
    tiny = [{"status": "rejected", "role_family": "firmware"}] * 4
    assert historical_conversion(tiny, "firmware") is None
    enough = tiny + [{"status": "technical interview", "role_family": "firmware"}]
    value = historical_conversion(enough, "firmware")
    assert value is not None and 20 < value < 50


def test_embedded_and_asic_prep_exclude_irrelevant_topics():
    embedded = prep_topics("embedded_firmware", "Firmware Engineer", "C RTOS SPI debugging")
    assert "SPI" in embedded and "UVM" not in embedded
    asic = prep_topics("asic_design_verification", "DV Engineer", "SystemVerilog UVM functional coverage")
    assert "UVM" in asic and "RTOS scheduling" not in asic
