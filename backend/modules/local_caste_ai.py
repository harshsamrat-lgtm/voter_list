"""
Local AI Household and Family Lineage Caste Inference Engine.
Professional 4-Tier Caste Auto-Determination Architecture:

1. Tier 0: Admin Manual Override Protection (caste_source == 'manual_admin').
   - Admin-set caste is never overwritten during auto re-determination.
   - Acts as a verified certified anchor for Household (Tier 2) and Lineage (Tier 3).
2. Tier 1: Direct voter & relative surname recognition & Muslim community detection.
   - Strict surname matching at final position (prevents false prefix triggers like 'ठाकुर दास').
   - Multi-caste ambiguous suffixes ('पाल', 'कुमार', 'सिंह', 'चन्द्र', 'आर्य') never trigger Tier 1.
   - Nukta normalization for accurate Devanagari matching.
3. Tier 2: Household Kinship AI (same part_no + house_no + PROVEN DIRECT KINSHIP).
   - Unclassified voters only inherit caste from an anchor in the same house IF there is a
     verified direct familial relationship (Parent-Child, Husband-Wife, or Siblings sharing identical father).
   - BLIND DOMINANT CASTE IS COMPLETELY ELIMINATED: Unrelated tenants stay unclassified.
   - Locality Window: Chaining capped at max serial gap <= 25 to prevent conflation across different streets.
   - Strict Muslim isolation: Muslim and non-Muslim voters are never conflated.
4. Tier 3: Family Lineage matching with strict serial distance constraint <= 7:
   - "नाम और पिता/पति के नाम के अनुसार लेकिन उक्त भाग संख्या की लिस्ट मे क्रम संख्या मे केवल 7 से अधिक का अंतर न हो"
   - Both voters must be in the same part_no.
   - Difference in serial numbers must satisfy |serial_A - serial_B| <= 7.
   - Requires verified direct kinship (Parent-Child, Husband-Wife, or Siblings sharing identical father).
   - Closest serial distance anchor is selected in case of multiple candidates.
"""

import re
from typing import List, Dict, Any, Optional, Set, Tuple
from collections import defaultdict

from .caste_detector import (
    CASTE_PRESETS,
    AMBIGUOUS_MULTI_CASTE_SUFFIXES,
    extract_surname,
    detect_direct_caste,
    normalize_devanagari_nukta
)
from .community_detector import identify_voter_community

# Generic or unnumbered house representations that must NOT be pooled into a single household
GENERIC_HOUSE_NUMBERS = {
    "", "0", "00", "000", "0000", "--", "-", "na", "n/a", 
    "unknown", "०", "००", "उपलब्ध", "नहीं", "none", "null", "nd"
}

# Honorific prefixes and suffixes to strip during family name comparison
HONORIFIC_PREFIXES = {
    "श्री", "श्रीमती", "श्रीमति", "कु", "कु०", "कु.", "डॉ", "डॉ०", "डॉ.", 
    "डा", "डा०", "डा.", "पं", "पं०", "पं.", "मा", "मा०", "स्व", "स्व०", "स्व.",
    "ले", "ले०", "ले.", "बाबू", "चौधरी", "ठाकुर", "मास्टर", "लाला"
}

HONORIFIC_SUFFIXES = {
    "जी", "साहब", "साहेब", "देवी", "कुमारी", "बेगम", "बानो", "खातून", "वेगम"
}

# Maximum serial number difference permitted for family lineage propagation
MAX_SERIAL_DIFF = 7


def is_generic_house_no(house_no: Optional[str]) -> bool:
    """Returns True if the house number represents an unnumbered, generic, or dummy house."""
    if not house_no:
        return True
    cleaned = str(house_no).strip().lower()
    cleaned = re.sub(r'^[,\s\-–:]+|[,\s\-–:]+$', '', cleaned)
    return cleaned in GENERIC_HOUSE_NUMBERS or (cleaned.isdigit() and int(cleaned) == 0)


