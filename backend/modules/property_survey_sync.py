"""
Property Survey Sync & Global Phonetic Voter Audit Module.
Bridges NPPropertyServey (Firebase Firestore / Cloud & Next.js API) with UP Voter List Converter.

Dual-Engine Architecture:
1. Direct Firebase Firestore Cloud Engine: Uses serviceAccountKey.json to query live Nagar Panchayat
   survey & family collections directly with zero dependency on Node.js or port 9002.
2. Local Next.js API Engine (port 9002): Used if the dev server is actively running.
3. Offline Survey Cache: Stores streets and survey manifests locally so audit works even offline.

Phonetic Matching Rule:
"सदस्य का नाम व संबंधी का नाम से ध्वन्यात्मक मिलान से ही सम्पूर्ण वोटर डेटाबेस मे करना है
 क्योंकि मकान नंबर एव वार्ड नंबर वोटर लिस्ट मे मैच नहीं हो पाएंगे।"
"""

import os
import re
import time
import json
import base64
import requests
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict

from ..config import BASE_DIR, DATA_DIR
from .ai_search import normalize_devanagari, transliterate_latin_to_hindi, get_phonetic_key
from .local_caste_ai import (
    clean_and_normalize_name,
    are_names_related,
    _get_voter_field
)

NP_SURVEY_API_URL = os.getenv("NP_SURVEY_API_URL", "http://127.0.0.1:9002/api/voter-sync")
CACHE_STREETS_FILE = DATA_DIR / "survey_cache_streets.json"


# =============================================================================
# KINSHIP & HOUSEHOLD RESOLVER HELPERS (Mirrored from NPPropertyServey route.ts)
# =============================================================================

def is_invalid_name(val: Any) -> bool:
    if not val:
        return True
    s = str(val).strip().lower()
    return not s or s in ("अज्ञात", "unknown", "--", "n/a", "null", "undefined", ".", "-")


def resolve_owner_full_name(survey: Dict[str, Any], fam: Optional[Dict[str, Any]] = None) -> str:
    if not survey and not fam:
        return "उपलब्ध नहीं"

    # 1. Try ownerMemberId link in family members
    if fam and survey.get("ownerMemberId") and isinstance(fam.get("members"), list):
        owner_id = survey["ownerMemberId"]
        for m in fam["members"]:
            if isinstance(m, dict) and m.get("memberId") == owner_id and not is_invalid_name(m.get("name")):
                return str(m["name"]).strip()

    # 2. Try structured head name in family record
    if fam:
        parts = [fam.get("headFirstName"), fam.get("headMiddleName"), fam.get("headLastName")]
        valid_parts = [str(p).strip() for p in parts if not is_invalid_name(p)]
        if valid_parts:
            return " ".join(valid_parts)

    # 3. Try survey direct fields (firstName, middleName, lastName)
    if survey:
        parts = [survey.get("firstName"), survey.get("middleName"), survey.get("lastName")]
        valid_parts = [str(p).strip() for p in parts if not is_invalid_name(p)]
        if valid_parts:
            return " ".join(valid_parts)

        for fld in ("ownerName", "name", "headName"):
            val = survey.get(fld)
            if not is_invalid_name(val):
                return str(val).strip()

        owner_obj = survey.get("owner")
        if isinstance(owner_obj, dict) and not is_invalid_name(owner_obj.get("firstName")):
            return f"{owner_obj.get('firstName', '')} {owner_obj.get('lastName', '')}".strip()

    # 4. Try member marked with isOwner or relationship 'स्वयं' / 'मुखिया'
    if fam and isinstance(fam.get("members"), list):
        for m in fam["members"]:
            if isinstance(m, dict) and m.get("isOwner") and not is_invalid_name(m.get("name")):
                return str(m["name"]).strip()
        for m in fam["members"]:
            if isinstance(m, dict):
                r = str(m.get("relationship") or "").lower().strip()
                if r in ("स्वयं", "मुखिया", "self", "head") and not is_invalid_name(m.get("name")):
                    return str(m["name"]).strip()

    return "उपलब्ध नहीं"


