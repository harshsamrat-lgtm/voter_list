"""
Property Survey Sync & Global Phonetic Voter Audit Module.
Bridges NPPropertyServey (Firebase / Next.js) with UP Voter List Converter.

Rule: "सदस्य का नाम व संबंधी का नाम से ध्वन्यात्मक मिलान से ही सम्पूर्ण वोटर डेटाबेस मे करना है
       क्योंकि मकान नंबर एव वार्ड नंबर वोटर लिस्ट मे मैच नहीं हो पाएंगे।"
"""

import os
import re
import time
import requests
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict

from .ai_search import normalize_devanagari, transliterate_latin_to_hindi, get_phonetic_key
from .local_caste_ai import (
    clean_and_normalize_name,
    are_names_related,
    _get_voter_field
)

NP_SURVEY_API_URL = os.getenv("NP_SURVEY_API_URL", "http://127.0.0.1:9002/api/voter-sync")


class PropertySurveySync:
    """Handles communication with NPPropertyServey and performs global phonetic voter matching."""

    @classmethod
    def fetch_streets(cls, api_url: Optional[str] = None) -> Dict[str, Any]:
        """Fetches unique wards and streets from NPPropertyServey API."""
        url = (api_url or NP_SURVEY_API_URL) + "?action=get_streets"
        try:
            resp = requests.get(url, timeout=35)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    return data
            return {
                "success": False,
                "error": f"API returned status {resp.status_code}: {resp.text[:200]}"
            }
        except requests.exceptions.ConnectionError:
            return {
                "success": False,
                "error": f"NPPropertyServey सर्वर (पोर्ट 9002) से कनेक्ट नहीं हो सका। कृपया सुनिश्चित करें कि 'npm run dev' चल रहा है।"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def fetch_street_houses_and_members(
        cls,
        street: str,
        zone: Optional[str] = None,
        ward: Optional[str] = None,
        min_age: int = 17,
        api_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fetches all houses and family members aged >= min_age for a specific street, filtered by zone or ward."""
        base_url = api_url or NP_SURVEY_API_URL
        params = {
            "action": "get_street_data",
            "street": street.strip(),
            "minAge": min_age
        }
        if zone:
            params["zone"] = zone.strip()
        if ward:
            params["ward"] = ward.strip()

        try:
            resp = requests.get(base_url, params=params, timeout=45)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    return data
            return {
                "success": False,
                "error": f"API returned status {resp.status_code}: {resp.text[:200]}"
            }
        except requests.exceptions.ConnectionError:
            return {
                "success": False,
                "error": f"NPPropertyServey सर्वर (पोर्ट 9002) से कनेक्ट नहीं हो सका।"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def prepare_voter_index(cls, all_voters: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Pre-normalizes all voters in memory with inverted token and phonetic index for sub-second audit matching."""
        prepped = []
        by_token = defaultdict(list)
        by_phon_lead = defaultdict(list)

        for v in all_voters:
            v_name_clean = clean_and_normalize_name(v.get("name") or "")
            v_rel_clean = clean_and_normalize_name(v.get("relation_name") or "")
            norm_m = normalize_devanagari(v_name_clean)
            norm_r = normalize_devanagari(v_rel_clean)
            phon_m = get_phonetic_key(v_name_clean)
            phon_r = get_phonetic_key(v_rel_clean)
            item = {
                "raw": v,
                "norm_m": norm_m,
                "norm_r": norm_r,
                "phon_m": phon_m,
                "phon_r": phon_r,
                "gender": (v.get("gender") or "").strip()
            }
            prepped.append(item)
            for tok in norm_m.split():
                if len(tok) >= 2:
                    by_token[tok].append(item)
            if phon_m:
                for pt in phon_m.split():
                    lead = pt[:3]
                    if len(lead) >= 2:
                        by_phon_lead[lead].append(item)

        return {
            "items": prepped,
            "by_token": by_token,
            "by_phon_lead": by_phon_lead
        }

    @classmethod
    def match_member_against_all_voters(
        cls,
        member_name: str,
        relative_name: str,
        all_voters: Any,
        gender_hint: Optional[str] = None
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Matches a survey family member strictly on (Member Name + Relative Name) phonetically
        across the ENTIRE voter database, with zero reliance on house or ward numbers.
        Seamlessly handles both Hindi Devanagari and English/Roman script entries.
        
        Returns:
            (is_registered, matched_voter_dict, match_description)
        """
        m_str = (member_name or "").strip()
        r_str = (relative_name or "").strip()

        if not m_str:
            return False, None, "सदस्य का नाम रिक्त है"

        # Detect Latin / English characters in surveyor input
        is_m_lat = bool(re.search(r'[a-zA-Z]', m_str))
        is_r_lat = bool(re.search(r'[a-zA-Z]', r_str))

        if is_m_lat:
            m_cands = transliterate_latin_to_hindi(m_str)
            norm_m_list = [normalize_devanagari(clean_and_normalize_name(c)) for c in m_cands]
        else:
            norm_m_list = [normalize_devanagari(clean_and_normalize_name(m_str))]
        phon_m = get_phonetic_key(m_str)

        if is_r_lat and r_str:
            r_cands = transliterate_latin_to_hindi(r_str)
            norm_r_list = [normalize_devanagari(clean_and_normalize_name(c)) for c in r_cands]
        elif r_str:
            norm_r_list = [normalize_devanagari(clean_and_normalize_name(r_str))]
        else:
            norm_r_list = []
        phon_r = get_phonetic_key(r_str) if r_str else ""

        best_candidate = None
        best_score = 0

        # Fast candidate filtering using inverted index if available
        if isinstance(all_voters, dict) and "items" in all_voters:
            index_data = all_voters
            cands = []
            seen = set()
            for nm in norm_m_list:
                for tok in nm.split():
                    if len(tok) >= 2 and tok in index_data["by_token"]:
                        for it in index_data["by_token"][tok]:
                            i_id = id(it)
                            if i_id not in seen:
                                seen.add(i_id)
                                cands.append(it)
            if phon_m:
                for pt in phon_m.split():
                    lead = pt[:3]
                    if len(lead) >= 2 and lead in index_data["by_phon_lead"]:
                        for it in index_data["by_phon_lead"][lead]:
                            i_id = id(it)
                            if i_id not in seen:
                                seen.add(i_id)
                                cands.append(it)
            candidate_list = cands if cands else index_data["items"]
            is_prepped = True
        elif all_voters and isinstance(all_voters[0], dict) and "norm_m" in all_voters[0]:
            candidate_list = all_voters
            is_prepped = True
        else:
            candidate_list = all_voters
            is_prepped = False

        for item in candidate_list:
            if is_prepped:
                v = item["raw"]
                v_norm_m = item["norm_m"]
                v_norm_r = item["norm_r"]
                v_phon_m = item.get("phon_m") or ""
                v_phon_r = item.get("phon_r") or ""
                v_gender = item["gender"]
            else:
                v = item
                v_name_clean = clean_and_normalize_name(v.get("name") or "")
                v_rel_clean = clean_and_normalize_name(v.get("relation_name") or "")
                v_norm_m = normalize_devanagari(v_name_clean)
                v_norm_r = normalize_devanagari(v_rel_clean)
                v_phon_m = get_phonetic_key(v_name_clean)
                v_phon_r = get_phonetic_key(v_rel_clean)
                v_gender = (v.get("gender") or "").strip()

            # Check member name match phonetically / transliterated
            m_matched = any(are_names_related(nm, v_norm_m) for nm in norm_m_list if nm)
            if not m_matched and phon_m and v_phon_m:
                if phon_m == v_phon_m:
                    m_matched = True
                else:
                    pm_words = phon_m.split()
                    vpm_words = v_phon_m.split()
                    if pm_words and vpm_words and pm_words[0] == vpm_words[0]:
                        if phon_m.startswith(v_phon_m) or v_phon_m.startswith(phon_m):
                            m_matched = True
                        elif set(vpm_words).issubset(set(pm_words)) or set(pm_words).issubset(set(vpm_words)):
                            m_matched = True

            if not m_matched:
                continue

            # Check relative name match (if relative_name was supplied in survey)
            if norm_r_list and v_norm_r:
                r_matched = any(are_names_related(nr, v_norm_r) for nr in norm_r_list if nr)
                if not r_matched and phon_r and v_phon_r:
                    if phon_r == v_phon_r:
                        r_matched = True
                    else:
                        pr_words = phon_r.split()
                        vpr_words = v_phon_r.split()
                        if pr_words and vpr_words and pr_words[0] == vpr_words[0]:
                            if phon_r.startswith(v_phon_r) or v_phon_r.startswith(phon_r):
                                r_matched = True
                            elif set(vpr_words).issubset(set(pr_words)) or set(pr_words).issubset(set(vpr_words)):
                                r_matched = True
                if not r_matched:
                    continue
                score = 100
            elif not norm_r_list:
                score = 60
            else:
                score = 50

            # Gender consistency check
            if gender_hint and v_gender:
                if (gender_hint in ("महिला", "स्त्री", "Female", "female", "F") and v_gender in ("महिला", "स्त्री")) or \
                   (gender_hint in ("पुरुष", "नर", "Male", "male", "M") and v_gender in ("पुरुष", "नर")):
                    score += 10
                elif (gender_hint in ("महिला", "स्त्री", "Female", "female", "F") and v_gender in ("पुरुष", "नर")) or \
                     (gender_hint in ("पुरुष", "नर", "Male", "male", "M") and v_gender in ("महिला", "स्त्री")):
                    score -= 25

            if score > best_score:
                best_score = score
                best_candidate = v
                if score >= 100:
                    break

        if best_candidate and best_score >= 70:
            desc = "नाम व संबंधी का सटीक/ध्वन्यात्मक मिलान"
            return True, best_candidate, desc

        return False, None, "वोटर लिस्ट में नाम व संबंधी का रिकॉर्ड नहीं मिला"

    @classmethod
    def sync_mapping_to_np_survey(cls, mapping_data: Dict[str, Any], api_url: Optional[str] = None) -> Dict[str, Any]:
        """Sends member voter mapping to NPPropertyServey API so it is permanently saved in Firestore."""
        url = api_url or NP_SURVEY_API_URL
        payload = {
            "action": "save_voter_mapping",
            "familyId": mapping_data.get("family_id"),
            "memberId": mapping_data.get("member_id"),
            "voterEpic": mapping_data.get("epic_no"),
            "voterPartNo": str(mapping_data.get("part_no") or ""),
            "voterSerialNo": mapping_data.get("serial_no"),
            "voterName": mapping_data.get("voter_name"),
            "isVoterRegistered": True
        }
        try:
            resp = requests.post(url, json=payload, timeout=20)
            if resp.status_code == 200:
                return resp.json()
            return {"success": False, "error": f"NPPropertyServey error {resp.status_code}: {resp.text[:200]}"}
        except Exception as e:
            return {"success": False, "error": f"Failed to sync with NPPropertyServey: {str(e)}"}

    @classmethod
    def delete_mapping_from_np_survey(cls, family_id: str, member_id: str, api_url: Optional[str] = None) -> Dict[str, Any]:
        """Deletes member voter mapping from NPPropertyServey API."""
        url = api_url or NP_SURVEY_API_URL
        payload = {
            "action": "delete_voter_mapping",
            "familyId": family_id,
            "memberId": member_id
        }
        try:
            resp = requests.post(url, json=payload, timeout=20)
            if resp.status_code == 200:
                return resp.json()
            return {"success": False, "error": f"NPPropertyServey error {resp.status_code}: {resp.text[:200]}"}
        except Exception as e:
            return {"success": False, "error": f"Failed to unlink in NPPropertyServey: {str(e)}"}

    @classmethod
    def audit_street_voters(
        cls,
        street_data: Dict[str, Any],
        all_voters: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Takes raw street survey data from NPPropertyServey,
        and annotates every house and 17+ member with their live voter registration status.
        Integrates active manual member-to-voter mappings with highest precedence.
        """
        from .database import VoterDatabase
        existing_mappings = VoterDatabase.get_all_survey_mappings_dict()

        houses = street_data.get("houses", [])
        annotated_houses = []

        total_eligible = 0
        total_registered = 0
        total_unregistered = 0
        form6_18plus = 0
        form6_17plus = 0

        # Pre-normalize the entire database once for high-speed phonetic matching
        prepped_voters = cls.prepare_voter_index(all_voters)

        for house in houses:
            fam_id = house.get("familyId") or ""
            members = house.get("eligibleMembers", [])
            annotated_members = []
            house_registered = 0
            house_unregistered = 0

            for m in members:
                total_eligible += 1
                m_name = m.get("name") or ""
                m_rel = m.get("fatherHusbandName") or ""
                m_gender = m.get("gender") or ""
                m_age = int(m.get("age") or 0)
                m_id = m.get("memberId") or ""
                m_key = f"{fam_id}_{m_id}" if fam_id and m_id else ""

                # 1. Check if member was already manually mapped
                manual_map = existing_mappings.get(m_key)
                if not manual_map and m.get("isVoterRegistered") and m.get("voterEpic"):
                    manual_map = {
                        "epic_no": m.get("voterEpic"),
                        "part_no": m.get("voterPartNo"),
                        "serial_no": m.get("voterSerialNo"),
                        "voter_name": m.get("voterName") or m_name,
                        "voter_id": None
                    }

                if manual_map:
                    is_reg = True
                    total_registered += 1
                    house_registered += 1
                    status = "registered"
                    status_label = "वोट बना हुआ है (मैप किया गया)"
                    status_badge = "success"
                    action_needed = "सत्यापित (मैप किया गया)"
                    match_desc = "मैन्युअल मैपिंग द्वारा सत्यापित (Manual Verified Mapping)"
                    voter_info = {
                        "id": manual_map.get("voter_id"),
                        "epic_no": manual_map.get("epic_no") or "उपलब्ध नहीं",
                        "part_no": manual_map.get("part_no"),
                        "serial_no": manual_map.get("serial_no"),
                        "voter_name": manual_map.get("voter_name") or m_name,
                        "voter_relative": m_rel,
                        "relation_type": "पिता",
                        "voter_house": house.get("houseNumber") or "",
                        "voter_age": m.get("age"),
                        "voter_gender": m.get("gender"),
                        "caste_key": None,
                        "caste_label": None,
                        "is_manual_mapped": True
                    }
                else:
                    # 2. Automated global phonetic matching
                    is_reg, v_match, match_desc = cls.match_member_against_all_voters(
                        m_name, m_rel, prepped_voters, gender_hint=m_gender
                    )

                    if is_reg and v_match:
                        total_registered += 1
                        house_registered += 1
                        status = "registered"
                        status_label = "वोट बना हुआ है"
                        status_badge = "success"
                        voter_info = {
                            "id": v_match.get("id"),
                            "epic_no": v_match.get("epic_no") or "उपलब्ध नहीं",
                            "part_no": v_match.get("part_no"),
                            "serial_no": v_match.get("serial_no"),
                            "voter_name": v_match.get("name"),
                            "voter_relative": v_match.get("relation_name"),
                            "relation_type": v_match.get("relation_type") or "पिता",
                            "voter_house": v_match.get("house_no") or "",
                            "voter_age": v_match.get("age"),
                            "voter_gender": v_match.get("gender"),
                            "caste_key": v_match.get("caste_key"),
                            "caste_label": v_match.get("caste_reason") or v_match.get("caste_key"),
                            "is_manual_mapped": False
                        }
                        action_needed = "सत्यापित (पंजीकृत)"
                    else:
                        total_unregistered += 1
                        house_unregistered += 1
                        status = "unregistered"
                        status_badge = "danger"
                        voter_info = None

                        if m_age >= 18:
                            form6_18plus += 1
                            status_label = "वोट नहीं बना (18+ पात्र)"
                            action_needed = "फॉर्म 6 (नया मतदाता आवेदन पत्र)"
                        else:
                            form6_17plus += 1
                            status_label = "वोट नहीं बना (17+ अग्रिम पात्र)"
                            action_needed = "अग्रिम फॉर्म 6 (17+ युवा अग्रिम पंजीकरण)"

                annotated_members.append({
                    **m,
                    "isRegistered": is_reg,
                    "status": status,
                    "statusLabel": status_label,
                    "statusBadge": status_badge,
                    "actionNeeded": action_needed,
                    "matchDescription": match_desc,
                    "voterRecord": voter_info
                })

            annotated_houses.append({
                **house,
                "houseRegisteredCount": house_registered,
                "houseUnregisteredCount": house_unregistered,
                "hasUnregistered": house_unregistered > 0,
                "eligibleMembers": annotated_members
            })

        return {
            "success": True,
            "street": street_data.get("street"),
            "zone": street_data.get("zone"),
            "ward": street_data.get("ward"),
            "totalHouses": len(annotated_houses),
            "totalEligibleMembers": total_eligible,
            "totalRegistered": total_registered,
            "totalUnregistered": total_unregistered,
            "form6_18plusCount": form6_18plus,
            "form6_17plusCount": form6_17plus,
            "houses": annotated_houses
        }
