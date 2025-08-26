# backend/services/donor_profile_matcher.py
import os
import re
import json
import logging
import difflib
from typing import Any, Dict, List, Tuple, Optional
from azure.cosmos import ContainerProxy

# ---- Env flags / knobs ----
DONOR_PROFILE_LOOKUP_ENABLE = os.getenv("DONOR_PROFILE_LOOKUP_ENABLE", "1") not in ("0", "false", "False")
DONOR_PROFILE_MIN_SCORE = float(os.getenv("DONOR_PROFILE_MIN_SCORE", "0.65"))
DONOR_PROFILE_TOKEN_LIMIT = int(os.getenv("DONOR_PROFILE_TOKEN_LIMIT", "4"))
DONOR_PROFILE_MAX_CHARS = int(os.getenv("DONOR_PROFILE_MAX_CHARS", "8000"))

# Comma-separated allow/deny lists (Title_Case_With_Underscores keys). Case-insensitive.
_INCLUDE = [s.strip() for s in os.getenv("DONOR_PROFILE_INCLUDE_FIELDS", "").split(",") if s.strip()]
_EXCLUDE = [s.strip() for s in os.getenv("DONOR_PROFILE_EXCLUDE_FIELDS", "").split(",") if s.strip()]

STOPWORDS = {
    "and","of","for","the","a","an","&",
    "agency","foundation","fund","bank","ministry","department",
    "international","cooperation","development","affairs","programme","program",
    "office","directorate","general","state","government","governments",
}

# --------- basic text utils ---------
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

def _tokens(name: str) -> List[str]:
    toks = [t for t in re.split(r"\s+", _norm(name)) if t and t not in STOPWORDS]
    return toks[:DONOR_PROFILE_TOKEN_LIMIT] or ([t for t in re.split(r"\s+", _norm(name)) if t][:2])

def _jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb: return 0.0
    return len(sa & sb) / len(sa | sb)

def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()

def _score(query_name: str, candidate_name: str) -> float:
    r = _ratio(query_name, candidate_name); j = _jaccard(_tokens(query_name), _tokens(candidate_name))
    return 0.7 * r + 0.3 * j

def _strip_html(s: Optional[str]) -> str:
    if not s: return ""
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</p\s*>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\s+\n", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()

def _maybe_list(val: Any) -> Optional[List[str]]:
    if isinstance(val, list):
        return [str(x) for x in val]
    if isinstance(val, str) and val.strip().startswith("["):
        try:
            arr = json.loads(val)
            if isinstance(arr, list):
                return [str(x) for x in arr]
        except Exception:
            return None
    return None

def _pick(doc: Dict[str, Any], *keys: str) -> str:
    """Pick first present (case-insensitive) among keys."""
    # normalize all keys once
    idx = {k.lower(): k for k in doc.keys()}
    for k in keys:
        real = idx.get(k.lower())
        if real is not None:
            v = doc.get(real)
            if isinstance(v, str): return v.strip()
            if v is None: continue
            return str(v)
    return ""