def resolve_member_father_husband_name(
    member: Dict[str, Any],
    m_idx: int,
    all_members: List[Dict[str, Any]],
    fam: Optional[Dict[str, Any]],
    head_name: str,
    head_father: str,
    head_gender: str
) -> str:
    raw = (member.get("fatherHusbandName") or "").strip()
    rel = (member.get("relationship") or "").strip().lower()

    has_wife_member = any(
        str(x.get("relationship") or "").strip().lower() in ("पत्नी", "wife")
        for x in all_members if isinstance(x, dict)
    )
    husband_member = next(
        (x for x in all_members if isinstance(x, dict) and str(x.get("relationship") or "").strip().lower() in ("पति", "husband")),
        None
    )

    female_markers = (
        "devi", "kumari", "begum", "bano", "shrimati", "smt", "bala", "vati", "rani",
        "देवी", "कुमारी", "बेगम", "बानो", "श्रीमती", "बाला", "वती", "रानी"
    )
    is_head_female_by_name = any(m in head_name.lower() for m in female_markers)

    male_given_markers = (
        "ram", "kumar", "singh", "lal", "prasad", "kant", "prakash", "chandra", "datt",
        "pal", "veer", "nath", "gopal", "mohan", "shyam", "sundar", "babu", "das",
        "kishore", "vardhan", "sen", "raj", "deep", "anand", "bhai"
    )
    has_male_given_marker = any(m in head_name.lower() for m in male_given_markers)

    owner_member = next(
        (x for x in all_members if isinstance(x, dict) and (x.get("isOwner") or str(x.get("relationship") or "").strip().lower() in ("स्वयं", "मुखिया", "self", "head"))),
        None
    )

    if husband_member:
        is_head_male = False
    elif has_wife_member:
        is_head_male = True
    elif is_head_female_by_name:
        is_head_male = False
    elif (owner_member and owner_member.get("gender") == "महिला") or head_gender == "महिला":
        is_head_male = has_male_given_marker
    else:
        is_head_male = True

    # 1. Daughter-in-law
    if rel in ("पुत्र बधु", "पुत्रवधू", "बहू", "daughter-in-law"):
        if not is_invalid_name(raw) and raw.lower() != head_name.lower() and raw.lower() != head_father.lower():
            return raw

        if m_idx > 0:
            prev = all_members[m_idx - 1]
            prev_rel = str(prev.get("relationship") or "").strip().lower()
            if prev_rel in ("पुत्र", "बेटा", "son") and prev.get("gender") == "पुरुष" and not is_invalid_name(prev.get("name")):
                return str(prev["name"]).strip()

        for i in range(m_idx - 1, -1, -1):
            prev = all_members[i]
            prev_rel = str(prev.get("relationship") or "").strip().lower()
            if prev_rel in ("पुत्र", "बेटा", "son") and prev.get("gender") == "पुरुष" and not is_invalid_name(prev.get("name")):
                return str(prev["name"]).strip()

        for i in range(m_idx + 1, len(all_members)):
            nxt = all_members[i]
            nxt_rel = str(nxt.get("relationship") or "").strip().lower()
            if nxt_rel in ("पुत्र", "बेटा", "son") and nxt.get("gender") == "पुरुष" and not is_invalid_name(nxt.get("name")):
                return str(nxt["name"]).strip()

        d_age = int(member.get("age") or 0)
        all_sons = [
            x for x in all_members
            if isinstance(x, dict) and str(x.get("relationship") or "").strip().lower() in ("पुत्र", "बेटा", "son")
            and x.get("gender") == "पुरुष" and not is_invalid_name(x.get("name"))
        ]
        if all_sons:
            if d_age > 0:
                all_sons.sort(key=lambda a: abs(int(a.get("age") or 0) - d_age))
            return str(all_sons[0]["name"]).strip()
        return ""

    # 2. Sons & Daughters
    if rel in ("पुत्र", "पुत्री", "बेटा", "बेटी", "दत्तक पुत्र", "दत्तक पुत्री", "son", "daughter"):
        if not is_invalid_name(raw) and raw.lower() != head_father.lower():
            return raw
        if is_head_male:
            return head_name if head_name and head_name != "उपलब्ध नहीं" else ""
        else:
            if husband_member and not is_invalid_name(husband_member.get("name")):
                return str(husband_member["name"]).strip()
            return head_father or ""

    # 3. Wife
    if rel in ("पत्नी", "wife"):
        return head_name if head_name and head_name != "उपलब्ध नहीं" else ""

    # 4. Husband
    if rel in ("पति", "husband"):
        return head_father or ""

    # 5. Self / Head
    if member.get("isOwner") or rel in ("स्वयं", "मुखिया", "self", "head"):
        return head_father or ""

    # 6. Grandchildren
    if rel in ("पोता", "पोती", "grandson", "granddaughter"):
        if not is_invalid_name(raw) and raw.lower() != head_father.lower() and raw.lower() != head_name.lower():
            return raw
        for i in range(m_idx - 1, -1, -1):
            prev = all_members[i]
            prev_rel = str(prev.get("relationship") or "").strip().lower()
            if prev_rel in ("पुत्र", "बेटा", "son") and prev.get("gender") == "पुरुष" and not is_invalid_name(prev.get("name")):
                return str(prev["name"]).strip()
        all_sons = [
            x for x in all_members
            if isinstance(x, dict) and str(x.get("relationship") or "").strip().lower() in ("पुत्र", "बेटा", "son")
            and x.get("gender") == "पुरुष" and not is_invalid_name(x.get("name"))
        ]
        if all_sons:
            return str(all_sons[0]["name"]).strip()
        return head_name

    # 7. Brother / Sister
    if rel in ("भाई", "बहन", "brother", "sister"):
        return head_father or ""

    # 8. Mother
    if rel in ("माता", "माँ", "mother"):
        return head_father or ""

    # Fallback
    if not is_invalid_name(raw):
        return raw
    if is_head_male:
        return head_name
    return head_father or head_name or ""


