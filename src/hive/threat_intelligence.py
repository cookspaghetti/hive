"""Bounded, evidence-safe OSINT enrichment for extracted HIVE indicators.

Providers return a shared observation shape.  Raw third-party responses are not
persisted; HIVE keeps only the fields needed to explain a finding plus a digest
of the response that was observed at query time.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

import httpx

from hive.state import HVI, Message, SessionState

_PROVIDER_LABELS = {
    "semak_mule": "Semak Mule",
    "virus_total": "VirusTotal",
    "abuse_ipdb": "AbuseIPDB",
    "rdap": "RDAP",
}
_TTLS = {
    "semak_mule": 24 * 60 * 60,
    "virus_total": 24 * 60 * 60,
    "abuse_ipdb": 6 * 60 * 60,
    "rdap": 7 * 24 * 60 * 60,
}
_DIGICERT_G2_INTERMEDIATE = (
    Path(__file__).with_name("certs")
    / "digicert_global_g2_tls_rsa_sha256_2020_ca1.crt"
)


def _verified_ssl_context() -> ssl.SSLContext:
    """Build verified TLS context with Semak Mule's omitted intermediate."""
    context = ssl.create_default_context()
    if _DIGICERT_G2_INTERMEDIATE.is_file():
        context.load_verify_locations(cafile=str(_DIGICERT_G2_INTERMEDIATE))
    return context


class ThreatIntelCache(Protocol):
    def get(self, key: str, now: float) -> dict[str, Any] | None: ...

    def put(self, key: str, value: dict[str, Any]) -> None: ...


