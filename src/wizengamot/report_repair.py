from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from . import __version__
from .report_contract import (
    EXTERNAL_SOURCE_CLASSES,
    FORMAL_LOCATOR_RE,
    GENERIC_SOURCE_ID_RE,
    NON_ATOMIC_EXTERNAL_SOURCE_RE,
    NON_IDENTIFYING_LOCATOR_RE,
    payload_contract_errors,
    validate_report_contract,
)


TOOL_VERSION = f"wizengamot/{__version__}"
MARKDOWN_LINK_RE = re.compile(r"^\s*\[[^\]]+\]\((https?://[^\s)]+)\)\s*$", re.IGNORECASE)
RAW_URL_RE = re.compile(r"https?://[^\s<>\]]+", re.IGNORECASE)
DOI_RE = re.compile(r"^(?:doi:\s*)?(10\.\d{4,9}/\S+)$", re.IGNORECASE)
NON_REPAIRABLE_LOCATOR_RE = re.compile(
    r"^(?:article|article body|blog post|search summary|homepage|page content|"
    r"not independently retrieved)\.?$",
    re.IGNORECASE,
)
UNVERIFIED_LOCATOR_RE = re.compile(
    r"not independently (?:retrieved|reconfirmed|verified)",
    re.IGNORECASE,
)
SLUG_RE = re.compile(r"[^a-z0-9]+")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _slug(value: str | None, *, fallback: str, limit: int = 48) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = SLUG_RE.sub("-", ascii_value).strip("-")
    return (slug or fallback)[:limit].rstrip("-")


def _trim_url(value: str) -> str:
    value = value.rstrip(".,;:!?")
    while value.endswith(")") and value.count("(") < value.count(")"):
        value = value[:-1]
    return value


def _doi_value(value: str) -> str | None:
    match = DOI_RE.fullmatch(value.strip())
    if not match:
        return None
    return match.group(1).rstrip(".,;)").lower()


def _canonical_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value.strip())
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        return None
    host = hostname.lower().rstrip(".")
    if port is not None and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    path = parsed.path or ""
    if path == "/":
        path = ""
    return urlunsplit((parsed.scheme.lower(), host, path, parsed.query, ""))


def canonical_direct_locator(value: Any) -> tuple[str, str] | None:
    """Return a canonical identity key and display locator for a direct URL or DOI."""

    text = _text(value)
    if text is None:
        return None
    doi = _doi_value(text)
    if doi is not None:
        return f"doi:{doi}", f"doi:{doi}"
    url = _canonical_url(text)
    if url is None:
        return None
    parsed = urlsplit(url)
    if parsed.hostname and parsed.hostname.lower() in {"doi.org", "dx.doi.org"}:
        doi = _doi_value(parsed.path.lstrip("/"))
        if doi is not None:
            return f"doi:{doi}", url
    return f"url:{url}", url


def _normalized_locator(citation: dict[str, Any]) -> tuple[str, str] | None:
    locator = _text(citation.get("locator"))
    if locator is None:
        source = canonical_direct_locator(citation.get("source"))
        if source is not None:
            return source[1], "fill-locator-from-source"
        return None

    direct = canonical_direct_locator(locator)
    if direct is not None:
        return direct[1], "canonicalize-direct-locator"
    markdown = MARKDOWN_LINK_RE.fullmatch(locator)
    if markdown:
        direct = canonical_direct_locator(markdown.group(1))
        if direct is not None:
            return direct[1], "normalize-markdown-locator"
    if NON_REPAIRABLE_LOCATOR_RE.fullmatch(locator) or UNVERIFIED_LOCATOR_RE.search(locator):
        return None
    urls = [_trim_url(value) for value in RAW_URL_RE.findall(locator)]
    canonical_urls = [value for value in urls if canonical_direct_locator(value) is not None]
    if len(canonical_urls) == 1 and len(urls) == 1:
        canonical = canonical_direct_locator(canonical_urls[0])
        if canonical is not None:
            return canonical[1], "extract-single-url-locator"
    return None


