from hive.reporting import reporting_guidance, reporting_summary


def test_reporting_guidance_is_explicit_and_source_linked():
    guidance = reporting_guidance()

    assert guidance["automated_submission"] is False
    assert guidance["reviewed_date"] == "2026-08-22"
    assert "997" in guidance["steps"][0]["action"]
    assert guidance["steps"][0]["source_url"].startswith("https://www.rmp.gov.my/")
    assert any("police report" in step["action"] for step in guidance["steps"])
    assert "does not submit" in reporting_summary()