def normalize_house_key(house_no: Optional[str]) -> str:
    """Normalizes house number string for clustering (handles Devanagari numerals, leading zeros, and sub-house variations)."""
    if not house_no:
        return ""
    h = str(house_no).strip().upper()
    devanagari_to_ascii = str.maketrans("०१२३४५६७८९", "0123456789")
    h = h.translate(devanagari_to_ascii)
    h = re.sub(r'[><+~*|!।॥”"“\'`#?@^&;_]+', '', h)
    h = re.sub(r'\s+', '', h)
    # Strip leading zeros if followed by non-zero digit: 04 -> 4, 04A -> 4A
    if re.match(r'^0+[1-9]', h):
        h = re.sub(r'^0+', '', h)
    # Normalize hyphen before letters: 12-क -> 12क, 12-A -> 12A
    h = re.sub(r'[-–](?=[A-Z\u0900-\u097F])', '', h)
    return h


def parse_serial_no(serial_val: Any) -> Optional[int]:
    """Safely extracts an integer serial number from int or string."""
    if serial_val is None:
        return None
    if isinstance(serial_val, int):
        return serial_val
    try:
        s_str = str(serial_val).strip()
        digits = re.sub(r'\D', '', s_str)
        if digits:
            return int(digits)
    except Exception:
        pass
    return None


def clean_and_normalize_name(name_str: Optional[str]) -> str:
    """Normalizes Hindi names by stripping zero-width joiners, nuktas, punctuation, and honorifics."""
    if not name_str:
        return ""
    clean = normalize_devanagari_nukta(name_str)
    # OCR typo fixes for common given/father names
    clean = re.sub(r'\bध्र्म\b', 'धर्म', clean)
    clean = re.sub(r'\bधमर्\b', 'धर्म', clean)
    clean = re.sub(r'\bशमार्\b', 'शर्मा', clean)
    clean = re.sub(r'\bउमिर्ला\b', 'उर्मिला', clean)
    clean = re.sub(r'\bभबवान\b', 'भगवान', clean)
    clean = re.sub(r'\bकिशेर\b', 'किशोर', clean)
    clean = re.sub(r'[^\w\s\u0900-\u097F]', ' ', clean)
    tokens = clean.split()
    if not tokens:
        return ""
    # Strip leading honorific prefixes
    while tokens and tokens[0] in HONORIFIC_PREFIXES:
        tokens.pop(0)
    # Strip trailing honorific suffixes (only if more than 1 token remains)
    while len(tokens) > 1 and tokens[-1] in HONORIFIC_SUFFIXES:
        tokens.pop(-1)
    return " ".join(tokens).strip()


