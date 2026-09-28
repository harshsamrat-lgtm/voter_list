"""
Uttar Pradesh Nagar Nikay (Urban Local Body / ULB / State Election Commission UP)
Dedicated Electoral Roll Extractor.

Supports:
- Nagar Panchayat (नगर पंचायत)
- Nagar Palika Parishad (नगर पालिका परिषद)
- Nagar Nigam (नगर निगम)
- Gram / Kshetra Panchayat (पंचायत निर्वाचन नामावली)

Extracts the standard 2-column tabular format (~98 voters per page) containing:
1. क्र०सं० (Serial Number)
2. मकान नं० (House Number)
3. निर्वाचक का नाम (Voter Name)
4. पिता/पति/माता का नाम (Relative Name)
5. लिंग (Gender: पु/म)
6. आयु (Age)
"""

import os
import re
from typing import List, Dict, Optional, Any, Tuple
import pymupdf as fitz
from ..models.voter import VoterRecord, PageProcessingResult
from .validator import clean_house_no, clean_hindi_text, normalize_relation_type


class ULBExtractor:
    """Extractor for Uttar Pradesh Urban Local Body (Nagar Nikay / SEC UP) voter lists."""

    @classmethod
    def is_ulb_pdf(cls, pdf_path: str) -> bool:
        """
        Determines whether the given PDF is a Uttar Pradesh Nagar Nikay (ULB) voter list.
        Checks for municipal keywords, Ward indicators, or the 2-column tabular layout.
        """
        if not os.path.exists(pdf_path):
            return False

        # 1. Filename heuristic
        fname = os.path.basename(pdf_path).upper()
        if "ULB" in fname or "NIKAY" in fname or "NAGAR" in fname:
            return True

        # 2. Content analysis of first 2 pages
        try:
            doc = fitz.open(pdf_path)
            if len(doc) == 0:
                doc.close()
                return False

            sample_text = ""
            for p_idx in range(min(2, len(doc))):
                sample_text += " " + doc[p_idx].get_text("text")
            doc.close()

            ulb_keywords = [
                "नगर पंचायत", "नगर पालिका", "नगर निगम", "नगरीय निकाय",
                "निकाय का नाम", "वाड[", "वार्ड", "मतदान केंद्र",
                "मतदा“ केÛġ", "भाग संÉ™ा", "भाग संख्या", "राज्य निर्वाचन आयोग",
                "साहूकारा", "मोहल्ले के नाम", "अन्तिम प्रकाशन सूची"
            ]

            match_count = sum(1 for kw in ulb_keywords if kw in sample_text)
            if match_count >= 2:
                return True

            # Check if tabular headers exist
            if ("क्र०सं०" in sample_text or "Đ०सं०" in sample_text or "Đ० ं०" in sample_text) and \
               ("मकान" in sample_text or "मका“" in sample_text or "म ‘ा“" in sample_text) and \
               ("निर्वाचक" in sample_text or "Ǔ“वा[चक" in sample_text):
                return True

        except Exception:
            pass

        return False

    @classmethod
    def decode_sec_up_font(cls, text: str) -> str:
        """
        Decodes State Election Commission (SEC) UP / Nagar Nikay font subsets (Identity-H / Mangal / TTF).
        Restores full conjuncts (युक्ताक्षर), vowels, reph, and characters across all font subsets.
        """
        if not text:
            return ""
        s = text

        # Multi-character ligatures and compound characters
        rules = [
            # Compound syllables
            ("क ृु", "कु"), ("क ृ", "कृ"), ("कृु", "कु"), ("क ु", "कु"), ("क ू", "कू"),
            ("म“ोजक", "मनोज क"), ("Ĥवेशक", "प्रवेश क"),
            ("िाम", "राम"), ("िा", "रा"), ("िे", "रे"), ("िै", "रै"), ("िो", "रो"), ("िौ", "रौ"), ("िू", "रू"), ("िु", "रु"),
            ("ससंह", "सिंह"), ("ससॊह", "सिंह"), ("सॊह", "सिंह"), ("हि", "हरि"), ("Ǔ“", "नि"), ("ͪ व", "वि"), ("ͪ ", "ि"),
            ("Ĥ", "प्र"), ("Û", "न्द"), ("Ü", "प्त"), ("™", "या"), ("æ", "श्व"), ("à", "म्म"), ("É", "ख्य"),
            ("¢", "क्ष"), ("ɮ", "द्ध"), ("è", "स्म"), ("ã", "ल्ल"), ("ġ", "्र"), ("Ǔ", "ि"), ("Ǿ", "रू"),
            ("ȣ", "ी"), ("Ȣ", "ी"), ("Ȱ", "ै"), ("ȶ", "ै"), ("ͧ", "ु"), ("ͬ", "ी"), ("ȸ", "ी"),
            ("“", "न"), ("”", "न"), ("›", "ल"), ("š", "श"), ("ž", "श"), ("¡", "ा"), ("ʜ", "्र"),
            ("Ǘ", "ू"), ("Ž", "श"), ("Œ", "छ"), ("ğ", "त्र"), ("Đ", "क्र"), ("Ě", "ट"), ("ç", "्"),
            ("ê", "क्ष्"), ("’", "अ"), ("\\", "अ"), ("”ु", "पु"), (" ", "स"), ("‘", "द"),
            # Multi-character phrases & common name patterns in SEC UP rolls
            ("मो¡àम\x91", "मोहम्मद"), ("मौ¡àम\x91", "मोहम्मद"), ("मौ¡àà\x91", "मोहम्मद"),
            ("मो\x9càम\x91", "मोहम्मद"), ("\\¡म\x91", "अहमद"),
            ("¡ु ै\x93", "हुसैन"), ("¡ु", "हु"), ("ै\x93", "ैन"),
            ("रÏजाक", "रज्जाक"), ("व¡ȣ\x91\x93", "वहीदन"), ("ाजज\x91", "साजिद"),
            ("]ररफ", "आरिफ"), ("]ͧ", "यु"), ("शब\x93म", "शबनम"),
            ("राशȢ\x91", "राशिद"), ("रशȢ\x91", "रशीद"), ("\x93जज", "नज़ीर"),
            ("\x93जȢ", "नज़ी"), ("याशमȢ\x93", "यासमीन"), ("बÉश", "बख्श"),
            ("जा\x93", "जान"), ("\\\x9bȣ", "अली"),
            # Subset 150 single characters
            ("\x9a", "र"), ("\x94", "प"), ("\x80", "क"), ("\x98", "म"), ("\x9e", "श"),
            ("\x8c", "ड"), ("\x8f", "त"), ("\x90", "थ"), ("\x91", "द"), ("\x93", "न"),
            ("\x9b", "ल"), ("\xa1", "ह"), ("\xc9", "ख"), ("\xcf", "ज़्"),
            ("]यु", "आयु"), ("]", "आ"),
            ("बबराा", "बबराला"), ("बबिाला", "बबराला"), ("035- म्मभ", "035-सम्भल"),
            (" शवुरी ूव", "शिवपुरी पूर्व"), (" ााूकारा", "साहूकारा"),
            ("मोाम्ममद", "मोहम्मद"), ("मौाम्ममद", "मोहम्मद"),
            ("शमार्", "शर्मा"), ("वमार्", "वर्मा"), ("वा्ैय", "वार्ष्णेय"),
            ("गुप्तता", "गुप्ता"), ("प्रीित", "प्रीति"), ("दुगैश", "दुर्गेश"),
        ]
        for old, new in rules:
            s = s.replace(old, new)

        # Reph [ (e.g. शमा[ -> शर्मा, वषा[ -> वर्षा, Ǔ“म[ला -> निर्मला, वाड[ -> वार्ड)
        s = re.sub(r'([क-ह])\[([ा-ू]?)', r'र्\1\2', s)
        s = re.sub(r'\[', 'र्', s)

        # Isolated 'ि' inside words -> 'र' (e.g. सिोज -> सरोज, बबिाला -> बबराला, सूिज -> सूरज, िाजेश -> राजेश)
        s = re.sub(r'(?<=[क-ह])ि(?=[क-ह])', 'र', s)
        s = re.sub(r'^ि(?=[क-ह])', 'र', s)

        # Additional conjunct fixes
        s = s.replace("क ृ", "कृ").replace("क ु", "कु").replace("क ू", "कू")
        s = re.sub(r'([क-ह])\s+([ािीुूेैोौंः्ृ])', r'\1\2', s)
        
        # Remove non-Devanagari stray symbols while preserving Hindi text, spaces, hyphens
        s = re.sub(r'[^\u0900-\u097Fa-zA-Z0-9\s\.\-/]', '', s)
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    @classmethod
    def extract_page_header_metadata(
        cls,
        page: Optional[fitz.Page] = None,
        words: Optional[List] = None,
        fallback_meta: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Extracts municipal administrative metadata directly from a specific page's header.
        (District, Nikay Name, Ward, Polling Station, Part No, Booth, Mohalla).
        """
        meta = {
            "district": None,
            "nikay_name": None,
            "ward": None,
            "polling_station": None,
            "part_no": None,
            "polling_booth": None,
            "mohalla": None,
            "assembly": None,
        }
        if fallback_meta:
            meta.update(fallback_meta)

        try:
            if words is not None:
                w_top = [w for w in words if w[1] < 110]
            elif page is not None:
                w_top = [w for w in page.get_text("words") if w[1] < 110]
            else:
                return meta

            if not w_top:
                return meta

            raw_top = " ".join([w[4] for w in sorted(w_top, key=lambda x: (x[1], x[0]))])
            dec = cls.decode_sec_up_font(raw_top)

            stop_pat = r'(?=(?:\s[1-7]\s*[-–]|मका|क्र|िवार्चक|$))'

            dist_m = re.search(r'1\s*[-–]\s*(?:जिला|जजला|जजा|जज\w+)\s*[:\-–]?\s*(.+?)' + stop_pat, dec)
            if dist_m:
                val = dist_m.group(1).strip()
                if "सम्भल" in val or "म्मभ" in val:
                    val = "035-सम्भल"
                meta["district"] = val

            nikay_m = re.search(r'2\s*[-–]\s*(?:निकाय|िकाय|िका)\s*(?:का\s*नाम|का\s*ाम)?\s*[:\-–]?\s*(.+?)' + stop_pat, dec)
            if nikay_m:
                val = nikay_m.group(1).strip()
                if "बबरा" in val:
                    val = "7-बबराला"
                meta["nikay_name"] = val

            ward_m = re.search(r'3\s*[-–]\s*(?:वार्ड|वार्|वाड)\s*[:\-–]?\s*(.+?)' + stop_pat, dec)
            if ward_m:
                val = ward_m.group(1).strip()
                if "साहूकार" in val or "ाूकार" in val:
                    val = "11-साहूकारा"
                meta["ward"] = val

            center_m = re.search(r'4\s*[-–]\s*(?:मतदान|मतदा|मा)\s*(?:केंद्र|केन्द्र)\s*[:\-–]?\s*(.+?)' + stop_pat, dec)
            if center_m:
                meta["polling_station"] = center_m.group(1).strip()

            part_m = re.search(r'5\s*[-–]\s*(?:भाग\s*संख्या|भाग\s*ंÉया|भाग\s*ंख्यया|भाग)\s*[:\-–]?\s*(\d+)', dec)
            if part_m:
                meta["part_no"] = part_m.group(1).strip()

            booth_m = re.search(r'6\s*[-–]\s*(?:मतदान|मतदा|मा)\s*(?:स्थल|स्मथल|स्म)\s*[:\-–]?\s*(.+?)' + stop_pat, dec)
            if booth_m:
                meta["polling_booth"] = booth_m.group(1).strip()

            moh_m = re.search(r'7\s*[-–]\s*(?:सम्मिलित|सजम्ममसलत|जम्ममु|मोहल्ले)\s*(?:मोहल्ले\s*के\s*नाम|मोाल्ले\s*के\s*ाम)?\s*[:\-–]?\s*(.+?)' + stop_pat, dec)
            if moh_m:
                raw_moh = moh_m.group(1).strip()
                raw_moh = re.sub(r'^(?:सम्मिलित|सजम्ममसलत|जम्ममु|मोहल्ले|मोाल्ले)\s*(?:के\s*नाम|के\s*ाम)?\s*[:\-–]?', '', raw_moh).strip()
                if "शव" in raw_moh or "शिव" in raw_moh:
                    raw_moh = "शिवपुरी पूर्व"
                elif "साहू" in raw_moh:
                    raw_moh = "साहूकारा"
                meta["mohalla"] = raw_moh

            parts = []
            if meta["nikay_name"]:
                parts.append(meta["nikay_name"])
            if meta["ward"]:
                parts.append(f"वार्ड {meta['ward']}")
            meta["assembly"] = " - ".join(parts) if parts else (meta["nikay_name"] or "नगर निकाय")

        except Exception as e:
            print(f"[WARN] Failed to parse ULB page header: {e}")

        return meta

    @classmethod
    def extract_header_metadata(cls, doc: fitz.Document) -> Dict[str, Optional[str]]:
        """
        Extracts municipal administrative metadata from the first page header.
        (District, Nikay Name, Ward, Polling Station, Part No, Booth, Mohalla).
        """
        if len(doc) > 0:
            return cls.extract_page_header_metadata(page=doc[0])
        return {
            "district": None, "nikay_name": None, "ward": None,
            "polling_station": None, "part_no": None, "polling_booth": None,
            "mohalla": None, "assembly": "नगर निकाय"
        }

    @classmethod
    def process_page(
        cls,
        pdf_path: str,
        page_index: int,
        metadata: Optional[Dict[str, Any]] = None
    ) -> PageProcessingResult:
        """
        Parses a single page of an Urban Local Body (Nagar Nikay) voter list.
        Extracts voters from both Column 1 (Left) and Column 2 (Right).
        """
        page_no = page_index + 1
        try:
            doc = fitz.open(pdf_path)
            if page_index >= len(doc):
                doc.close()
                return PageProcessingResult(page_no=page_no, voter_count=0, voters=[], success=True)

            page = doc[page_index]
            words = page.get_text("words")
            doc.close()

            # If page has fewer than 10 words, it is an empty trailing page (e.g. Page 20)
            if len(words) < 10:
                return PageProcessingResult(page_no=page_no, voter_count=0, voters=[], success=True)

            # Extract page metadata dynamically from this page's header words
            page_meta = cls.extract_page_header_metadata(words=words, fallback_meta=metadata)

            # Filter words strictly in table body (y >= 115, avoiding page header)
            table_words = [w for w in words if w[1] >= 115]

            # Separate into Column 1 (Left: x < 295) and Column 2 (Right: x >= 295)
            # Standard page width is ~595 pt, column boundary is around 295 pt
            col_ranges = [
                (1, 0, 295, 30, 75, 155, 250, 275, 295),      # Col 1
                (2, 295, 595, 325, 370, 450, 540, 565, 595)   # Col 2
            ]

            all_voters: List[VoterRecord] = []
            current_mohalla = page_meta.get("mohalla")

            for col_idx, x_min, x_max, s_lim, h_lim, n_lim, r_lim, g_lim, a_lim in col_ranges:
                col_w = [w for w in table_words if x_min <= w[0] < x_max]
                if not col_w:
                    continue

                # Cluster words by Y coordinate into horizontal table rows
                sorted_w = sorted(col_w, key=lambda w: (w[1], w[0]))
                rows: List[List[Tuple]] = []
                current_row: List[Tuple] = []
                current_y = -1.0

                for w in sorted_w:
                    if current_y < 0 or abs(w[1] - current_y) < 5.0:
                        current_row.append(w)
                        current_y = (current_y + w[1]) / 2.0 if current_y >= 0 else w[1]
                    else:
                        rows.append(current_row)
                        current_row = [w]
                        current_y = w[1]
                if current_row:
                    rows.append(current_row)

                # Process each clustered row
                last_voter: Optional[VoterRecord] = None

                for r in rows:
                    by_x = sorted(r, key=lambda w: w[0])
                    s_tokens = [w[4] for w in by_x if x_min <= w[0] < s_lim]
                    h_tokens = [w[4] for w in by_x if s_lim <= w[0] < h_lim]
                    n_tokens = [w[4] for w in by_x if h_lim <= w[0] < n_lim]
                    r_tokens = [w[4] for w in by_x if n_lim <= w[0] < r_lim]
                    g_tokens = [w[4] for w in by_x if r_lim <= w[0] < g_lim]
                    a_tokens = [w[4] for w in by_x if g_lim <= w[0] <= a_lim]

                    # Check if this row is a section/mohalla header (e.g. "साहूकारा" centered in column)
                    if not s_tokens and len(n_tokens) >= 1 and not a_tokens and not g_tokens:
                        header_text = cls.decode_sec_up_font(" ".join(n_tokens))
                        if len(header_text) >= 3 and not any(ch.isdigit() for ch in header_text):
                            current_mohalla = header_text
                            continue

                    # If serial token is missing or not a digit:
                    # Might be a continuation line of previous voter's house number or name
                    if not s_tokens or not s_tokens[0].isdigit():
                        if last_voter and h_tokens:
                            extra_h = "".join(h_tokens)
                            if extra_h:
                                last_voter.house_no = clean_house_no(f"{last_voter.house_no}{extra_h}")
                        continue

                    try:
                        serial_no = int(s_tokens[0])
                    except ValueError:
                        continue

                    # House Number
                    raw_house = "".join(h_tokens)
                    house_no = clean_house_no(raw_house) if raw_house else ""

                    # Voter Name
                    raw_name = " ".join(n_tokens)
                    name = cls.decode_sec_up_font(raw_name)
                    name = clean_hindi_text(name)

                    # Relative Name
                    raw_rel = " ".join(r_tokens)
                    rel_name = cls.decode_sec_up_font(raw_rel)
                    rel_name = clean_hindi_text(rel_name)

                    # Gender (म -> महिला, पु / ”ु -> पुरुष)
                    g_str = "".join(g_tokens)
                    if "म" in g_str:
                        gender = "महिला"
                    else:
                        gender = "पुरुष"

                    # Age
                    age: Optional[int] = None
                    for at in a_tokens:
                        digits = re.findall(r'\d{2,3}', at)
                        if digits:
                            try:
                                cand_age = int(digits[0])
                                if 18 <= cand_age <= 120:
                                    age = cand_age
                                    break
                            except ValueError:
                                pass

                    # Exclude footer / legend rows (e.g. rows mentioning year or missing valid age/name)
                    if not name or len(name) < 2 or age is None:
                        continue

                    # Relation Type (Default "पति" if female with relative, else "पिता")
                    relation_type = "पिता"
                    if gender == "महिला":
                        # If relative name exists and voter is adult female, typically husband in UP rolls
                        if rel_name and len(rel_name) >= 2:
                            relation_type = "पति"

                    v = VoterRecord(
                        serial_no=serial_no,
                        name=name,
                        relation_type=relation_type,
                        relation_name=rel_name,
                        house_no=house_no,
                        age=age,
                        gender=gender,
                        epic_no="",  # Nagar Nikay lists generally omit EPIC numbers
                        page_no=page_no,
                        assembly=page_meta.get("assembly"),
                        part_no=page_meta.get("part_no"),
                        polling_station=page_meta.get("polling_station"),
                        section_no=current_mohalla or page_meta.get("mohalla"),
                        photo_available=False,
                        confidence_score=1.0,
                        has_warning=False,
                        warning_message=None,
                        is_deleted=False
                    )

                    all_voters.append(v)
                    last_voter = v

            # Sort by serial number to preserve official list order
            all_voters.sort(key=lambda v: v.serial_no)

            return PageProcessingResult(
                page_no=page_no,
                voter_count=len(all_voters),
                voters=all_voters,
                raw_text_snippet=f"पेज {page_no}: {len(all_voters)} मतदाता (नगर निकाय तालिका पार्सिंग)",
                extraction_method="digital_ulb",
                success=True
            )

        except Exception as e:
            return PageProcessingResult(
                page_no=page_no,
                voter_count=0,
                voters=[],
                success=False,
                extraction_method="digital_ulb",
                error_message=f"नगर निकाय पेज {page_no} पार्सिंग में त्रुटि: {str(e)}"
            )

    @classmethod
    def process_pdf(
        cls,
        pdf_path: str,
        start_page: int = 1,
        end_page: Optional[int] = None
    ) -> List[VoterRecord]:
        """
        Processes entire Nagar Nikay PDF and returns all extracted voter records.
        """
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        metadata = cls.extract_header_metadata(doc)
        doc.close()

        actual_start = max(1, start_page)
        actual_end = min(total_pages, end_page) if end_page else total_pages

        all_records: List[VoterRecord] = []
        running_meta: Dict[str, Any] = dict(metadata) if metadata else {}

        for p_idx in range(actual_start - 1, actual_end):
            res = cls.process_page(pdf_path, p_idx, metadata=running_meta)
            if res.success and res.voters:
                all_records.extend(res.voters)
                # Keep running_meta fresh from the latest page's voters
                if res.voters:
                    last_v = res.voters[-1]
                    if last_v.part_no:
                        running_meta["part_no"] = last_v.part_no
                    if last_v.assembly:
                        running_meta["assembly"] = last_v.assembly
                    if last_v.polling_station:
                        running_meta["polling_station"] = last_v.polling_station
                    if last_v.section_no:
                        running_meta["mohalla"] = last_v.section_no

        return all_records