def _audit(
    repairs: list[dict[str, Any]],
    *,
    field_path: str,
    old_value: Any,
    new_value: Any,
    rule: str,
    timestamp: str,
) -> None:
    if old_value == new_value:
        return
    repairs.append({
        "field_path": field_path,
        "old_value": copy.deepcopy(old_value),
        "new_value": copy.deepcopy(new_value),
        "rule": rule,
        "timestamp": timestamp,
        "tool_version": TOOL_VERSION,
    })


def _finding_references(report: dict[str, Any]) -> list[tuple[int, dict[str, Any], str]]:
    findings = report.get("findings")
    if not isinstance(findings, list):
        return []
    result: list[tuple[int, dict[str, Any], str]] = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue
        for field in ("source_ids", "reviewed_source_ids"):
            if isinstance(finding.get(field), list):
                result.append((index, finding, field))
    return result


def _remap_references(
    report: dict[str, Any],
    mapping: dict[str, str],
    repairs: list[dict[str, Any]],
    *,
    rule: str,
    timestamp: str,
) -> None:
    for index, finding, field in _finding_references(report):
        old = finding[field]
        new = _dedupe([
            mapping.get(value, value)
            for value in old
            if isinstance(value, str) and value.strip()
        ])
        if old != new:
            finding[field] = new
            _audit(
                repairs,
                field_path=f"findings[{index}].{field}",
                old_value=old,
                new_value=new,
                rule=rule,
                timestamp=timestamp,
            )


def _citation_score(citation: dict[str, Any], index: int) -> tuple[int, int, int, int, int]:
    source_id = _text(citation.get("source_id"))
    descriptive = int(source_id is not None and not GENERIC_SOURCE_ID_RE.fullmatch(source_id))
    source_class = citation.get("source_class")
    coherent = int(
        (source_class == "external-primary" and citation.get("primary") is True)
        or (source_class == "external-secondary" and citation.get("primary") is False)
    )
    primary = int(source_class == "external-primary" and citation.get("primary") is True)
    complete = sum(
        int(_text(citation.get(field)) is not None)
        for field in ("publisher", "source", "label", "date")
    )
    claims = len(citation.get("claims_supported", [])) if isinstance(citation.get("claims_supported"), list) else 0
    return coherent, primary, descriptive, complete + claims, -index


def _merge_duplicate_citations(
    report: dict[str, Any], repairs: list[dict[str, Any]], *, timestamp: str
) -> str | None:
    citations = report.get("citations")
    if not isinstance(citations, list):
        return None
    groups: dict[str, list[int]] = {}
    for index, citation in enumerate(citations):
        if not isinstance(citation, dict):
            continue
        canonical = canonical_direct_locator(citation.get("locator"))
        if canonical is not None:
            groups.setdefault(canonical[0], []).append(index)
    duplicate_groups = [indexes for indexes in groups.values() if len(indexes) > 1]
    if not duplicate_groups:
        return None

    old_citations = copy.deepcopy(citations)
    removed: set[int] = set()
    replacements: dict[int, dict[str, Any]] = {}
    mapping: dict[str, str] = {}
    for indexes in duplicate_groups:
        keep = max(indexes, key=lambda index: _citation_score(citations[index], index))
        retained = copy.deepcopy(citations[keep])
        retained_id = _text(retained.get("source_id"))
        claims: list[str] = []
        for index in indexes:
            citation = citations[index]
            values = citation.get("claims_supported")
            if isinstance(values, list):
                claims.extend(value.strip() for value in values if isinstance(value, str) and value.strip())
            source_id = _text(citation.get("source_id"))
            if index != keep and source_id is not None and retained_id is not None:
                previous = mapping.get(source_id)
                if previous is not None and previous != retained_id:
                    return f"source ID {source_id!r} maps to multiple retained citations"
                mapping[source_id] = retained_id
            if index != keep:
                removed.add(index)
        retained["claims_supported"] = _dedupe(claims)
        replacements[keep] = retained

    new_citations = [
        replacements.get(index, citation)
        for index, citation in enumerate(citations)
        if index not in removed
    ]
    report["citations"] = new_citations
    _audit(
        repairs,
        field_path="citations",
        old_value=old_citations,
        new_value=new_citations,
        rule="merge-duplicate-direct-citations",
        timestamp=timestamp,
    )
    _remap_references(
        report,
        mapping,
        repairs,
        rule="merge-duplicate-direct-citations",
        timestamp=timestamp,
    )
    return None


