import json
import time

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes

from hive.state import HVI, Message, SessionState
from hive.threat_intelligence import (
    _DIGICERT_G2_INTERMEDIATE,
    FileThreatIntelCache,
    ThreatIntelligenceService,
)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_semak_mule_intermediate_certificate_has_expected_fingerprint():
    certificate = x509.load_pem_x509_certificate(
        _DIGICERT_G2_INTERMEDIATE.read_bytes()
    )

    assert certificate.fingerprint(hashes.SHA256()).hex() == (
        "c8025f9fc65fdfc95b3ca8cc7867b9a587b5277973957917463fc813d0b625a9"
    )


def test_url_enrichment_combines_vt_abuseipdb_and_rdap_without_raw_payloads(tmp_path):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "www.virustotal.com":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "attributes": {
                            "last_analysis_stats": {
                                "malicious": 3,
                                "suspicious": 1,
                                "harmless": 5,
                                "undetected": 61,
                            },
                            "reputation": -20,
                        }
                    }
                },
            )
        if request.url.host == "api.abuseipdb.com":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "abuseConfidenceScore": 73,
                        "totalReports": 8,
                        "countryCode": "MY",
                        "isp": "Example Network",
                    }
                },
            )
        if request.url.host == "rdap.org":
            return httpx.Response(
                200,
                json={
                    "handle": "EXAMPLE-1",
                    "status": ["active"],
                    "events": [
                        {"eventAction": "registration", "eventDate": "2026-08-20T00:00:00Z"}
                    ],
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    service = ThreatIntelligenceService(
        virus_total_api_key="vt-key",
        abuse_ipdb_api_key="abuse-key",
        cache=FileThreatIntelCache(tmp_path),
        client=_client(handler),
    )
    session = SessionState(peer_id=1, persona="confused_elderly")
    session.hvis = [HVI("url", "https://Example.test/pay?token=secret", 10)]
    session.sandbox_results = [
        {"url": "https://example.test/pay?token=secret", "dest_ip": "8.8.8.8"}
    ]

    results = service.enrich(session)

    assert {item["provider"] for item in results} == {
        "virus_total",
        "abuse_ipdb",
        "rdap",
    }
    vt = next(item for item in results if item["provider"] == "virus_total")
    assert vt["observable"] == "https://example.test/pay"
    assert vt["risk"] == "malicious"
    assert vt["facts"]["malicious"] == 3
    assert vt["raw_response_sha256"]
    assert "data" not in vt
    assert all("secret" not in str(request.url) for request in requests)


def test_semak_mule_maps_pdrm_report_count_without_a_credential():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "status": 1,
                "table_header": ["No. Akaun", "Repot"],
                "table_data": [["512802774281", 52]],
                "count": 2252,
                "cat": 1,
                "kw": "512802774281",
            },
        )

    service = ThreatIntelligenceService(client=_client(handler))
    session = SessionState(peer_id=1, persona="confused_elderly")
    session.hvis = [HVI("bank_account", "5128 0277 4281", 7)]

    result = service.enrich(session)[0]

    assert result["provider"] == "semak_mule"
    assert result["status"] == "hit"
    assert result["risk"] == "malicious"
    assert result["facts"]["police_reports"] == 52
    assert "apikey" not in captured["headers"]
    assert captured["payload"]["data"]["bankAccount"] == "512802774281"


def test_apk_is_hash_lookup_only_and_unknown_file_is_not_uploaded():
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append((request.method, str(request.url)))
        return httpx.Response(404, json={"error": {"code": "NotFoundError"}})

    service = ThreatIntelligenceService(
        virus_total_api_key="vt-key",
        client=_client(handler),
    )
    session = SessionState(peer_id=1, persona="confused_elderly")
    apk = Message(
        role="stranger",
        text="install this",
        ts=time.time(),
        msg_id=12,
        media_kind="file",
        media_name="parcel.apk",
        media_mime="application/vnd.android.package-archive",
        media_sha256="a" * 64,
    )

    result = service.enrich(session, [], [apk])[0]

    assert result["status"] == "no_hit"
    assert methods == [
        ("GET", f"https://www.virustotal.com/api/v3/files/{'a' * 64}")
    ]


def test_cached_observation_is_reused_without_second_provider_call(tmp_path):
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"status": 1, "table_data": [], "count": 1, "cat": 2, "kw": "60123456789"},
        )

    service = ThreatIntelligenceService(
        client=_client(handler),
        cache=FileThreatIntelCache(tmp_path),
    )
    first = SessionState(peer_id=1, persona="confused_elderly")
    second = SessionState(peer_id=2, persona="confused_elderly")
    first.hvis = [HVI("phone", "+60 12-345 6789", 1)]
    second.hvis = [HVI("phone", "+60 12-345 6789", 9)]

    service.enrich(first)
    cached = service.enrich(second)[0]

    assert calls == 1
    assert cached["cached"] is True
    assert cached["source_msg_id"] == 9


def test_operator_force_refresh_bypasses_cached_provider_result(tmp_path):
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"status": 1, "table_data": [], "count": calls, "cat": 1},
        )

    service = ThreatIntelligenceService(
        client=_client(handler),
        cache=FileThreatIntelCache(tmp_path),
    )
    session = SessionState(peer_id=1, persona="confused_elderly")
    session.hvis = [HVI("bank_account", "12345678", 1)]

    service.enrich(session)
    refreshed = service.enrich(session, force=True)

    assert calls == 2
    assert len(session.threat_intelligence) == 1
    assert refreshed[0]["cached"] is False


def test_provider_failure_is_recorded_as_unknown_instead_of_raising():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    service = ThreatIntelligenceService(client=_client(handler))
    session = SessionState(peer_id=1, persona="confused_elderly")
    session.hvis = [HVI("bank_account", "12345678", 1)]

    result = service.enrich(session)[0]

    assert result["status"] == "error"
    assert result["risk"] == "unknown"
    assert "offline" in result["summary"]