class FileThreatIntelCache:
    """Small atomic cache persisted beneath the evidence directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def get(self, key: str, now: float) -> dict[str, Any] | None:
        path = self._path(key)
        with self._lock:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                return None
        if float(value.get("expires_ts") or 0) <= now:
            return None
        value["cached"] = True
        return value

    def put(self, key: str, value: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        temporary = path.with_suffix(".json.tmp")
        payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        with self._lock:
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(path)


def _response_digest(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _host(value: str) -> str:
    parsed = urlsplit(value if "://" in value else f"https://{value}")
    return (parsed.hostname or "").lower().removeprefix("www.")


def _canonical_url(value: str) -> str:
    parsed = urlsplit(value if "://" in value else f"https://{value}")
    scheme = parsed.scheme.lower() if parsed.scheme.lower() in {"http", "https"} else "https"
    host = (parsed.hostname or "").lower()
    if not host:
        return value.strip()
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or "/"
    # Query strings frequently contain tokens and personal identifiers.  Domain
    # and path retain enough specificity for reputation lookups without sending them.
    return f"{scheme}://{host}{port}{path}"


def _observation(
    provider: str,
    indicator_kind: str,
    observable: str,
    source_msg_id: int | None,
    *,
    status: str,
    risk: str,
    summary: str,
    facts: dict[str, Any] | None = None,
    source_url: str,
    payload: Any = None,
    checked_ts: float | None = None,
) -> dict[str, Any]:
    checked = time.time() if checked_ts is None else checked_ts
    identity = f"{provider}|{indicator_kind}|{observable}"
    return {
        "id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
        "provider": provider,
        "provider_label": _PROVIDER_LABELS[provider],
        "indicator_kind": indicator_kind,
        "observable": observable,
        "source_msg_id": source_msg_id,
        "status": status,
        "risk": risk,
        "summary": summary,
        "facts": facts or {},
        "checked_ts": checked,
        "expires_ts": checked + _TTLS[provider],
        "cached": False,
        "source_url": source_url,
        "raw_response_sha256": _response_digest(payload) if payload is not None else None,
    }


class SemakMuleProvider:
    name = "semak_mule"

    def __init__(self, client: httpx.Client) -> None:
        self.client = client
        self.endpoint = "https://semakmule.rmp.gov.my/api/mule/get_search_data.php"

    def check(self, kind: str, value: str, source_msg_id: int | None) -> dict[str, Any]:
        digits = "".join(character for character in value if character.isdigit())
        category = "bank" if kind == "bank_account" else "telefon"
        data = {
            "category": category,
            "bankAccount": digits if category == "bank" else "",
            "telNo": digits if category == "telefon" else "",
            "companyName": "",
            "captcha": "",
        }
        response = self.client.post(
            self.endpoint,
            json={"data": data},
        )
        response.raise_for_status()
        payload = response.json()
        if int(payload.get("status") or 0) != 1:
            raise ValueError(str(payload.get("status_message") or "Semak Mule rejected the query"))
        rows = payload.get("table_data") if isinstance(payload.get("table_data"), list) else []
        reports = sum(
            int(row[-1])
            for row in rows
            if isinstance(row, list) and row and str(row[-1]).isdigit()
        )
        hit = bool(rows)
        return _observation(
            self.name,
            kind,
            digits,
            source_msg_id,
            status="hit" if hit else "no_hit",
            risk="malicious" if hit else "unknown",
            summary=(
                f"PDRM records show {reports} report{'s' if reports != 1 else ''} for this value."
                if hit
                else "No matching PDRM record was returned; this does not establish safety."
            ),
            facts={"police_reports": reports, "matched_rows": len(rows)},
            source_url="https://semakmule.rmp.gov.my/semak",
            payload=payload,
        )


class VirusTotalProvider:
    name = "virus_total"

    def __init__(self, api_key: str, client: httpx.Client) -> None:
        self.api_key = api_key
        self.client = client
        self.base = "https://www.virustotal.com/api/v3"

    def check(self, kind: str, value: str, source_msg_id: int | None) -> dict[str, Any]:
        observable = _canonical_url(value) if kind == "url" else value.lower()
        if not self.api_key:
            return _observation(
                self.name,
                kind,
                observable,
                source_msg_id,
                status="not_configured",
                risk="unknown",
                summary="Add a VirusTotal API key in Setup to enable this check.",
                source_url="https://www.virustotal.com/",
            )
        if kind == "url":
            object_id = base64.urlsafe_b64encode(observable.encode()).decode().rstrip("=")
            url = f"{self.base}/urls/{object_id}"
        else:
            url = f"{self.base}/files/{quote(observable, safe='')}"
        response = self.client.get(url, headers={"x-apikey": self.api_key})
        if response.status_code == 404:
            return _observation(
                self.name,
                kind,
                observable,
                source_msg_id,
                status="no_hit",
                risk="unknown",
                summary="VirusTotal has no existing record for this observable.",
                source_url="https://www.virustotal.com/",
                payload={"status_code": 404},
            )
        response.raise_for_status()
        payload = response.json()
        attributes = ((payload.get("data") or {}).get("attributes") or {})
        stats = attributes.get("last_analysis_stats") or {}
        malicious = int(stats.get("malicious") or 0)
        suspicious = int(stats.get("suspicious") or 0)
        harmless = int(stats.get("harmless") or 0)
        undetected = int(stats.get("undetected") or 0)
        risk = "malicious" if malicious else "suspicious" if suspicious else "unknown"
        return _observation(
            self.name,
            kind,
            observable,
            source_msg_id,
            status="hit",
            risk=risk,
            summary=(
                f"{malicious} malicious and {suspicious} suspicious engine detections."
                if malicious or suspicious
                else (
                    "No participating engine marked this observable malicious "
                    "in the latest report."
                )
            ),
            facts={
                "malicious": malicious,
                "suspicious": suspicious,
                "harmless": harmless,
                "undetected": undetected,
                "reputation": int(attributes.get("reputation") or 0),
            },
            source_url="https://www.virustotal.com/",
            payload=payload,
        )


class AbuseIPDBProvider:
    name = "abuse_ipdb"

    def __init__(self, api_key: str, client: httpx.Client) -> None:
        self.api_key = api_key
        self.client = client
        self.endpoint = "https://api.abuseipdb.com/api/v2/check"

    def check(self, value: str, source_msg_id: int | None) -> dict[str, Any]:
        ip = str(ipaddress.ip_address(value))
        if not self.api_key:
            return _observation(
                self.name,
                "ip",
                ip,
                source_msg_id,
                status="not_configured",
                risk="unknown",
                summary="Add an AbuseIPDB API key in Setup to enable this check.",
                source_url="https://www.abuseipdb.com/",
            )
        response = self.client.get(
            self.endpoint,
            headers={"Key": self.api_key, "Accept": "application/json"},
            params={"ipAddress": ip, "maxAgeInDays": 90, "verbose": "true"},
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        score = int(data.get("abuseConfidenceScore") or 0)
        reports = int(data.get("totalReports") or 0)
        risk = "suspicious" if score >= 50 else "context" if reports else "unknown"
        return _observation(
            self.name,
            "ip",
            ip,
            source_msg_id,
            status="hit" if reports else "no_hit",
            risk=risk,
            summary=(
                f"{score}% abuse confidence from {reports} report{'s' if reports != 1 else ''}."
                if reports
                else "No abuse reports were returned for this IP in the selected window."
            ),
            facts={
                "abuse_confidence": score,
                "total_reports": reports,
                "last_reported_at": data.get("lastReportedAt"),
                "country_code": data.get("countryCode"),
                "isp": data.get("isp"),
                "usage_type": data.get("usageType"),
                "is_tor": bool(data.get("isTor")),
            },
            source_url=f"https://www.abuseipdb.com/check/{quote(ip, safe='')}",
            payload=payload,
        )


class RdapProvider:
    name = "rdap"

    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def check(self, value: str, source_msg_id: int | None) -> dict[str, Any]:
        domain = _host(value)
        if not domain or "." not in domain:
            raise ValueError("RDAP requires a valid public domain")
        response = self.client.get(f"https://rdap.org/domain/{quote(domain, safe='')}")
        if response.status_code == 404:
            return _observation(
                self.name,
                "domain",
                domain,
                source_msg_id,
                status="no_hit",
                risk="unknown",
                summary="No RDAP registration record was returned for this domain.",
                source_url=f"https://lookup.icann.org/en/lookup?name={quote(domain, safe='')}",
                payload={"status_code": 404},
            )
        response.raise_for_status()
        payload = response.json()
        events = payload.get("events") if isinstance(payload.get("events"), list) else []
        registered = next(
            (
                str(event.get("eventDate"))
                for event in events
                if event.get("eventAction") == "registration" and event.get("eventDate")
            ),
            "",
        )
        age_days: int | None = None
        if registered:
            try:
                created = datetime.fromisoformat(registered.replace("Z", "+00:00"))
                age_days = max(0, (datetime.now(UTC) - created.astimezone(UTC)).days)
            except ValueError:
                pass
        statuses = [str(item) for item in payload.get("status") or []]
        risk = "suspicious" if age_days is not None and age_days < 30 else "context"
        summary = (
            f"Domain registered {age_days} day{'s' if age_days != 1 else ''} ago."
            if age_days is not None
            else "Registration record found; creation age was unavailable."
        )
        return _observation(
            self.name,
            "domain",
            domain,
            source_msg_id,
            status="hit",
            risk=risk,
            summary=summary,
            facts={
                "registered_at": registered or None,
                "domain_age_days": age_days,
                "statuses": statuses[:12],
                "handle": payload.get("handle"),
            },
            source_url=f"https://lookup.icann.org/en/lookup?name={quote(domain, safe='')}",
            payload=payload,
        )


class ThreatIntelligenceService:
    """Run applicable providers concurrently and cache normalized observations."""

    def __init__(
        self,
        *,
        virus_total_api_key: str = "",
        abuse_ipdb_api_key: str = "",
        timeout_s: float = 5.0,
        cache: ThreatIntelCache | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.client = client or httpx.Client(
            timeout=max(1.0, timeout_s),
            follow_redirects=True,
            # Semak Mule omits an intermediate from its served chain. Add the
            # published DigiCert intermediate while retaining TLS verification.
            verify=_verified_ssl_context(),
            headers={"User-Agent": "HIVE/0.1 authorised anti-scam research"},
        )
        self.cache = cache
        self.providers = {
            "semak_mule": SemakMuleProvider(self.client),
            "virus_total": VirusTotalProvider(virus_total_api_key, self.client),
            "abuse_ipdb": AbuseIPDBProvider(abuse_ipdb_api_key, self.client),
            "rdap": RdapProvider(self.client),
        }
        self.configured = {
            "semak_mule": True,
            "virus_total": bool(virus_total_api_key),
            "abuse_ipdb": bool(abuse_ipdb_api_key),
            "rdap": True,
        }

    def _cached(self, key: str) -> dict[str, Any] | None:
        return self.cache.get(key, time.time()) if self.cache else None

    def _run(
        self,
        provider: str,
        kind: str,
        value: str,
        source_msg_id: int | None,
        bypass_cache: bool = False,
    ) -> dict[str, Any]:
        normalized = (
            _canonical_url(value)
            if kind == "url"
            else _host(value)
            if kind == "domain"
            else value.lower()
            if kind == "file_hash"
            else "".join(character for character in value if character.isdigit())
            if kind in {"bank_account", "phone"}
            else value
        )
        key = f"{provider}|{kind}|{normalized}"
        cached = None if bypass_cache else self._cached(key)
        if cached is not None:
            cached["source_msg_id"] = source_msg_id
            return cached
        try:
            if provider == "semak_mule":
                result = self.providers[provider].check(kind, value, source_msg_id)
            elif provider == "virus_total":
                result = self.providers[provider].check(kind, value, source_msg_id)
            elif provider == "abuse_ipdb":
                result = self.providers[provider].check(value, source_msg_id)
            else:
                result = self.providers[provider].check(value, source_msg_id)
        except Exception as exc:  # noqa: BLE001 - provider failures are observations
            result = _observation(
                provider,
                kind,
                normalized,
                source_msg_id,
                status="error",
                risk="unknown",
                summary=f"Provider check could not complete: {str(exc)[:240]}",
                source_url={
                    "semak_mule": "https://semakmule.rmp.gov.my/semak",
                    "virus_total": "https://www.virustotal.com/",
                    "abuse_ipdb": "https://www.abuseipdb.com/",
                    "rdap": "https://lookup.icann.org/",
                }[provider],
            )
            # Shorter retry window for transient failures and rate limits.
            result["expires_ts"] = result["checked_ts"] + 5 * 60
        if self.cache and result["status"] != "not_configured":
            self.cache.put(key, result)
        return result

    def enrich(
        self,
        session: SessionState,
        indicators: list[HVI] | None = None,
        messages: list[Message] | None = None,
        *,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        candidates = indicators if indicators is not None else session.hvis
        tasks: list[tuple[str, str, str, int | None]] = []
        for item in candidates:
            if item.kind in {"bank_account", "phone"}:
                tasks.append(("semak_mule", item.kind, item.value, item.source_msg_id))
            elif item.kind == "url":
                tasks.append(("virus_total", "url", item.value, item.source_msg_id))
                tasks.append(("rdap", "domain", item.value, item.source_msg_id))
                host = _host(item.value)
                matching = next(
                    (
                        result
                        for result in reversed(session.sandbox_results)
                        if _host(str(result.get("url") or "")) == host
                        and result.get("dest_ip")
                    ),
                    None,
                )
                if matching:
                    tasks.append(
                        (
                            "abuse_ipdb",
                            "ip",
                            str(matching["dest_ip"]),
                            item.source_msg_id,
                        )
                    )
        for message in messages or []:
            name = str(message.media_name or "").lower()
            mime = str(message.media_mime or "").lower()
            if message.media_sha256 and (
                name.endswith(".apk") or mime == "application/vnd.android.package-archive"
            ):
                tasks.append(
                    ("virus_total", "file_hash", message.media_sha256, message.msg_id)
                )

        existing = {
            (
                str(item.get("provider")),
                str(item.get("indicator_kind")),
                str(item.get("observable")),
            )
            for item in session.threat_intelligence
        }
        unique: dict[tuple[str, str, str], tuple[str, str, str, int | None]] = {}
        for task in tasks:
            provider, kind, value, _source = task
            observable = (
                _canonical_url(value)
                if kind == "url"
                else _host(value)
                if kind == "domain"
                else value.lower()
                if kind == "file_hash"
                else "".join(character for character in value if character.isdigit())
                if kind in {"bank_account", "phone"}
                else value
            )
            key = (provider, kind, observable)
            if force or key not in existing:
                unique[key] = task
        if not unique:
            return []

        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(6, len(unique))) as executor:
            futures = {
                executor.submit(self._run, *task, force): key
                for key, task in unique.items()
            }
            for future in as_completed(futures):
                results.append(future.result())
        results.sort(key=lambda item: (str(item["provider"]), str(item["observable"])))
        if force:
            replaced = {
                (
                    str(item.get("provider")),
                    str(item.get("indicator_kind")),
                    str(item.get("observable")),
                )
                for item in results
            }
            session.threat_intelligence = [
                item
                for item in session.threat_intelligence
                if (
                    str(item.get("provider")),
                    str(item.get("indicator_kind")),
                    str(item.get("observable")),
                )
                not in replaced
            ]
        session.threat_intelligence.extend(results)
        return results


class SyntheticThreatIntelligenceService(ThreatIntelligenceService):
    """Exercise OSINT routing in demos/evaluations without external queries.

    Synthetic account numbers, reserved domains, and inert APK hashes must not
    be submitted to public reputation services.  The inherited routing and
    de-duplication logic remains active, while this seam records a normalized,
    explicitly non-live observation for every eligible provider lookup.
    """

    def __init__(self) -> None:
        super().__init__(timeout_s=1.0)
        self.configured = {provider: True for provider in _PROVIDER_LABELS}

    def _run(
        self,
        provider: str,
        kind: str,
        value: str,
        source_msg_id: int | None,
        bypass_cache: bool = False,
    ) -> dict[str, Any]:
        del bypass_cache
        observable = (
            _canonical_url(value)
            if kind == "url"
            else _host(value)
            if kind == "domain"
            else value.lower()
            if kind == "file_hash"
            else "".join(character for character in value if character.isdigit())
            if kind in {"bank_account", "phone"}
            else value
        )
        return _observation(
            provider,
            kind,
            observable,
            source_msg_id,
            status="synthetic_fixture",
            risk="unknown",
            summary=(
                "Synthetic observable routed to this provider; external lookup "
                "was intentionally suppressed."
            ),
            facts={
                "external_lookup": False,
                "fixture": True,
                "purpose": "controlled_pipeline_evaluation",
            },
            source_url="",
            payload={"provider": provider, "kind": kind, "synthetic": True},
        )


def build_synthetic_threat_intelligence_service() -> SyntheticThreatIntelligenceService:
    """Return the no-network threat-intelligence seam for controlled runs."""
    return SyntheticThreatIntelligenceService()


def build_threat_intelligence_service(settings: Any) -> ThreatIntelligenceService:
    return ThreatIntelligenceService(
        virus_total_api_key=str(getattr(settings, "virustotal_api_key", "") or ""),
        abuse_ipdb_api_key=str(getattr(settings, "abuseipdb_api_key", "") or ""),
        timeout_s=float(getattr(settings, "threat_intel_timeout_s", 5.0)),
        cache=FileThreatIntelCache("evidence/threat_intelligence/cache"),
    )