def _stable_external_id(citation: dict[str, Any]) -> str | None:
    canonical = canonical_direct_locator(citation.get("locator"))
    locator = _text(citation.get("locator"))
    if canonical is not None:
        canonical_key = canonical[0]
    elif (
        locator is not None
        and FORMAL_LOCATOR_RE.search(locator)
        and not NON_IDENTIFYING_LOCATOR_RE.search(locator)
    ):
        canonical_key = "formal:" + " ".join(locator.lower().split())
    else:
        return None
    parsed = (
        urlsplit(canonical[1])
        if canonical is not None and canonical[0].startswith("url:")
        else None
    )
    hostname = parsed.hostname.removeprefix("www.") if parsed and parsed.hostname else None
    publisher = _text(citation.get("publisher"))
    if publisher is not None and NON_ATOMIC_EXTERNAL_SOURCE_RE.search(publisher):
        publisher = None
    namespace = _slug(publisher or hostname, fallback="source", limit=32)
    source = _text(citation.get("source"))
    title = source if source and canonical_direct_locator(source) is None else _text(citation.get("label"))
    title_slug = _slug(title or hostname, fallback="document", limit=48)
    suffix = hashlib.sha256(canonical_key.encode("utf-8")).hexdigest()[:8]
    return f"ext-{namespace}-{title_slug}-{suffix}"


def _stabilize_source_ids(
    report: dict[str, Any], repairs: list[dict[str, Any]], *, timestamp: str
) -> str | None:
    citations = report.get("citations")
    if not isinstance(citations, list):
        return None
    source_ids = [
        _text(citation.get("source_id"))
        for citation in citations
        if isinstance(citation, dict)
    ]
    nonempty_ids = [value for value in source_ids if value is not None]
    duplicates = [source_id for source_id, count in Counter(nonempty_ids).items() if count > 1]
    if duplicates:
        return f"duplicate source ID collision {duplicates[0]!r} remains after direct-locator merge"

    used = set(nonempty_ids)
    for index, citation in enumerate(citations):
        if not isinstance(citation, dict) or citation.get("source_class") not in EXTERNAL_SOURCE_CLASSES:
            continue
        old_id = _text(citation.get("source_id"))
        if old_id is None or not GENERIC_SOURCE_ID_RE.fullmatch(old_id):
            continue
        new_id = _stable_external_id(citation)
        if new_id is None:
            continue
        if new_id in used and new_id != old_id:
            return f"stable source ID {new_id!r} collides with an existing citation"
        used.discard(old_id)
        used.add(new_id)
        citation["source_id"] = new_id
        _audit(
            repairs,
            field_path=f"citations[{index}].source_id",
            old_value=old_id,
            new_value=new_id,
            rule="stabilize-external-source-id",
            timestamp=timestamp,
        )
        _remap_references(
            report,
            {old_id: new_id},
            repairs,
            rule="stabilize-external-source-id",
            timestamp=timestamp,
        )
    return None


def _valid_citation(citation: dict[str, Any], source_class: str) -> bool:
    if citation.get("source_class") != source_class:
        return False
    return not validate_report_contract({"citations": [citation], "findings": []})