def are_person_names_matching(name_a: Optional[str], name_b: Optional[str]) -> bool:
    """
    Rigorously checks if name_a and name_b refer to the same individual in an electoral roll.
    Allows:
    - Zero-width spaces, punctuation, nukta differences.
    - Omission of honorifics ('श्री', 'डॉ', 'देवी', 'जी', 'साहब').
    - Compound space differences ('राम सेवक' vs 'रामसेवक', 'शिव कुमार' vs 'शिवकुमार').
    - Omission of a recognized caste surname in one name (e.g. 'वीरेन्द्र कुमार' vs 'वीरेन्द्र कुमार गुप्ता').
    STRICTLY FORBIDS:
    - Different given names (e.g. 'राज कुमार' vs 'नीरज कुमार', 'राजेश' vs 'राजीव').
    - Substring matching of generic parts ('राम', 'कुमार', 'सिंह').
    - Fuzzy character similarity on different given names.
    """
    if not name_a or not name_b:
        return False

    na = clean_and_normalize_name(name_a)
    nb = clean_and_normalize_name(name_b)

    if not na or not nb:
        return False

    # 1. Exact match
    if na == nb:
        return True

    # 2. Spaces stripped match (e.g. 'राम सेवक' vs 'रामसेवक', 'शिव कुमार' vs 'शिवकुमार')
    na_ns = re.sub(r'\s+', '', na)
    nb_ns = re.sub(r'\s+', '', nb)
    if na_ns == nb_ns:
        return True

    tokens_a = na.split()
    tokens_b = nb.split()

    sur_a = extract_surname(na)
    sur_b = extract_surname(nb)

    # Core given names without trailing surname/ambiguous suffix if multi-token
    # e.g. 'वीरेन्द्र कुमार गुप्ता' -> ['वीरेन्द्र', 'कुमार']
    core_a = tokens_a[:-1] if len(tokens_a) > 1 and (tokens_a[-1] in AMBIGUOUS_MULTI_CASTE_SUFFIXES or tokens_a[-1] == sur_a) else tokens_a
    core_b = tokens_b[:-1] if len(tokens_b) > 1 and (tokens_b[-1] in AMBIGUOUS_MULTI_CASTE_SUFFIXES or tokens_b[-1] == sur_b) else tokens_b

    str_core_a = " ".join(core_a)
    str_core_b = " ".join(core_b)

    # If both core given names are non-empty, compare them
    if str_core_a and str_core_b:
        if str_core_a == str_core_b or re.sub(r'\s+', '', str_core_a) == re.sub(r'\s+', '', str_core_b):
            if len(re.sub(r'\s+', '', str_core_a)) >= 3:
                return True

    # One full name matches the other's core (e.g. full 'वीरेन्द्र कुमार' == core 'वीरेन्द्र कुमार' from 'वीरेन्द्र कुमार गुप्ता')
    if " ".join(tokens_a) == str_core_b or " ".join(tokens_b) == str_core_a:
        return True

    return False


# Backward-compatible alias for existing code
are_names_related = are_person_names_matching


def _get_voter_field(record: Any, field: str, default=None):
    if isinstance(record, dict):
        return record.get(field, default)
    return getattr(record, field, default)


def _set_voter_field(record: Any, field: str, val: Any):
    if isinstance(record, dict):
        record[field] = val
    else:
        setattr(record, field, val)


def check_kinship_between_voters(v1: Any, v2: Any) -> Tuple[bool, str]:
    """
    Rigorously evaluates whether two voters share a verified direct familial relationship:
    1. Parent-Child (Direct: v1 is parent of v2, or v2 is parent of v1)
    2. Husband-Wife (Direct: v1 is husband/wife of v2, or v2 is husband/wife of v1)
    3. Mother-Child via Common Husband/Father (v1 lists husband X, v2 lists father X, or vice-versa)
    4. Siblings (Both list identical father, or both list identical mother)
    5. Co-spouses (Both list identical husband)

    STRICT COMMUNITY ISOLATION: Muslim and non-Muslim voters never share kinship.
    """
    v1_name = _get_voter_field(v1, "name") or ""
    v1_rel = _get_voter_field(v1, "relation_name") or ""
    v1_type = str(_get_voter_field(v1, "relation_type") or "पिता").strip()

    v2_name = _get_voter_field(v2, "name") or ""
    v2_rel = _get_voter_field(v2, "relation_name") or ""
    v2_type = str(_get_voter_field(v2, "relation_type") or "पिता").strip()

    # Community isolation: use cached is_muslim if present
    v1_m = _get_voter_field(v1, "is_muslim")
    is_m_1 = bool(v1_m) if v1_m is not None else identify_voter_community(v1_name, v1_rel, v1_type)[0]

    v2_m = _get_voter_field(v2, "is_muslim")
    is_m_2 = bool(v2_m) if v2_m is not None else identify_voter_community(v2_name, v2_rel, v2_type)[0]

    if is_m_1 != is_m_2:
        return False, "cross_community"

    # 1. Rule 1: v2 is the parent or spouse of v1 (v1 lists v2 as relation)
    if v1_rel and v2_name and are_person_names_matching(v1_rel, v2_name):
        if "पति" in v1_type:
            return True, "पति-पत्नी सम्बन्ध"
        elif "माता" in v1_type:
            return True, "माता-संतान सम्बन्ध"
        else:
            return True, "पिता-संतान सम्बन्ध"

    # 2. Rule 2: v1 is the parent or spouse of v2 (v2 lists v1 as relation)
    if v2_rel and v1_name and are_person_names_matching(v2_rel, v1_name):
        if "पति" in v2_type:
            return True, "पति-पत्नी सम्बन्ध"
        elif "माता" in v2_type:
            return True, "माता-संतान सम्बन्ध"
        else:
            return True, "पिता-संतान सम्बन्ध"

    # 3. Rule 3: Mother-Child via Common Husband/Father
    # In Indian voter rolls, a married woman's relative is 'पति' and her children's relative is 'पिता'.
    # When the woman's husband matches the child's father, they are Mother and Child!
    if v1_rel and v2_rel:
        if ("पति" in v1_type and "पिता" in v2_type) or ("पिता" in v1_type and "पति" in v2_type):
            if are_person_names_matching(v1_rel, v2_rel):
                return True, "माता-संतान (साझा पति/पिता) सम्बन्ध"

    # 4. Rule 4: Siblings (सहोदर भाई/बहन)
    # Both list father (or both list mother), and parent's name matches!
    if v1_rel and v2_rel:
        if ("पिता" in v1_type and "पिता" in v2_type):
            if are_person_names_matching(v1_rel, v2_rel):
                return True, "सहोदर (साझा पिता) सम्बन्ध"
        elif ("माता" in v1_type and "माता" in v2_type):
            if are_person_names_matching(v1_rel, v2_rel):
                return True, "सहोदर (साझा माता) सम्बन्ध"

    # 5. Rule 5: Co-wives / Spouses (both list identical husband)
    if v1_rel and v2_rel:
        if ("पति" in v1_type and "पति" in v2_type):
            if are_person_names_matching(v1_rel, v2_rel):
                return True, "दाम्पत्य (साझा पति) सम्बन्ध"

    return False, "no_relation"


