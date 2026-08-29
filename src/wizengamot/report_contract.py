from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


EXTERNAL_SOURCE_CLASSES = {"external-primary", "external-secondary"}
GENERIC_SOURCE_ID_RE = re.compile(
    r"^(?:[a-z]+[-_]?)?\d+[a-z]?$",
    re.IGNORECASE,
)
NON_ATOMIC_EXTERNAL_SOURCE_RE = re.compile(
    r"\b(?:aggregat(?:ed|or)|multiple|several|unspecified|various)\b|"
    r"\b(?:industry|secondary|search[- ]result) (?:articles?|coverage|summar(?:y|ies))\b",
    re.IGNORECASE,
)
NON_IDENTIFYING_SOURCE_RE = re.compile(
    r"^(?:internet|online|search|search results?|unknown|web)$",
    re.IGNORECASE,
)
NON_IDENTIFYING_LOCATOR_RE = re.compile(
    r"\b(?:aggregat(?:ed|or)|article body|coverage|full (?:document|text)|homepage|"
    r"multiple|not (?:directly )?(?:fetched|retrieved|reviewed)|search[- ]results?|"
    r"secondary summar(?:y|ies)|unspecified|various)\b",
    re.IGNORECASE,
)
URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
DOI_RE = re.compile(r"^(?:doi:\s*)?10\.\d{4,9}/\S+$", re.IGNORECASE)
FORMAL_LOCATOR_RE = re.compile(
    r"(?:§|\b(?:article|chapter|document|page|pages|paragraph|rule|rules|section|sections)\b)",
    re.IGNORECASE,
)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _string_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _external_locator_is_identifying(locator: str, source: str | None) -> bool:
    if NON_IDENTIFYING_LOCATOR_RE.search(locator):
        return False
    return bool(
        URL_RE.fullmatch(locator)
        or DOI_RE.fullmatch(locator)
        or FORMAL_LOCATOR_RE.search(locator)
        or (source is not None and (URL_RE.fullmatch(source) or DOI_RE.fullmatch(source)))
    )