def _repair_findings(
    report: dict[str, Any], repairs: list[dict[str, Any]], *, timestamp: str
) -> None:
    citations = report.get("citations")
    citations = citations if isinstance(citations, list) else []
    citation_by_id = {
        source_id: citation
        for citation in citations
        if isinstance(citation, dict)
        and (source_id := _text(citation.get("source_id"))) is not None
    }
    findings = report.get("findings")
    if not isinstance(findings, list):
        return
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue
        source_ids = _dedupe([
            value.strip()
            for value in finding.get("source_ids", [])
            if isinstance(value, str) and value.strip()
        ]) if isinstance(finding.get("source_ids"), list) else []

        if finding.get("novelty") == "corroborated" and len(source_ids) < 2:
            old = finding.get("novelty")
            finding["novelty"] = "derived"
            _audit(
                repairs,
                field_path=f"findings[{index}].novelty",
                old_value=old,
                new_value="derived",
                rule="downgrade-unsupported-corroboration",
                timestamp=timestamp,
            )

        reviewed = finding.get("reviewed_source_ids")
        reviewed_ids = [
            value.strip() for value in reviewed if isinstance(value, str) and value.strip()
        ] if isinstance(reviewed, list) else []
        if (
            finding.get("claim_type") in {"negative-capability", "comparative"}
            and not reviewed_ids
            and source_ids
        ):
            finding["reviewed_source_ids"] = source_ids
            _audit(
                repairs,
                field_path=f"findings[{index}].reviewed_source_ids",
                old_value=reviewed,
                new_value=source_ids,
                rule="inherit-reviewed-source-ids",
                timestamp=timestamp,
            )

        if finding.get("evidence_class") != "external-primary":
            continue
        referenced = [citation_by_id[source_id] for source_id in source_ids if source_id in citation_by_id]
        if any(_valid_citation(citation, "external-primary") for citation in referenced):
            continue
        new_evidence = (
            "external-secondary"
            if any(_valid_citation(citation, "external-secondary") for citation in referenced)
            else "model-reasoning"
        )
        finding["evidence_class"] = new_evidence
        _audit(
            repairs,
            field_path=f"findings[{index}].evidence_class",
            old_value="external-primary",
            new_value=new_evidence,
            rule="downgrade-unsupported-primary-evidence",
            timestamp=timestamp,
        )
        if new_evidence == "model-reasoning" and finding.get("classification") == "fact":
            finding["classification"] = "inference"
            _audit(
                repairs,
                field_path=f"findings[{index}].classification",
                old_value="fact",
                new_value="inference",
                rule="downgrade-unsupported-fact-inference",
                timestamp=timestamp,
            )


ERROR_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("duplicate-source-id", "duplicate source_id"),
    ("duplicate-locator", "duplicate locator"),
    ("generic-source-id", " is generic"),
    ("missing-publisher", "requires a publisher"),
    ("bundled-publisher", "identify one publisher"),
    ("missing-source", "requires an identifiable source"),
    ("bundled-source", "identify exactly one source"),
    ("missing-locator", "requires a locator"),
    ("invalid-locator", "requires a direct URL"),
    ("primary-flag", "must set primary="),
    ("missing-claims-supported", "requires at least one claim"),
    ("missing-reviewed-sources", "requires at least one reviewed source"),
    ("unsupported-corroboration", "requires at least two source IDs"),
    ("unresolved-source", "does not resolve to a citation"),
    ("unsupported-primary-evidence", "must reference at least one primary citation"),
)


def contract_error_category(error: str) -> str:
    for category, marker in ERROR_CATEGORIES:
        if marker in error:
            return category
    prefix = error.split(":", 1)[0]
    return re.sub(r"\[\d+\]", "[]", prefix)