def cluster_voters_by_house_and_locality(records: List[Any], max_serial_gap: int = 25) -> List[List[Any]]:
    """
    Partitions voters into household locality groups according to the user's electoral rule:
    - Only voters with valid, non-generic house numbers are grouped.
    - Voters with identical normalized house_no join the SAME group IF:
      1. They appear contiguously in the voter list sequence, OR
      2. The difference in serial numbers between this voter and the preceding voter of the same house is <= 25.
    - If the serial gap exceeds 25, a new group is started (preventing house numbers from spanning across streets).
    """
    if not records:
        return []

    sorted_recs = sorted(
        records,
        key=lambda r: parse_serial_no(_get_voter_field(r, "serial_no")) or 999999
    )

    groups: List[List[Any]] = []
    active_groups: Dict[str, Dict[str, Any]] = {}

    for idx, r in enumerate(sorted_recs):
        h_raw = _get_voter_field(r, "house_no")
        if is_generic_house_no(h_raw):
            continue
        h_norm = normalize_house_key(h_raw)
        if not h_norm:
            continue

        s_no = parse_serial_no(_get_voter_field(r, "serial_no"))

        joined = False
        if h_norm in active_groups:
            active_grp = active_groups[h_norm]
            prev_idx = active_grp["last_index"]
            prev_s = active_grp["last_serial"]

            is_contiguous = (idx == prev_idx + 1)
            serial_gap_ok = False
            if s_no is not None and prev_s is not None:
                serial_gap_ok = (s_no - prev_s <= max_serial_gap)
            elif s_no is None and prev_s is None:
                serial_gap_ok = is_contiguous

            if is_contiguous or serial_gap_ok:
                active_grp["members"].append(r)
                active_grp["last_index"] = idx
                if s_no is not None:
                    active_grp["last_serial"] = s_no
                joined = True

        if not joined:
            new_grp = {
                "house_key": h_norm,
                "members": [r],
                "last_index": idx,
                "last_serial": s_no
            }
            active_groups[h_norm] = new_grp
            groups.append(new_grp["members"])

    return groups