# =============================================================================
# DIRECT FIREBASE FIRESTORE CLOUD ENGINE (STANDALONE ZERO-PORT-9002 REQUIRED)
# =============================================================================

class FirestoreDirectClient:
    """Connects directly to Google Cloud Firestore using the project service account key."""

    _instance: Optional["FirestoreDirectClient"] = None

    @classmethod
    def find_service_account_path(cls) -> Optional[Path]:
        candidates = [
            DATA_DIR / "serviceAccountKey.json",
            BASE_DIR / "serviceAccountKey.json",
            Path(r"C:\Users\HP\Downloads\WebApp\NPPropertyServey\serviceAccountKey.json"),
            Path(r"C:\UP_Voter_Service\data\serviceAccountKey.json")
        ]
        for p in candidates:
            if p.exists():
                return p
        return None

    @classmethod
    def get_instance(cls) -> Optional["FirestoreDirectClient"]:
        if cls._instance is None:
            p = cls.find_service_account_path()
            if p:
                try:
                    cls._instance = FirestoreDirectClient(p)
                except Exception as e:
                    print(f"[FirestoreDirectClient] Init warning: {e}")
                    cls._instance = None
        return cls._instance

    def __init__(self, key_path: Path):
        with open(key_path, "r", encoding="utf-8") as f:
            self.key_data = json.load(f)
        self.project_id = self.key_data.get("project_id", "nagar-property-surveyor")
        self._token = None
        self._token_expiry = 0
        self._cached_streets = None
        self._cached_streets_time = 0

    def get_token(self) -> str:
        now = int(time.time())
        if self._token and now < self._token_expiry - 60:
            return self._token

        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        header = {"alg": "RS256", "typ": "JWT"}
        claim = {
            "iss": self.key_data["client_email"],
            "scope": "https://www.googleapis.com/auth/datastore",
            "aud": "https://oauth2.googleapis.com/token",
            "exp": now + 3600,
            "iat": now
        }

        def b64_url(b: bytes) -> str:
            return base64.urlsafe_b64encode(b).decode("utf-8").rstrip("=")

        h_b64 = b64_url(json.dumps(header).encode("utf-8"))
        c_b64 = b64_url(json.dumps(claim).encode("utf-8"))
        sig_input = f"{h_b64}.{c_b64}".encode("utf-8")

        priv_key = load_pem_private_key(self.key_data["private_key"].encode("utf-8"), password=None)
        sig = priv_key.sign(sig_input, padding.PKCS1v15(), hashes.SHA256())
        sig_b64 = b64_url(sig)

        jwt = f"{h_b64}.{c_b64}.{sig_b64}"
        data = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt
        }).encode("utf-8")

        req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            tdata = json.loads(resp.read().decode("utf-8"))
            self._token = tdata["access_token"]
            self._token_expiry = now + int(tdata.get("expires_in", 3600))
            return self._token

    def _parse_fs_val(self, v: Any) -> Any:
        if not isinstance(v, dict):
            return v
        if "stringValue" in v: return v["stringValue"]
        if "integerValue" in v: return int(v["integerValue"])
        if "doubleValue" in v: return float(v["doubleValue"])
        if "booleanValue" in v: return v["booleanValue"]
        if "timestampValue" in v: return v["timestampValue"]
        if "nullValue" in v: return None
        if "mapValue" in v:
            fields = v["mapValue"].get("fields", {})
            return {k: self._parse_fs_val(val) for k, val in fields.items()}
        if "arrayValue" in v:
            vals = v["arrayValue"].get("values", [])
            return [self._parse_fs_val(val) for val in vals]
        return v

    def parse_doc(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        doc_id = doc.get("name", "").split("/")[-1]
        res = {"id": doc_id}
        for k, v in doc.get("fields", {}).items():
            res[k] = self._parse_fs_val(v)
        return res

    def run_query(self, structured_query: Dict[str, Any]) -> List[Dict[str, Any]]:
        tok = self.get_token()
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents:runQuery"
        req = urllib.request.Request(
            url,
            data=json.dumps({"structuredQuery": structured_query}).encode("utf-8"),
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=40) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = []
            for item in data:
                doc = item.get("document")
                if doc:
                    results.append(self.parse_doc(doc))
            return results

    def batch_get_families(self, family_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not family_ids:
            return {}
        fam_map = {}
        chunk_size = 30
        tok = self.get_token()
        url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents:batchGet"
        for i in range(0, len(family_ids), chunk_size):
            chunk = family_ids[i:i + chunk_size]
            doc_paths = [f"projects/{self.project_id}/databases/(default)/documents/families/{fid}" for fid in chunk]
            req = urllib.request.Request(
                url,
                data=json.dumps({"documents": doc_paths}).encode("utf-8"),
                headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    for item in json.loads(resp.read().decode("utf-8")):
                        if "found" in item:
                            d = self.parse_doc(item["found"])
                            fam_map[d["id"]] = d
            except Exception as e:
                print(f"[batchGet] Chunk fetch warning: {e}")
        return fam_map

    def fetch_all_streets(self) -> Dict[str, Any]:
        now = time.time()
        if self._cached_streets and (now - self._cached_streets_time < 300):
            return self._cached_streets

        q = {
            "from": [{"collectionId": "surveys"}],
            "select": {
                "fields": [
                    {"fieldPath": "zone"},
                    {"fieldPath": "ward"},
                    {"fieldPath": "street"},
                    {"fieldPath": "isActive"},
                    {"fieldPath": "isDeleted"}
                ]
            }
        }
        docs = self.run_query(q)

        zones_set = set()
        wards_set = set()
        streets_set = set()
        streets_by_zone = defaultdict(list)
        streets_by_ward = defaultdict(list)

        for d in docs:
            if d.get("isActive") is False or d.get("isDeleted") is True:
                continue
            zone = (d.get("zone") or "").strip()
            ward = (d.get("ward") or "").strip()
            street = (d.get("street") or "").strip()

            if zone: zones_set.add(zone)
            if ward: wards_set.add(ward)
            if street:
                streets_set.add(street)
                if zone and street not in streets_by_zone[zone]:
                    streets_by_zone[zone].append(street)
                if ward and street not in streets_by_ward[ward]:
                    streets_by_ward[ward].append(street)

        def natural_sort_key(s: str):
            digits = "".join(c for c in s if c.isdigit())
            return (int(digits) if digits else 9999, s)

        zones = sorted(list(zones_set), key=natural_sort_key)
        wards = sorted(list(wards_set), key=natural_sort_key)
        all_streets = sorted(list(streets_set), key=natural_sort_key)

        for z in streets_by_zone:
            streets_by_zone[z] = sorted(streets_by_zone[z], key=natural_sort_key)
        for w in streets_by_ward:
            streets_by_ward[w] = sorted(streets_by_ward[w], key=natural_sort_key)

        res = {
            "success": True,
            "source": "firebase_cloud",
            "sourceLabel": "🟢 लाइव क्लाउड सर्वे (Firebase Firestore)",
            "totalSurveys": len(docs),
            "zones": zones,
            "streetsByZone": dict(streets_by_zone),
            "allStreets": all_streets,
            "wards": wards,
            "streetsByWard": dict(streets_by_ward)
        }
        self._cached_streets = res
        self._cached_streets_time = now

        # Persist to local cache for offline usage
        try:
            CACHE_STREETS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CACHE_STREETS_FILE, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False)
        except Exception:
            pass

        return res

    def fetch_street_data(
        self,
        street: str,
        zone: Optional[str] = None,
        ward: Optional[str] = None,
        min_age: int = 17
    ) -> Dict[str, Any]:
        filters = [
            {
                "fieldFilter": {
                    "field": {"fieldPath": "street"},
                    "op": "EQUAL",
                    "value": {"stringValue": street.strip()}
                }
            }
        ]
        if zone:
            filters.append({
                "fieldFilter": {
                    "field": {"fieldPath": "zone"},
                    "op": "EQUAL",
                    "value": {"stringValue": zone.strip()}
                }
            })

        if len(filters) == 1:
            where_clause = filters[0]
        else:
            where_clause = {
                "compositeFilter": {
                    "op": "AND",
                    "filters": filters
                }
            }

        q = {
            "from": [{"collectionId": "surveys"}],
            "where": where_clause
        }
        raw_surveys = self.run_query(q)

        def house_sort_key(item: Dict[str, Any]):
            h = str(item.get("houseNumber") or "").strip()
            num = ""
            for c in h:
                if c.isdigit(): num += c
                else: break
            return (int(num) if num else 99999, h)

        raw_surveys.sort(key=house_sort_key)

        fids = list(set(s.get("familyId") for s in raw_surveys if s.get("familyId")))
        families_map = self.batch_get_families(fids)

        total_eligible_count = 0
        houses = []

        for item in raw_surveys:
            if item.get("isActive") is False or item.get("isDeleted") is True:
                continue

            fam = families_map.get(item.get("familyId")) if item.get("familyId") else None
            resolved_owner = resolve_owner_full_name(item, fam)

            head_father = (
                (fam.get("headFatherHusbandName") if fam else None) or
                item.get("fatherHusbandName") or
                item.get("headFatherHusbandName") or
                ""
            ).strip()

            head_gender = (item.get("ownerGender") or "").strip()
            if not head_gender and fam and isinstance(fam.get("members"), list):
                hm = next((
                    m for m in fam["members"]
                    if isinstance(m, dict) and (m.get("isOwner") or m.get("memberId") == item.get("ownerMemberId") or str(m.get("relationship") or "").strip() in ("स्वयं", "मुखिया", "self", "head"))
                ), None)
                if hm and hm.get("gender"):
                    head_gender = str(hm["gender"]).strip()
            if not head_gender:
                head_gender = "पुरुष"

            raw_members = (fam.get("members") if fam and isinstance(fam.get("members"), list) else None) or item.get("familyMembers") or []
            eligible_members = []

            for m_idx, m in enumerate(raw_members):
                if not isinstance(m, dict):
                    continue
                member_father_husband = resolve_member_father_husband_name(
                    m, m_idx, raw_members, fam, resolved_owner, head_father, head_gender
                )
                try:
                    age_val = int(m.get("age") or 0)
                except (ValueError, TypeError):
                    age_val = 0

                if age_val >= min_age:
                    total_eligible_count += 1
                    eligible_members.append({
                        "name": str(m.get("name") or "").strip(),
                        "fatherHusbandName": member_father_husband,
                        "age": age_val,
                        "gender": m.get("gender") or "अन्य",
                        "relationship": m.get("relationship") or "सदस्य",
                        "mobile": m.get("mobile") or (fam.get("mobile") if fam else None) or "",
                        "memberId": m.get("memberId") or "",
                        "isOwner": bool(m.get("isOwner")),
                        "isVoterRegistered": bool(m.get("isVoterRegistered")),
                        "voterEpic": m.get("voterEpic") or "",
                        "voterPartNo": str(m.get("voterPartNo") or ""),
                        "voterSerialNo": str(m.get("voterSerialNo") or ""),
                        "voterName": m.get("voterName") or "",
                        "voterMappedAt": m.get("voterMappedAt") or ""
                    })

            houses.append({
                "id": item.get("id"),
                "houseNumber": item.get("houseNumber") or "1",
                "propertyId": item.get("propertyId") or "",
                "address": item.get("address") or "",
                "zone": item.get("zone") or zone or "",
                "ward": item.get("ward") or ward or "",
                "street": item.get("street") or street,
                "ownerName": resolved_owner,
                "headName": resolved_owner,
                "headFatherHusbandName": head_father,
                "gpsLocation": item.get("gpsLocation") or (item.get("propertyDetails", {}).get("gpsLocation") if isinstance(item.get("propertyDetails"), dict) else None),
                "familyId": item.get("familyId") or "",
                "propertyType": item.get("propertyType") or "",
                "eligibleMembersCount": len(eligible_members),
                "eligibleMembers": eligible_members
            })

        return {
            "success": True,
            "source": "firebase_cloud",
            "sourceLabel": "🟢 लाइव क्लाउड सर्वे (Firebase Firestore)",
            "street": street,
            "zone": zone,
            "ward": ward,
            "totalHouses": len(houses),
            "totalEligibleMembers": total_eligible_count,
            "houses": houses
        }

    def save_voter_mapping(
        self,
        family_id: str,
        member_id: str,
        voter_epic: str,
        voter_part_no: str,
        voter_serial_no: str,
        voter_name: str,
        is_registered: bool = True
    ) -> Dict[str, Any]:
        tok = self.get_token()
        doc_url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/families/{family_id}"
        req = urllib.request.Request(doc_url, headers={"Authorization": f"Bearer {tok}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                doc = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"success": False, "error": f"Family not found in Firestore: {str(e)}"}

        fields = doc.get("fields", {})
        members_val = fields.get("members", {}).get("arrayValue", {}).get("values", [])
        found = False
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

        for m_wrap in members_val:
            m_fields = m_wrap.get("mapValue", {}).get("fields", {})
            mid = m_fields.get("memberId", {}).get("stringValue", "")
            if mid == member_id:
                found = True
                m_fields["isVoterRegistered"] = {"booleanValue": is_registered}
                m_fields["voterEpic"] = {"stringValue": voter_epic or ""}
                m_fields["voterPartNo"] = {"stringValue": str(voter_part_no or "")}
                m_fields["voterSerialNo"] = {"stringValue": str(voter_serial_no or "")}
                m_fields["voterName"] = {"stringValue": voter_name or ""}
                m_fields["voterMappedAt"] = {"stringValue": now_iso}

        if not found:
            return {"success": False, "error": "Member not found in family"}

        fields["members"] = {"arrayValue": {"values": members_val}}
        patch_url = f"{doc_url}?updateMask.fieldPaths=members"
        patch_req = urllib.request.Request(
            patch_url,
            data=json.dumps({"fields": {"members": fields["members"]}}).encode("utf-8"),
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
            method="PATCH"
        )
        try:
            with urllib.request.urlopen(patch_req, timeout=20) as presp:
                return {"success": True, "message": "Voter mapping synced to Firestore successfully"}
        except Exception as pe:
            return {"success": False, "error": f"Failed to patch family in Firestore: {str(pe)}"}

    def delete_voter_mapping(self, family_id: str, member_id: str) -> Dict[str, Any]:
        tok = self.get_token()
        doc_url = f"https://firestore.googleapis.com/v1/projects/{self.project_id}/databases/(default)/documents/families/{family_id}"
        req = urllib.request.Request(doc_url, headers={"Authorization": f"Bearer {tok}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                doc = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"success": False, "error": f"Family not found in Firestore: {str(e)}"}

        fields = doc.get("fields", {})
        members_val = fields.get("members", {}).get("arrayValue", {}).get("values", [])

        for m_wrap in members_val:
            m_fields = m_wrap.get("mapValue", {}).get("fields", {})
            mid = m_fields.get("memberId", {}).get("stringValue", "")
            if mid == member_id:
                m_fields["isVoterRegistered"] = {"booleanValue": False}
                for fld in ("voterEpic", "voterPartNo", "voterSerialNo", "voterName", "voterMappedAt"):
                    if fld in m_fields:
                        m_fields[fld] = {"stringValue": ""}

        fields["members"] = {"arrayValue": {"values": members_val}}
        patch_url = f"{doc_url}?updateMask.fieldPaths=members"
        patch_req = urllib.request.Request(
            patch_url,
            data=json.dumps({"fields": {"members": fields["members"]}}).encode("utf-8"),
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
            method="PATCH"
        )
        try:
            with urllib.request.urlopen(patch_req, timeout=20) as presp:
                return {"success": True, "message": "Voter mapping unlinked in Firestore successfully"}
        except Exception as pe:
            return {"success": False, "error": f"Failed to patch family in Firestore: {str(pe)}"}


# =============================================================================
# MAIN ORCHESTRATOR: PROPERTY SURVEY SYNC (DUAL ENGINE)
# =============================================================================

class PropertySurveySync:
    """Handles communication with NPPropertyServey via Local Port 9002 OR Direct Firebase Cloud."""

    @classmethod
    def fetch_streets(cls, api_url: Optional[str] = None) -> Dict[str, Any]:
        """
        Dual-Engine Streets Fetch:
        1. Tries local port 9002 (if actively running).
        2. Automatically falls back to Direct Firebase Firestore Cloud connection.
        3. Automatically falls back to Local Cache file if offline.
        """
        # Engine 1: Try Local Port 9002 (Short 1.2s timeout to avoid lag)
        url = (api_url or NP_SURVEY_API_URL) + "?action=get_streets"
        try:
            resp = requests.get(url, timeout=1.2)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    data["source"] = "local_api"
                    data["sourceLabel"] = "🟢 स्थानीय NP सर्वे ऐप (पोर्ट 9002)"
                    return data
        except Exception:
            pass  # Expected if port 9002 is not running

        # Engine 2: Direct Firebase Firestore Cloud
        fs_client = FirestoreDirectClient.get_instance()
        if fs_client:
            try:
                res = fs_client.fetch_all_streets()
                if res.get("success"):
                    return res
            except Exception as fe:
                print(f"[PropertySurveySync] Cloud fetch error: {fe}")

        # Engine 3: Offline Cache Fallback
        if CACHE_STREETS_FILE.exists():
            try:
                with open(CACHE_STREETS_FILE, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                cached["source"] = "offline_cache"
                cached["sourceLabel"] = "🟡 ऑफ़लाइन कैश्ड सर्वे डेटा"
                return cached
            except Exception:
                pass

        return {
            "success": False,
            "error": "सर्वे डेटा प्राप्त नहीं हो सका। कृपया इंटरनेट कनेक्शन या सर्विस अकाउंट की जांच करें।"
        }

    @classmethod
    def fetch_street_houses_and_members(
        cls,
        street: str,
        zone: Optional[str] = None,
        ward: Optional[str] = None,
        min_age: int = 17,
        api_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Dual-Engine Street Data Fetch:
        1. Tries local port 9002 if running.
        2. Automatically falls back to Direct Firebase Firestore Cloud.
        """
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

        # Engine 1: Try Local Port 9002
        try:
            resp = requests.get(base_url, params=params, timeout=1.5)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    data["source"] = "local_api"
                    data["sourceLabel"] = "🟢 स्थानीय NP सर्वे ऐप (पोर्ट 9002)"
                    return data
        except Exception:
            pass

        # Engine 2: Direct Firebase Firestore Cloud
        fs_client = FirestoreDirectClient.get_instance()
        if fs_client:
            try:
                res = fs_client.fetch_street_data(street=street, zone=zone, ward=ward, min_age=min_age)
                if res.get("success"):
                    return res
            except Exception as fe:
                return {
                    "success": False,
                    "error": f"क्लाउड फायरस्टोर से गली डेटा लोड विफल: {str(fe)}"
                }

        return {
            "success": False,
            "error": "गली का सर्वे डेटा प्राप्त नहीं हो सका।"
        }

    @classmethod
    def sync_mapping_to_np_survey(cls, mapping_data: Dict[str, Any], api_url: Optional[str] = None) -> Dict[str, Any]:
        """Dual-Engine save mapping: Tries local 9002, then falls back to Direct Firestore."""
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
        # Try local 9002
        try:
            resp = requests.post(url, json=payload, timeout=2.0)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass

        # Fallback to Direct Firestore
        fs_client = FirestoreDirectClient.get_instance()
        if fs_client:
            try:
                return fs_client.save_voter_mapping(
                    family_id=mapping_data.get("family_id", ""),
                    member_id=mapping_data.get("member_id", ""),
                    voter_epic=mapping_data.get("epic_no", ""),
                    voter_part_no=str(mapping_data.get("part_no") or ""),
                    voter_serial_no=mapping_data.get("serial_no", ""),
                    voter_name=mapping_data.get("voter_name", ""),
                    is_registered=True
                )
            except Exception as ex:
                return {"success": False, "error": str(ex)}

        return {"success": False, "error": "मैपिंग सिंक करने हेतु कोई सक्रिय कनेक्शन नहीं मिला।"}

    @classmethod
    def delete_mapping_from_np_survey(cls, family_id: str, member_id: str, api_url: Optional[str] = None) -> Dict[str, Any]:
        """Dual-Engine delete mapping: Tries local 9002, then falls back to Direct Firestore."""
        url = api_url or NP_SURVEY_API_URL
        payload = {
            "action": "delete_voter_mapping",
            "familyId": family_id,
            "memberId": member_id
        }
        try:
            resp = requests.post(url, json=payload, timeout=2.0)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass

        fs_client = FirestoreDirectClient.get_instance()
        if fs_client:
            try:
                return fs_client.delete_voter_mapping(family_id=family_id, member_id=member_id)
            except Exception as ex:
                return {"success": False, "error": str(ex)}

        return {"success": False, "error": "अनलिंक करने हेतु कोई सक्रिय कनेक्शन नहीं मिला।"}

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
        """
        m_str = (member_name or "").strip()
        r_str = (relative_name or "").strip()

        if not m_str:
            return False, None, "सदस्य का नाम रिक्त है"

        # Detect Latin / English characters in surveyor input
        has_latin_m = bool(re.search(r'[a-zA-Z]', m_str))
        has_latin_r = bool(re.search(r'[a-zA-Z]', r_str))

        if has_latin_m:
            trans_m = transliterate_latin_to_hindi(m_str)
            clean_m = clean_and_normalize_name(trans_m)
        else:
            clean_m = clean_and_normalize_name(m_str)

        if has_latin_r:
            trans_r = transliterate_latin_to_hindi(r_str)
            clean_r = clean_and_normalize_name(trans_r)
        else:
            clean_r = clean_and_normalize_name(r_str)

        norm_m_primary = normalize_devanagari(clean_m)
        norm_r_primary = normalize_devanagari(clean_r) if clean_r else ""
        phon_m = get_phonetic_key(clean_m)
        phon_r = get_phonetic_key(clean_r) if clean_r else ""

        norm_m_list = [norm_m_primary]
        norm_r_list = [norm_r_primary] if norm_r_primary else []

        if has_latin_m:
            raw_m_clean = clean_and_normalize_name(m_str)
            if raw_m_clean != norm_m_primary:
                norm_m_list.append(raw_m_clean)
        if has_latin_r and r_str:
            raw_r_clean = clean_and_normalize_name(r_str)
            if raw_r_clean != norm_r_primary:
                norm_r_list.append(raw_r_clean)

        best_candidate = None
        best_score = 0

        is_prepped = isinstance(all_voters, dict) and "by_token" in all_voters

        if is_prepped:
            candidate_set = set()
            for tok in norm_m_primary.split():
                if len(tok) >= 2 and tok in all_voters["by_token"]:
                    for it in all_voters["by_token"][tok]:
                        candidate_set.add(id(it))
            if not candidate_set and phon_m:
                for pt in phon_m.split():
                    lead = pt[:3]
                    if len(lead) >= 2 and lead in all_voters["by_phon_lead"]:
                        for it in all_voters["by_phon_lead"][lead]:
                            candidate_set.add(id(it))
            if not candidate_set:
                candidate_list = all_voters["items"]
            else:
                candidate_list = [it for it in all_voters["items"] if id(it) in candidate_set]
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
    def audit_street_voters(
        cls,
        street_data: Dict[str, Any],
        all_voters: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Takes raw street survey data,
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
            "source": street_data.get("source", "firebase_cloud"),
            "sourceLabel": street_data.get("sourceLabel", "🟢 लाइव क्लाउड सर्वे (Firebase Firestore)"),
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