def plan_payload_repairs(payload: dict[str, Any], *, timestamp: str | None = None) -> dict[str, Any]:
    timestamp = timestamp or utc_now()
    original = copy.deepcopy(payload)
    working = copy.deepcopy(payload)
    result = working.get("result")
    report = result.get("report") if isinstance(result, dict) else None
    original_result = original.get("result")
    original_report = original_result.get("report") if isinstance(original_result, dict) else None
    if not isinstance(report, dict) or not isinstance(original_report, dict):
        return {
            "accepted": False,
            "reason": "final payload has no schema-shaped report",
            "payload": original,
            "repairs": [],
            "old_errors": [],
            "new_errors": [],
        }

    old_errors = validate_report_contract(original_report)
    repairs: list[dict[str, Any]] = []
    citations = report.get("citations")
    if isinstance(citations, list):
        for index, citation in enumerate(citations):
            if not isinstance(citation, dict):
                continue
            normalized = _normalized_locator(citation)
            if normalized is None:
                continue
            new_locator, rule = normalized
            old_locator = citation.get("locator")
            if old_locator != new_locator:
                citation["locator"] = new_locator
                _audit(
                    repairs,
                    field_path=f"citations[{index}].locator",
                    old_value=old_locator,
                    new_value=new_locator,
                    rule=rule,
                    timestamp=timestamp,
                )

    collision = _merge_duplicate_citations(report, repairs, timestamp=timestamp)
    if collision is None:
        collision = _stabilize_source_ids(report, repairs, timestamp=timestamp)
    if collision is not None:
        return {
            "accepted": False,
            "reason": collision,
            "payload": original,
            "repairs": [],
            "old_errors": old_errors,
            "new_errors": old_errors,
        }

    _repair_findings(report, repairs, timestamp=timestamp)
    new_errors = validate_report_contract(report)
    old_categories = {contract_error_category(error) for error in old_errors}
    new_categories = {contract_error_category(error) for error in new_errors}
    accepted = bool(repairs) and len(new_errors) < len(old_errors) and new_categories <= old_categories
    if not accepted:
        reason = (
            "repair introduced a new error category"
            if not new_categories <= old_categories
            else "projected contract error set is not strictly smaller"
        )
        return {
            "accepted": False,
            "reason": reason,
            "payload": original,
            "repairs": [],
            "old_errors": old_errors,
            "new_errors": old_errors,
        }

    prior_repairs = working.get("report_contract_repairs")
    prior_repairs = prior_repairs if isinstance(prior_repairs, list) else []
    working["report_contract_repairs"] = [*prior_repairs, *repairs]
    working["report_contract_errors"] = new_errors
    return {
        "accepted": True,
        "reason": None,
        "payload": working,
        "repairs": repairs,
        "old_errors": old_errors,
        "new_errors": new_errors,
    }


def _contract_error_payload(payload: dict[str, Any]) -> bool:
    result = payload.get("result")
    report = result.get("report") if isinstance(result, dict) else None
    return (
        isinstance(report, dict)
        and isinstance(report.get("status"), str)
        and bool(payload_contract_errors(payload))
    )


def _mechanically_qualified(payload: dict[str, Any], expected_agent: str) -> bool:
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("is_error") is True:
        return False
    report = result.get("report")
    return bool(
        isinstance(report, dict)
        and report.get("status") in {"complete", "partial", "blocked"}
        and report.get("agent_name") == expected_agent
        and not validate_report_contract(report)
    )


def _timestamp_directory(timestamp: str) -> str:
    return re.sub(r"[^0-9TZ]", "", timestamp.replace("+00:00", "Z"))


def _invalidate_corpus_cache(
    run_dir: Path,
    backup_dir: Path,
    *,
    timestamp: str,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    index_path = run_dir / "source-corpus-index.json"
    if index_path.exists():
        backup_path = backup_dir / "cache" / index_path.name
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(index_path, backup_path)
        actions.append({
            "path": str(index_path),
            "action": "invalidated-and-backed-up",
            "backup_path": str(backup_path),
        })

    pipeline_path = run_dir / "post-source-pipeline.json"
    pipeline = _read_json(pipeline_path)
    if pipeline is not None:
        backup_path = backup_dir / "cache" / pipeline_path.name
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pipeline_path, backup_path)
        definition = pipeline.get("definition")
        if isinstance(definition, dict):
            definition["source_corpus_sha256"] = None
        pipeline["source_contract"] = None
        stages = pipeline.get("stages")
        stages = stages if isinstance(stages, list) else []
        pipeline["status"] = "invalidated-source-corpus" if stages else "blocked-source-contract"
        pipeline["reason"] = "Final source reports changed during deterministic contract repair."
        pipeline["source_corpus_invalidated_at"] = timestamp
        pipeline["updated_at"] = timestamp
        _atomic_json(pipeline_path, pipeline)
        actions.append({
            "path": str(pipeline_path),
            "action": "source-corpus-hash-invalidated",
            "backup_path": str(backup_path),
        })

    summary_path = run_dir / "summary.json"
    summary = _read_json(summary_path)
    nested_pipeline = summary.get("post_source_pipeline") if summary is not None else None
    if summary is not None and isinstance(nested_pipeline, dict):
        backup_path = backup_dir / "cache" / summary_path.name
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(summary_path, backup_path)
        definition = nested_pipeline.get("definition")
        if isinstance(definition, dict):
            definition["source_corpus_sha256"] = None
        nested_pipeline["source_contract"] = None
        stages = nested_pipeline.get("stages")
        stages = stages if isinstance(stages, list) else []
        nested_pipeline["status"] = (
            "invalidated-source-corpus" if stages else "blocked-source-contract"
        )
        nested_pipeline["reason"] = (
            "Final source reports changed during deterministic contract repair."
        )
        nested_pipeline["source_corpus_invalidated_at"] = timestamp
        nested_pipeline["updated_at"] = timestamp
        summary["post_source_pipeline_status"] = nested_pipeline["status"]
        summary["source_corpus_invalidated_at"] = timestamp
        _atomic_json(summary_path, summary)
        actions.append({
            "path": str(summary_path),
            "action": "cached-source-qualification-invalidated",
            "backup_path": str(backup_path),
        })
    return actions