class LocalCasteAIEngine:
    """Local AI inference engine for voter caste determination and household/lineage propagation."""

    is_generic_house_no = staticmethod(is_generic_house_no)
    normalize_house_key = staticmethod(normalize_house_key)
    parse_serial_no = staticmethod(parse_serial_no)
    clean_and_normalize_name = staticmethod(clean_and_normalize_name)
    are_names_related = staticmethod(are_person_names_matching)
    are_person_names_matching = staticmethod(are_person_names_matching)
    check_kinship_between_voters = staticmethod(check_kinship_between_voters)
    cluster_voters_by_house_and_locality = staticmethod(cluster_voters_by_house_and_locality)

    @classmethod
    def infer_caste_for_records(cls, records: List[Any]) -> List[Any]:
        """
        Takes a list of VoterRecord objects or dicts and determines/propagates caste:
        1. Tier 0: Admin Manual Override Protection (caste_source == 'manual_admin').
        2. Tier 1: Direct voter & relative surname recognition & Muslim community detection.
        3. Tier 2: Household Kinship AI (same part_no + house_no + PROVEN DIRECT KINSHIP).
           - Requires that unclassified voters only inherit caste from an anchor in the
             same house IF there is a verified direct familial relationship.
           - Blind dominant caste fallback is ELIMINATED: Unrelated tenants stay unclassified.
        4. Tier 3: Family Lineage matching with serial difference constraint <= 7:
           - Direct kinship required, closest serial distance anchor selected.
        """
        if not records:
            return records

        get_val = _get_voter_field
        set_val = _set_voter_field

        # ---------------------------------------------------------------------
        # Tier 0 & Tier 1: Admin Override Protection & Direct Surname/Community
        # ---------------------------------------------------------------------
        for r in records:
            c_src = get_val(r, "caste_source")
            c_key = get_val(r, "caste_key")

            # 🛡️ RULE 0: Admin Manual Override Protection
            # If caste was manually set/edited by Admin, NEVER overwrite it!
            if c_src in ("manual_admin", "admin") and c_key:
                set_val(r, "caste_key", c_key)
                set_val(r, "caste_source", "manual_admin")
                if not get_val(r, "caste_reason"):
                    set_val(r, "caste_reason", "एडमिन द्वारा प्रविष्टि (Manual Override)")
                continue

            v_name = get_val(r, "name") or ""
            rel_name = get_val(r, "relation_name") or ""
            rel_type = get_val(r, "relation_type") or "पिता"
            is_m_hint = bool(get_val(r, "is_muslim"))

            detected_key, matched_sur = detect_direct_caste(v_name, rel_name, rel_type, is_m_hint)

            if detected_key:
                set_val(r, "caste_key", detected_key)
                set_val(r, "caste_source", "direct_community" if detected_key == "muslim" else "direct_surname")
                if detected_key == "muslim":
                    set_val(r, "is_muslim", 1)
                    set_val(r, "caste_reason", f"प्रत्यक्ष समुदाय पहचान ({matched_sur})")
                else:
                    set_val(r, "is_muslim", 0)
                    set_val(r, "caste_reason", f"प्रत्यक्ष उपनाम मिलान ({matched_sur})")
            else:
                is_m, _, _ = identify_voter_community(v_name, rel_name, rel_type)
                if is_m:
                    set_val(r, "caste_key", "muslim")
                    set_val(r, "is_muslim", 1)
                    set_val(r, "caste_source", "direct_community")
                    set_val(r, "caste_reason", "प्रत्यक्ष समुदाय पहचान (मुस्लिम)")
                else:
                    set_val(r, "caste_key", None)
                    set_val(r, "is_muslim", 0)
                    set_val(r, "caste_source", None)
                    set_val(r, "caste_reason", None)

        # ---------------------------------------------------------------------
        # Group records by part_no for strict partition isolation
        # ---------------------------------------------------------------------
        part_records: Dict[str, List[Any]] = defaultdict(list)
        for r in records:
            part = str(get_val(r, "part_no") or "").strip()
            part_records[part].append(r)

        for part, p_records in part_records.items():
            # -----------------------------------------------------------------
            # Tier 2: Household Kinship AI (Part-wise with Locality Window <= 25)
            # Iteratively propagates caste to immediate kin within the household
            # Stops when no more members can be classified via verified kinship
            # -----------------------------------------------------------------
            household_groups = cls.cluster_voters_by_house_and_locality(p_records, max_serial_gap=25)

            for members in household_groups:
                if len(members) <= 1:
                    continue

                while True:
                    newly_classified_in_round = 0

                    # Current verified Hindu anchors in this household
                    anchors = [
                        m for m in members
                        if get_val(m, "caste_key")
                        and get_val(m, "caste_key") != "muslim"
                        and not get_val(m, "is_muslim")
                    ]

                    if not anchors:
                        break

                    # Prioritize Admin anchors first, then direct surname anchors
                    sorted_anchors = sorted(
                        anchors,
                        key=lambda a: 0 if get_val(a, "caste_source") == "manual_admin"
                        else (1 if get_val(a, "caste_source") == "direct_surname" else 2)
                    )

                    for m in members:
                        if get_val(m, "caste_key"):
                            continue

                        # Muslim voters NEVER inherit Hindu caste
                        if bool(get_val(m, "is_muslim")):
                            continue

                        # Strict kinship check against anchors
                        matched_anchor = None
                        matched_desc = ""
                        for a in sorted_anchors:
                            is_rel, desc = check_kinship_between_voters(m, a)
                            if is_rel:
                                matched_anchor = a
                                matched_desc = desc
                                break

                        if matched_anchor:
                            h_val = get_val(m, "house_no") or get_val(members[0], "house_no") or ""
                            a_name = get_val(matched_anchor, "name") or ""
                            a_caste = get_val(matched_anchor, "caste_key")
                            set_val(m, "caste_key", a_caste)
                            set_val(m, "caste_source", "household_ai")
                            set_val(m, "caste_reason", f"मकान AI: मकान नं० {h_val} में सम्बन्धी '{a_name}' से [{matched_desc}] रिश्ता सत्यापित")
                            newly_classified_in_round += 1

                    if newly_classified_in_round == 0:
                        break

            # -----------------------------------------------------------------
            # Tier 3: Family Lineage Matching with Serial Difference Constraint <= 7
            # -----------------------------------------------------------------
            known_anchors = [
                r for r in p_records
                if get_val(r, "caste_key") and get_val(r, "caste_key") != "muslim" and not get_val(r, "is_muslim")
            ]
            unclassified = [
                r for r in p_records
                if not get_val(r, "caste_key") and not get_val(r, "is_muslim")
            ]

            if not known_anchors or not unclassified:
                continue

            # Index anchors by serial number
            anchors_by_serial = defaultdict(list)
            for k in known_anchors:
                s = parse_serial_no(get_val(k, "serial_no"))
                if s is not None:
                    anchors_by_serial[s].append(k)

            for u in unclassified:
                u_serial = parse_serial_no(get_val(u, "serial_no"))
                if u_serial is None:
                    continue

                matches = []
                for s in range(u_serial - MAX_SERIAL_DIFF, u_serial + MAX_SERIAL_DIFF + 1):
                    for k in anchors_by_serial.get(s, []):
                        diff = abs(u_serial - s)
                        is_rel, desc = check_kinship_between_voters(u, k)
                        if is_rel:
                            matches.append((diff, get_val(k, "caste_key"), desc, k, s))

                if matches:
                    matches.sort(key=lambda x: x[0])
                    best_diff, best_caste, best_rel_desc, best_anchor, best_k_serial = matches[0]

                    set_val(u, "caste_key", best_caste)
                    set_val(u, "caste_source", "family_lineage_ai")
                    anchor_name = get_val(best_anchor, "name") or ""
                    reason_str = f"रिश्ता AI: क्रम अंतर {best_diff} (≤ 7) पर सम्बन्धी '{anchor_name}' (क्रम #{best_k_serial}) से [{best_rel_desc}] रिश्ता सत्यापित"
                    set_val(u, "caste_reason", reason_str)

        return records