def _all_keys_ci(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Case-insensitive view of doc keys."""
    return {k.lower(): v for k, v in doc.items()}

def _truncate(s: str, n: int) -> str:
    if len(s) <= n: return s
    return s[: max(0, n - 12)] + "\n[TRUNCATED]"

# --------- Cosmos candidate collection ---------
def _collect_candidates(container: ContainerProxy, name: str, limit_per_query: int = 50) -> List[Dict[str, Any]]:
    toks = _tokens(name)
    seen_ids = set()
    cands: List[Dict[str, Any]] = []

    for tok in toks:
        q = (
            "SELECT TOP 50 * FROM c "
            "WHERE CONTAINS(LOWER(c.donor_entity_name), @tok) "
            "   OR CONTAINS(LOWER(c.donor_name), @tok)"
        )
        try:
            items = container.query_items(
                query=q,
                parameters=[{"name": "@tok", "value": tok}],
                enable_cross_partition_query=True,
            )
            for it in items:
                cid = it.get("id")
                if cid and cid not in seen_ids:
                    cands.append(it); seen_ids.add(cid)
        except Exception as e:
            logging.warning(f"[DonorProfileLookup] query failed tok={tok}: {e}")

    if not cands:
        try:
            items = container.query_items(
                query="SELECT TOP 200 * FROM c",
                enable_cross_partition_query=True,
            )
            for it in items:
                cid = it.get("id")
                if cid and cid not in seen_ids:
                    cands.append(it); seen_ids.add(cid)
        except Exception as e:
            logging.warning(f"[DonorProfileLookup] fallback query failed: {e}")

    return cands

# --------- Build a rich, LLM-friendly profile text ---------
_FRIENDLY_LABELS = {
    "Donor_Entity_Name": "Donor Name",
    "Donor_Name": "Donor Name",
    "Entity_Name": "Donor Name",
    "parent_entity_if_applicable": "Country/Parent",
    "Country": "Country/Parent",
    "Type_Of_Entity": "Type",
    "Level_Of_Entity": "Level",
    "IOM_Membership_Status": "IOM Membership",
    "Website": "Website",
    "Thematic_Priorities_Related_To_IOM": "Thematic Priorities",
    "Thematic_Priorities_Narrative": "Priorities Narrative",
    "Geographical_Focus": "Geographical Focus",
    "Geographic_Priorities_Narrative": "Geographic Narrative",
    "Donor_Outreach_Instructions": "Outreach Guidance",
    "Proposal_Format": "Proposal Format",
    "Funding_Agreement_Format": "Funding Agreement Format",
    "Budget_Lines_Flexibility": "Budget Flexibility",
    "Reporting_Format": "Reporting",
    "No_Cost_Extensions_Policy": "No-Cost Extensions",
    "Visibility_Logo_Guidelines": "Visibility/Logo",
    "Calls_For_Proposals_Cfp_Website": "Calls for Proposals",
    "Start_Of_Budget_Cycle": "Budget Cycle Start",
    "End_Of_Budget_Cycle": "Budget Cycle End",
    "Additional_Info_Tips_Vis_A_Vis_Fundraising": "Additional Fundraising Info",
    "Organization_Chart": "Org Chart",
    "Templates_Logos_And_Key_Documents": "Templates/Logos/Docs",
    "Modified": "Modified",
    "Modified_By": "Modified By",
    "Approval_Status": "Approval Status",
    "source": "Source",
}

_PRIMARY_KEYS = {
    # identity (prefer)
    "Donor_Entity_Name","Donor_Name","Entity_Name","donor_entity_name",
    "Country","parent_entity_if_applicable",
    "Type_Of_Entity","Level_Of_Entity","IOM_Membership_Status","Website",
    # focus
    "Thematic_Priorities_Related_To_IOM","Thematic_Priorities_Narrative",
    "Geographical_Focus","Geographic_Priorities_Narrative",
    # ops/process
    "Donor_Outreach_Instructions","Proposal_Format","Funding_Agreement_Format",
    "Budget_Lines_Flexibility","Reporting_Format","No_Cost_Extensions_Policy",
    "Visibility_Logo_Guidelines","Calls_For_Proposals_Cfp_Website",
    "Start_Of_Budget_Cycle","End_Of_Budget_Cycle",
    "Additional_Info_Tips_Vis_A_Vis_Fundraising",
    # misc/meta
    "Organization_Chart","Templates_Logos_And_Key_Documents",
    "Modified","Modified_By","Approval_Status","source"
}

def _build_profile_text(doc: Dict[str, Any]) -> str:
    ci = _all_keys_ci(doc)

    # Helper to fetch with multiple fallbacks (case-insensitive)
    def pick(*keys: str) -> str:
        for k in keys:
            v = ci.get(k.lower())
            if v is not None:
                return v if isinstance(v, str) else json.dumps(v)
        return ""

    # ------- Identity -------
    name = pick("Donor_Entity_Name","Donor_Name","Entity_Name","donor_entity_name") or ""
    country = pick("Country","parent_entity_if_applicable") or ""
    dtype = pick("Type_Of_Entity") or ""
    level = pick("Level_Of_Entity") or ""
    membership = pick("IOM_Membership_Status") or ""
    website = pick("Website") or ""

    # ------- Focus -------
    priorities_list = _maybe_list(ci.get("thematic_priorities_related_to_iom"))
    priorities_narr = _strip_html(pick("Thematic_Priorities_Narrative"))
    geos_list = _maybe_list(ci.get("geographical_focus"))
    geo_narr = _strip_html(pick("Geographic_Priorities_Narrative"))

    # ------- Ops/Process -------
    outreach = _strip_html(pick("Donor_Outreach_Instructions"))
    proposals = pick("Proposal_Format")
    funding_fmt = pick("Funding_Agreement_Format")
    budget_flex = pick("Budget_Lines_Flexibility")
    reporting = pick("Reporting_Format")
    nce = pick("No_Cost_Extensions_Policy")
    visibility = _strip_html(pick("Visibility_Logo_Guidelines"))
    cfp = pick("Calls_For_Proposals_Cfp_Website")
    cycle_start = pick("Start_Of_Budget_Cycle")
    cycle_end = pick("End_Of_Budget_Cycle")
    extra_fundraising = _strip_html(pick("Additional_Info_Tips_Vis_A_Vis_Fundraising"))

    # ------- Meta / Links -------
    org_chart = pick("Organization_Chart")
    templates_docs = pick("Templates_Logos_And_Key_Documents")
    modified = pick("Modified")
    modified_by = pick("Modified_By")
    approval = pick("Approval_Status")
    source = pick("source")

    lines: List[str] = []

    # Identity
    if name: lines.append(f"Donor Name: {name}")
    id_bits = []
    if country: id_bits.append(f"Country/Parent: {country}")
    if dtype:   id_bits.append(f"Type: {dtype}")
    if level:   id_bits.append(f"Level: {level}")
    if membership: id_bits.append(f"IOM Membership: {membership}")
    if id_bits: lines.append(" | ".join(id_bits))
    if website: lines.append(f"Website: {website}")

    # Focus
    if priorities_list:
        lines.append("Thematic Priorities:")
        for p in priorities_list[:30]:
            lines.append(f"- {p}")
    if priorities_narr:
        lines.append("Priorities Narrative:")
        lines.append(priorities_narr)

    if geos_list:
        lines.append("Geographical Focus:")
        for g in geos_list[:40]:
            lines.append(f"- {g}")
    if geo_narr:
        lines.append("Geographic Narrative:")
        lines.append(geo_narr)

    # Ops/Process
    op_bits = []
    if proposals:   op_bits.append(f"Proposal Format: {proposals}")
    if funding_fmt: op_bits.append(f"Funding Agreement: {funding_fmt}")
    if reporting:   op_bits.append(f"Reporting: {reporting}")
    if budget_flex: op_bits.append(f"Budget Flexibility: {budget_flex}")
    if nce:         op_bits.append(f"No-Cost Extensions: {nce}")
    if cycle_start or cycle_end:
        op_bits.append(f"Budget Cycle: {cycle_start or 'N/A'} → {cycle_end or 'N/A'}")
    if op_bits:
        lines.append("Processes & Policies:")
        lines.extend(op_bits)
    if visibility:
        lines.append("Visibility/Logo Guidelines:")
        lines.append(visibility)
    if cfp:
        lines.append(f"Calls for Proposals: {cfp}")
    if outreach:
        lines.append("Outreach Guidance:")
        lines.append(outreach)
    if extra_fundraising:
        lines.append("Additional Fundraising Info:")
        lines.append(extra_fundraising)

    # Meta/Links
    meta_bits = []
    if org_chart:     meta_bits.append(f"Org Chart: {org_chart}")
    if templates_docs:meta_bits.append(f"Templates/Docs: {templates_docs}")
    if modified:      meta_bits.append(f"Modified: {modified}")
    if modified_by:   meta_bits.append(f"Modified By: {modified_by}")
    if approval:      meta_bits.append(f"Approval: {approval}")
    if source:        meta_bits.append(f"Source: {source}")
    if meta_bits:
        lines.append("Meta:")
        lines.extend(meta_bits)

    # -------- Add 'Other Fields' (informative leftovers) --------
    used_keys = {k.lower() for k in _PRIMARY_KEYS} | {
        "id","csv_row_index","created_at","updated_at","_rid","_self","_etag","_attachments","_ts",
        "donor_entity_name","parent_entity_if_applicable"
    }
    include_set = {k.lower() for k in _INCLUDE}
    exclude_set = {k.lower() for k in _EXCLUDE} | used_keys

    other_pairs = []
    for k, v in doc.items():
        kl = k.lower()
        if kl in exclude_set and kl not in include_set:
            continue
        if v is None: continue
        sv = v if isinstance(v, str) else json.dumps(v)
        sv = _strip_html(sv)
        if not sv or sv == "[]": continue
        label = _FRIENDLY_LABELS.get(k, k)
        other_pairs.append((label, sv))

    if other_pairs:
        lines.append("Other Fields:")
        for label, value in other_pairs[:40]:
            # keep each 'other' field concise
            value = value if len(value) <= 600 else (value[:580] + "…")
            lines.append(f"- {label}: {value}")

    profile_text = "\n".join([ln for ln in lines if ln]).strip()
    return _truncate(profile_text, DONOR_PROFILE_MAX_CHARS)

# --------- Public API ---------
def lookup_donor_profile_text(
    profile_container: ContainerProxy,
    donor_name: str
) -> Tuple[str, Dict[str, Any]]:
    """
    Returns (profile_text, meta) where:
      - profile_text: empty string if nothing suitable found
      - meta: {"match_score": float, "match_id": str, "match_name": str} if found, else {}
    """
    if not DONOR_PROFILE_LOOKUP_ENABLE or not donor_name:
        return "", {}

    cands = _collect_candidates(profile_container, donor_name)
    if not cands:
        return "", {}

    best_doc: Optional[Dict[str, Any]] = None
    best_score = -1.0
    for d in cands:
        cand_name = d.get("donor_entity_name") or d.get("Donor_Entity_Name") or d.get("Donor_Name") or d.get("Entity_Name") or ""
        sc = _score(donor_name, cand_name)
        if sc > best_score:
            best_score = sc; best_doc = d

    if not best_doc or best_score < DONOR_PROFILE_MIN_SCORE:
        return "", {}

    text = _build_profile_text(best_doc)
    meta = {
        "match_score": round(best_score, 3),
        "match_id": best_doc.get("id"),
        "match_name": (best_doc.get("donor_entity_name") or
                       best_doc.get("Donor_Entity_Name") or
                       best_doc.get("Donor_Name") or
                       best_doc.get("Entity_Name")),
    }
    return text, meta