def repair_run_contract(
    run_dir: Path,
    *,
    apply: bool = False,
    timestamp: str | None = None,
) -> dict[str, Any]:
    timestamp = timestamp or utc_now()
    results_dir = run_dir / "results"
    if not results_dir.is_dir():
        raise ValueError(f"Run {run_dir.name} has no results directory")

    decisions: list[dict[str, Any]] = []
    repairs_by_rule: Counter[str] = Counter()
    unresolved: Counter[str] = Counter()
    repaired_names: list[str] = []
    qualified_names: list[str] = []
    still_invalid_names: list[str] = []
    backup_dir = run_dir / "report-contract-backups" / _timestamp_directory(timestamp)

    for path in sorted(results_dir.glob("*.json")):
        payload = _read_json(path)
        if payload is None or not _contract_error_payload(payload):
            continue
        decision = plan_payload_repairs(payload, timestamp=timestamp)
        agent_name = path.stem
        accepted = bool(decision["accepted"])
        projected = decision["payload"] if accepted else payload
        new_errors = decision["new_errors"] if accepted else decision["old_errors"]
        for error in new_errors:
            unresolved[contract_error_category(error)] += 1
        if accepted:
            repaired_names.append(agent_name)
            repairs_by_rule.update(repair["rule"] for repair in decision["repairs"])
            if _mechanically_qualified(projected, agent_name):
                qualified_names.append(agent_name)
            elif new_errors:
                still_invalid_names.append(agent_name)
            if apply:
                backup_path = backup_dir / "results" / path.name
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup_path)
                _atomic_json(path, projected)
        else:
            still_invalid_names.append(agent_name)
        decisions.append({
            "agent_name": agent_name,
            "result_path": str(path),
            "status": "repaired" if apply and accepted else "would-repair" if accepted else "rejected",
            "old_error_count": len(decision["old_errors"]),
            "new_error_count": len(new_errors),
            "repair_count": len(decision["repairs"]),
            "rules": sorted({repair["rule"] for repair in decision["repairs"]}),
            "reason": decision["reason"],
        })

    cache_actions: list[dict[str, Any]] = []
    if apply and repaired_names:
        cache_actions = _invalidate_corpus_cache(run_dir, backup_dir, timestamp=timestamp)

    summary = {
        "run_id": run_dir.name,
        "mode": "apply" if apply else "dry-run",
        "generated_at": timestamp,
        "tool_version": TOOL_VERSION,
        "inspected_reports": len(decisions),
        "repaired_reports": len(repaired_names),
        "repaired_report_names": repaired_names,
        "newly_qualified_reports": len(qualified_names),
        "newly_qualified_report_names": qualified_names,
        "reports_still_invalid": len(still_invalid_names),
        "reports_still_invalid_names": still_invalid_names,
        "repairs_by_rule": dict(sorted(repairs_by_rule.items())),
        "unresolved_error_patterns": dict(sorted(unresolved.items())),
        "backup_directory": str(backup_dir) if apply and repaired_names else None,
        "source_corpus_cache": {
            "status": (
                "invalidated" if cache_actions
                else "not-present" if apply and repaired_names
                else "would-invalidate" if repaired_names
                else "unchanged"
            ),
            "actions": cache_actions,
        },
        "reports": decisions,
    }
    if apply:
        _atomic_json(run_dir / "report-contract-repair-summary.json", summary)
    return summary