def validate_report_contract(report: dict[str, Any]) -> list[str]:
    """Return deterministic provenance violations for a schema-shaped report.

    JSON Schema remains responsible for structural validation. This validator
    enforces cross-record and semantic provenance invariants that structured
    output schemas cannot express reliably.
    """

    errors: list[str] = []
    citations_value = report.get("citations")
    citations = citations_value if isinstance(citations_value, list) else []
    citation_by_id: dict[str, dict[str, Any]] = {}
    source_id_indexes: dict[str, int] = {}
    locator_indexes: dict[str, int] = {}

    for index, value in enumerate(citations):
        if not isinstance(value, dict):
            continue
        citation = value
        path = f"citations[{index}]"
        source_id = _text(citation.get("source_id"))
        source_class = citation.get("source_class")
        publisher = _text(citation.get("publisher"))
        source = _text(citation.get("source"))
        locator = _text(citation.get("locator"))

        if source_id is not None:
            if source_id in source_id_indexes:
                errors.append(
                    f"{path}.source_id: duplicate source_id {source_id!r}; "
                    f"first used by citations[{source_id_indexes[source_id]}]"
                )
            else:
                source_id_indexes[source_id] = index
                citation_by_id[source_id] = citation

        if source_class not in EXTERNAL_SOURCE_CLASSES:
            continue

        if locator is not None:
            if locator in locator_indexes:
                errors.append(
                    f"{path}.locator: duplicate locator {locator!r}; "
                    f"first used by citations[{locator_indexes[locator]}]"
                )
            else:
                locator_indexes[locator] = index

        if source_id is None:
            errors.append(f"{path}.source_id: external citation requires a source_id")
        elif GENERIC_SOURCE_ID_RE.fullmatch(source_id):
            errors.append(
                f"{path}.source_id: external source_id {source_id!r} is generic; "
                "use a descriptive stable identifier"
            )
        if publisher is None:
            errors.append(f"{path}.publisher: external citation requires a publisher")
        elif NON_ATOMIC_EXTERNAL_SOURCE_RE.search(publisher):
            errors.append(
                f"{path}.publisher: external citation must identify one publisher, "
                "not bundled or unspecified material"
            )
        if source is None:
            errors.append(f"{path}.source: external citation requires an identifiable source")
        elif (
            NON_ATOMIC_EXTERNAL_SOURCE_RE.search(source)
            or NON_IDENTIFYING_SOURCE_RE.fullmatch(source)
        ):
            errors.append(
                f"{path}.source: external citation must identify exactly one source, "
                "not bundled or unspecified material"
            )
        if locator is None:
            errors.append(f"{path}.locator: external citation requires a locator")
        elif not _external_locator_is_identifying(locator, source):
            errors.append(
                f"{path}.locator: external citation requires a direct URL, DOI, "
                "or precise formal document locator"
            )
        if source_class == "external-primary" and citation.get("primary") is not True:
            errors.append(f"{path}.primary: external-primary citation must set primary=true")
        if source_class == "external-secondary" and citation.get("primary") is not False:
            errors.append(f"{path}.primary: external-secondary citation must set primary=false")
        if not _string_ids(citation.get("claims_supported")):
            errors.append(f"{path}.claims_supported: external citation requires at least one claim")

    findings_value = report.get("findings")
    findings = findings_value if isinstance(findings_value, list) else []
    for index, value in enumerate(findings):
        if not isinstance(value, dict):
            continue
        finding = value
        path = f"findings[{index}]"
        claim_type = finding.get("claim_type")
        evidence_class = finding.get("evidence_class")
        source_ids = _string_ids(finding.get("source_ids"))
        reviewed_source_ids = _string_ids(finding.get("reviewed_source_ids"))

        if claim_type in {"negative-capability", "comparative"} and not reviewed_source_ids:
            errors.append(
                f"{path}.reviewed_source_ids: {claim_type} finding requires at least one reviewed source"
            )
        if finding.get("novelty") == "corroborated" and len(set(source_ids)) < 2:
            errors.append(f"{path}.source_ids: corroborated finding requires at least two source IDs")

        for source_id in source_ids:
            citation = citation_by_id.get(source_id)
            uses_external_namespace = source_id.lower().startswith(("ext-", "external-"))
            if citation is None and (evidence_class in EXTERNAL_SOURCE_CLASSES or uses_external_namespace):
                errors.append(
                    f"{path}.source_ids: external source_id {source_id!r} does not resolve to a citation"
                )
        for source_id in reviewed_source_ids:
            if (
                source_id.lower().startswith(("ext-", "external-"))
                and source_id not in citation_by_id
            ):
                errors.append(
                    f"{path}.reviewed_source_ids: external source_id {source_id!r} "
                    "does not resolve to a citation"
                )

        if evidence_class == "external-primary":
            primary_citations = [
                citation_by_id[source_id]
                for source_id in source_ids
                if source_id in citation_by_id
                and citation_by_id[source_id].get("source_class") == "external-primary"
                and citation_by_id[source_id].get("primary") is True
            ]
            if not primary_citations:
                errors.append(
                    f"{path}.evidence_class: external-primary finding must reference "
                    "at least one primary citation"
                )

    return _dedupe(errors)


def annotate_report_contract(payload: dict[str, Any]) -> list[str]:
    result = payload.get("result")
    report = result.get("report") if isinstance(result, dict) else None
    errors = validate_report_contract(report) if isinstance(report, dict) else []
    payload["report_contract_errors"] = errors
    return errors


def payload_contract_errors(payload: dict[str, Any]) -> list[str]:
    """Read saved errors and recompute them so old or edited payloads cannot bypass the gate."""

    result = payload.get("result")
    report = result.get("report") if isinstance(result, dict) else None
    computed = validate_report_contract(report) if isinstance(report, dict) else []
    stored = payload.get("report_contract_errors")
    if stored is None:
        return computed
    if not isinstance(stored, list) or not all(isinstance(error, str) for error in stored):
        return _dedupe([*computed, "report_contract_errors: expected an array of strings"])
    return _dedupe([*computed, *stored])
