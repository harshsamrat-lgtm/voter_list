"""
Pattern and Heuristic Field Parser for Electoral Rolls (मतदाता सूची).
Extracts: Serial No, Name, Relation Type, Relative Name, House No, Age, Gender, EPIC No.
"""

import re
from typing import Optional, List, Dict, Any
from ..models.voter import VoterRecord
from .validator import clean_hindi_text, normalize_gender, normalize_relation_type, clean_epic_no, validate_voter_record, clean_house_no, normalize_digits, DEVANAGARI_DIGITS, is_genuine_voter
from .community_detector import identify_voter_community


class UPFieldParser:
    """Parser specifically tuned for UP Voter List block patterns."""

    @staticmethod
    def extract_header_metadata(text: str) -> Dict[str, Optional[str]]:
        """
        Extracts Assembly constituency, Part No, Section from page header.
        Tolerant to multiple colons, hyphens, and Devanagari numerals.
        Uses negative lookbehind (?<!अनु) so 'अनुभाग' is never matched as 'भाग'.
        """
        meta = {
            "assembly": None,
            "part_no": None,
            "section_no": None
        }
        if not text:
            return meta
        
        # 1. Part number pattern (e.g. 'भाग संख्या : : 235', 'भाग संख्या : 125', 'भाग सं० 235', 'Part No. 125')
        part_match = re.search(
            r'(?<!अनु)(?:भाग\s*(?:संख्या|सं(?:०|\.)?|नं(?:०|\.)?)?|Part\s*No\.?)\s*[:\-–—\.\s]+([0-9\u0966-\u096F]+)',
            text,
            re.IGNORECASE
        )
        if part_match:
            meta["part_no"] = normalize_digits(part_match.group(1).strip())
            
        # 2. Section pattern (e.g. 'अनुभाग संख्या और नाम : 4-वार्ड 40', 'अनुभाग संख्या व नाम: 1-मोहल्ला चौक')
        section_match = re.search(
            r'(?:अनुभाग\s*(?:संख्या|सं(?:०|\.)?|नं(?:०|\.)?)?(?:\s*(?:व|और)\s*नाम)?|Section\s*No\.?)\s*[:\-–—\.\s]+([^\n\r]+)',
            text,
            re.IGNORECASE
        )
        if section_match:
            cand = section_match.group(1).strip()
            # Clean off any trailing voter card noise or part number if header crop clipped adjacent fields
            cand = re.sub(r'\s*(?:YOM|UP/|[0-9]{2}/|CGV|TZE|नाम\s*[:\-–]).*$', '', cand, flags=re.IGNORECASE).strip()
            cand = re.sub(r'\s*(?<!अनु)भाग\s*(?:संख्या|सं).*$', '', cand, flags=re.IGNORECASE).strip()
            if cand:
                meta["section_no"] = clean_hindi_text(cand)
            
        # 3. Assembly pattern (e.g. 'विधान सभा निर्वाचन क्षेत्र: : 111-गुन्नौर', 'विधान सभा : 174 - लखनऊ मध्य')
        assembly_match = re.search(
            r'(?:विधान\s*सभा(?:\s*निर्वाचन\s*क्षेत्र)?(?:\s*(?:की\s*)?संख्या\s*(?:व|और)\s*नाम)?|Assembly\s*Constituency)\s*[:\-–—\.\s]+(.*?)(?=\s*(?<!अनु)भाग\s*(?:संख्या|सं)|(?<!अनु)अनुभाग|$)',
            text,
            re.IGNORECASE
        )
        if assembly_match:
            raw_assm = assembly_match.group(1).strip()
            clean_assm = re.sub(r'^[,\s\-–:]+|[,\s\-–:]+$', '', raw_assm).strip()
            if clean_assm:
                meta["assembly"] = clean_hindi_text(clean_assm)
            
        return meta

    @staticmethod
    def extract_cover_metadata(text: str) -> Dict[str, Optional[str]]:
        """
        Extracts Assembly Constituency, Part Number, and Polling Station (मतदान स्थल)
        from Page 1 (Cover page) text with high precision and noise elimination.
        """
        meta = {
            "assembly": None,
            "part_no": None,
            "polling_station": None,
            "official_initial_serial": None,
            "official_final_serial": None,
            "official_total_voters": None
        }
        if not text:
            return meta

        # 1. Part Number:
        # e.g. 'भाग संख्या : : 235', 'भाग संख्या : 125', 'भाग सं० 219'
        part_match = re.search(
            r'(?<!अनु)(?:भाग\s*(?:संख्या|सं(?:०|\.)?|नं(?:०|\.)?)?|Part\s*No\.?)\s*[:\-–—\.\s]+([0-9\u0966-\u096F]+)',
            text,
            re.IGNORECASE
        )
        if part_match:
            meta["part_no"] = normalize_digits(part_match.group(1).strip())

        # Fallback: Booth Number in Section 3 (e.g. '234 - बाबूराम सिंह...')
        if not meta["part_no"]:
            booth_m = re.search(r'(?:मतदान\s*स्थल\s*(?:की\s*संख्या\s*और\s*नाम|का\s*नाम))\s*[:\-–—\.\s]+([0-9\u0966-\u096F]+)\s*[\-–]', text)
            if booth_m:
                meta["part_no"] = normalize_digits(booth_m.group(1).strip())

        # 2. Assembly Constituency:
        # e.g. 'विधानसभा निर्वाचन क्षेत्र की संख्या व नाम और आरक्षण स्थिति : - गुन्नौर (सामान्य)'
        assembly_match = re.search(
            r'(?:विधान\s*सभा\s*निर्वाचन\s*क्षेत्र[^\n\r:]*|विधान\s*सभा|Assembly\s*Constituency)\s*[:\-–—\.\s]+([^\n\r]+?)(?=\s*(?<!अनु)भाग\s*संख्या|\s*संसदीय|$)',
            text,
            re.IGNORECASE
        )
        if assembly_match:
            raw_assembly = assembly_match.group(1).strip()
            # Clean leading 'और आरक्षण स्थिति :' prefix noise
            raw_assembly = re.sub(r'^(?:और|व)?\s*आरक्षण\s*स्थिति\s*[:\-–\s]+', '', raw_assembly, flags=re.IGNORECASE).strip()
            clean_assm = re.sub(r'^[,\s\-–:]+|[,\s\-–:]+$', '', raw_assembly).strip()
            if clean_assm:
                meta["assembly"] = clean_hindi_text(clean_assm)

        # 3. Polling Station (मतदान स्थल):
        # Extract block from '3. मतदान स्थल का विवरण' up to section 4 or signature
        sec3_match = re.search(r'3\.\s*मतदान\s*स्थल\s*का\s*विवरण[\s\S]+?(?=(?:4\.\s*निर्वाचक|निर्वाचक\s*रजिस्ट्रीकरण|कुल\s*पृष्ठ|$))', text)
        block = sec3_match.group(0) if sec3_match else text

        # Polling station booth number & name
        ps_name = ''
        name_match = re.search(r'मतदान\s*स्थल\s*(?:की\s*संख्या\s*और\s*नाम|का\s*नाम)\s*[:\-–—\.\s]+([\s\S]+?)(?=(?:मतदान\s*स्थल\s*के\s*प्रकार|इस\s*भाग\s*में|मतदान\s*स्थल\s*का\s*पता|$))', block)
        if name_match:
            cand = name_match.group(1).strip()
            cand = re.sub(r'इस\s*भाग\s*में.*$', '', cand, flags=re.IGNORECASE)
            cand = re.sub(r'मतदान\s*स्थल\s*के\s*प्रकार.*$', '', cand, flags=re.IGNORECASE)
            lines = [re.sub(r'^[,\s\-–:]+|[,\s\-–:]+$', '', l.strip()) for l in cand.splitlines() if l.strip()]
            lines = [l for l in lines if not any(kw in l for kw in ['सहायक', 'प्रकार', 'पुरुष', 'महिला', 'संख्या'])]
            ps_name = ' '.join(lines).strip()
            # Normalize booth number in ps_name (e.g. '२१9 - ...' -> '219 - ...')
            ps_name = re.sub(r'^([0-9\u0966-\u096F]+)', lambda m: normalize_digits(m.group(1)), ps_name)

        # Polling station address (room/building info)
        ps_addr = ''
        addr_match = re.search(r'मतदान\s*स्थल\s*का\s*पता\s*[:\-–—\.\s]+([\s\S]+?)(?=(?:4\.\s*निर्वाचक|निर्वाचक\s*रजिस्ट्रीकरण|कुल\s*पृष्ठ|$))', block)
        if addr_match:
            cand = addr_match.group(1).strip()
            lines = []
            for l in cand.splitlines():
                l_str = l.strip()
                if not l_str or any(kw in l_str for kw in ['निर्वाचक', 'मतदाता', 'सहायक', 'प्रकार', 'संख्या', 'अधिकारी', 'रजिस्ट्रीकरण', 'कुल', 'पृष्ठ', 'प्रारम्भिक', 'अंतिम', 'हस्ताक्षर', 'मोहर', 'सील']):
                    continue
                # Filter out lines with high garbage / low Hindi content
                dev_chars = len(re.findall(r'[\u0900-\u097F]', l_str))
                if dev_chars < 3:
                    continue
                # Remove Latin noise tokens like HOA, OAOI, Pam
                l_str = re.sub(r'\b[A-Za-z0-9]{1,5}\b', '', l_str).strip()
                l_str = re.sub(r'^[,\s\-–:]+|[,\s\-–:]+$', '', l_str)
                if len(l_str) >= 3:
                    lines.append(l_str)
            ps_addr = ', '.join(lines).strip()

        # Combine ps_name and ps_addr cleanly
        polling_station = ''
        if ps_name and ps_addr:
            if ps_addr in ps_name:
                polling_station = ps_name
            elif ps_name in ps_addr:
                polling_station = ps_addr
            else:
                room_m = re.search(r'(?:क[०0]?\s*न[०0]?|कक्ष|कमरा|भवन)\s*[\.:\-–]?\s*[0-9A-Za-z\u0966-\u096F]+', ps_addr)
                if room_m and room_m.group(0) not in ps_name:
                    polling_station = f"{ps_name}, {room_m.group(0)}"
                else:
                    polling_station = ps_name
        elif ps_name:
            polling_station = ps_name
        elif ps_addr:
            polling_station = ps_addr

        # Final cleanup of polling station
        if polling_station:
            polling_station = re.sub(r'\b(?:HOA|HOAOI|OAOI|Pam|YB|ye|om|em)\b', '', polling_station, flags=re.IGNORECASE)
            polling_station = re.sub(r'^[,\s\-–:]+|[,\s\-–:]+$', '', polling_station).strip()
            meta["polling_station"] = clean_hindi_text(polling_station)

        # 4. Official Initial Serial, Final Serial, and Total Voters:
        sec4_match = re.search(r'4\.\s*निर्वाचकों\s*की\s*संख्या[\s\S]+?(?=(?:निर्वाचक\s*रजिस्ट्रीकरण|कुल\s*पृष्ठ|$))', text)
        sec4_text = sec4_match.group(0) if sec4_match else text

        start_m = re.search(r'प्रारम्भिक\s*(?:क्रम\s*संख्या|संख्या)?\s*[:\-–—\.\s]+([0-9\u0966-\u096F]+)', sec4_text)
        if start_m:
            try:
                meta["official_initial_serial"] = int(normalize_digits(start_m.group(1)))
            except ValueError:
                pass

        end_m = re.search(r'अंतिम\s*(?:क्रम\s*संख्या|संख्या)?\s*[:\-–—\.\s]+([0-9\u0966-\u096F]+)', sec4_text)
        if end_m:
            try:
                meta["official_final_serial"] = int(normalize_digits(end_m.group(1)))
            except ValueError:
                pass

        total_m = re.search(r'(?:कुल\s*मतदाता|कुल\s*निर्वाचक|नेट\s*इलेक्टर)\s*[:\-–—\.\s]+([0-9\u0966-\u096F]+)', sec4_text)
        if total_m:
            try:
                meta["official_total_voters"] = int(normalize_digits(total_m.group(1)))
            except ValueError:
                pass

        # Fallback for tabular Section 4 layout
        if sec4_match and (not meta["official_final_serial"] or not meta["official_total_voters"]):
            nums = [int(normalize_digits(n)) for n in re.findall(r'\b([0-9\u0966-\u096F]{2,5})\b', sec4_match.group(0))]
            plausible = [n for n in nums if 50 <= n <= 3000]
            if plausible:
                detected_max = max(plausible)
                if not meta["official_final_serial"]:
                    meta["official_final_serial"] = detected_max
                if not meta["official_total_voters"]:
                    meta["official_total_voters"] = detected_max

        return meta

    @classmethod
    def parse_age_from_text(cls, text: str) -> Optional[int]:
        """
        Robustly extracts voter age (18-120) from card OCR text.
        Handles OCR artifacts where digit '1' is recognized as Devanagari danda '।' (\\u0964),
        double danda '॥' (\\u0965), brackets ']'/'[', pipe '|', slash '/', exclamation '!',
        or when English OCR outputs 'arg: 19 fer: Gea'.
        """
        if not text:
            return None

        # 1. Direct regex between age keyword and gender/boundary
        age_match = re.search(
            r'(?:आयु|उम्र|Age|arg|org)\s*[:\-–\.]?\s*([^a-zA-Z\n\r]+?)(?=(?:लिंग|Gender|महिला|पुरुष|स्त्री|अन्य|fer|fem|male|pur|mah|\n|$))',
            text,
            re.IGNORECASE
        )
        if not age_match:
            # Fallback 1: match age keyword followed by digit-like symbols
            age_match = re.search(
                r'(?:आयु|उम्र|Age|arg|org)\s*[:\-–\.]?\s*([0-9\u0964\u0965\|!/\]\[lI\(\)oO\.\s]+)',
                text,
                re.IGNORECASE
            )
        if not age_match:
            # Fallback 2: two-digit number directly followed by gender keywords (e.g. from English OCR: '19 fer:' or '31 fer:')
            age_match = re.search(r'\b([1-9][0-9])\s*(?:लिंग|Gender|fer|fem|male|pur|mah|fef)', text, re.IGNORECASE)

        if age_match:
            raw_age = age_match.group(1).strip()
            # Normalize Devanagari Danda \\u0964 and \\u0965 (generated by Tesseract for Latin '1')
            raw_age = raw_age.replace('\u0965', '11')
            raw_age = raw_age.replace('\u0964', '1')
            raw_age = re.sub(r'[\]\[\|!/lI\)\(\}]', '1', raw_age)
            raw_age = re.sub(r'[oO०]', '0', raw_age)

            digits = re.findall(r'\d+', raw_age)
            if digits:
                try:
                    val = int(digits[0])
                    # Handle double-danda artifacts like 411 -> 41
                    if val > 120 and str(val).endswith('11'):
                        cand = int(str(val)[:-1])
                        if 18 <= cand <= 120:
                            val = cand
                    if 18 <= val <= 120:
                        return val
                    elif val == 9:
                        # Dropped leading 1 in 19 (e.g. आयु : 9 -> 19)
                        return 19
                    elif 2 <= val <= 8:
                        # Dropped units digit 1 before space/लिंग (e.g. 2 -> 21, 3 -> 31, 4 -> 41, etc.)
                        return val * 10 + 1
                except ValueError:
                    return None

        # Fallback 3: check if any 2-digit number (18-99) appears on the line containing 'लिंग' or 'Gender'
        for line in text.split('\n'):
            if any(k in line.lower() for k in ['लिंग', 'gender', 'पुरुष', 'महिला', 'fer:']):
                num_matches = re.findall(r'\b([1-9][0-9])\b', line)
                for nm in num_matches:
                    v = int(nm)
                    if 18 <= v <= 100:
                        return v

        return None

    # Robust multi-lingual regex for DELETED / विलोपित voter stamps
    IS_DELETED_REGEX = re.compile(
        r'(?i)(?:'
        r'D\s*E\s*L\s*E\s*T\s*E?\s*[DO0]?|'   # Spaced D E L E T E D or DELETE
        r'\b(?:DELETED?|BELETED?|PELETED?|VILOPIT|CANCEL(?:LED)?)\b|'  # Exact full words
        r'DELETE[DO0]?|'                        # DELETED
        r'वि[ल][ेो][पि][त]|'                    # विलोपित, विलेपित
        r'वि\s*लो\s*पि\s*त|'                   # वि लो पि त
        r'निरस्त[०\.]?|'                        # निरस्त
        r'नि\s*र\s*स्त'                         # नि र स्त
        r')'
    )
    ENG_HOUSE_REGEX = re.compile(
        r'(?:(?:Fer|Her|Wer|War|Hepa|Fed|Wen|Aa|FM|FAM|eB|Ham|Sea|Sca|Heat|GAM|House|H\.?No|Aone|oA|ACA|chet|Heb|HhT|Fhe|Gee|Seq|GAT|Gea|Wp)\s*(?:[A-Za-z]{1,5}\s*)?)\s*[:\-–\.]\s*([0-9A-Za-z\u0900-\u097F\-/]+)',
        re.IGNORECASE
    )


    @classmethod
    def split_compound_voter_block(cls, block: str) -> List[str]:
        """
        Splits a compound voter block that contains a DELETED card fused with
        a subsequent voter card (because the DELETED stamp obscured the gender line).
        """
        del_m = cls.IS_DELETED_REGEX.search(block)
        if not del_m:
            return [block]
        
        # Look for start of a subsequent voter card anywhere after the deletion marker
        rest = block[del_m.end():]
        next_m = re.search(
            r'(?:\n|\r\n)\s*(?:(?:(?:\d{1,5}\s+)?(?:[A-Za-z]{2,4}\d{5,8}|UP/\d{1,2}/\d{1,3}/\d{3,8}))|मतदाता\s*का\s*नाम|नाम\s*[:\-–])',
            rest
        )
        if next_m:
            split_idx = del_m.end() + next_m.start()
            b1 = block[:split_idx].strip()
            b2 = block[split_idx:].strip()
            if len(b1) >= 5 and len(b2) >= 5:
                return [b1, b2]
        return [block]

    @classmethod
    def parse_single_voter_box(
        cls, 
        box_text: str, 
        default_serial: int = 1, 
        page_no: int = 1, 
        metadata: Optional[Dict] = None,
        box_eng: Optional[str] = None
    ) -> Optional[VoterRecord]:
        """
        Parses the text of an individual voter card box into a structured VoterRecord.
        Supports cross-lingual Hindi + English extraction for house numbers, age, serial, and EPIC.
        """
        if not box_text or len(box_text.strip()) < 3:
            return None
            
        lines = [line.strip() for line in box_text.splitlines() if line.strip()]
        joined_text = " \n ".join(lines)
        
        # 0. Check Deletion status (DELETED / DELETE / विलोपित / निरस्त stamp or text)
        is_deleted = False
        deleted_reason = None
        if cls.IS_DELETED_REGEX.search(box_text):
            is_deleted = True
            deleted_reason = "विलोपित / DELETED"

        if len(box_text.strip()) < 10 and not is_deleted:
            return None

        # 1. Extract Serial Number
        serial_no = default_serial
        # Serial is often at the very beginning of the block or before EPIC
        serial_match = re.search(r'^\s*(\d{1,4})\b', joined_text)
        if serial_match:
            try:
                serial_no = int(serial_match.group(1))
            except ValueError:
                serial_no = default_serial
                
        # 2. Extract EPIC Number (e.g., YOM275584, YOM33492, CGV20427, UP/9/042/032370)
        epic_no = clean_epic_no(joined_text)
        
        # 3. Extract Voter Name
        # Must only match lines starting with नाम or मतदाता का नाम (never matches पति का नाम or पिता का नाम)
        name = ""
        name_match = re.search(
            r'(?:^|\n)\s*(?:मतदाता\s*का\s*नाम|नाम|Name)\s*[:\-–]?\s*([\s\S]+?)(?=(?:\n\s*(?:पिता|पति|माता|अन्य|अभिभावक|संरक्षक|मकान|आयु|उम्र|लिंग|House|Age|Gender)|$))',
            box_text,
            re.IGNORECASE
        )
        if name_match:
            raw_val = name_match.group(1).strip()
            # Clean internal newlines into single spaces so full names across lines are preserved
            name = clean_hindi_text(raw_val)
            
        # Fallback if 'नाम :' prefix was misread (e.g. 'BOF :', 'BY :', 'नाव :', 'ताम :', 'डक." :', 'झट. :')
        if not name or len(name) < 2:
            rel_idx = -1
            for li, l in enumerate(lines):
                if re.search(r'(?:पिता|फिता|पति|पत्ति|माता|अन्य|अभिभावक|संरक्षक)\s*(?:का\s*)?(?:नाम|नाव|नाभ)?', l, re.IGNORECASE) or l.startswith('अन्य:'):
                    rel_idx = li
                    break
            if rel_idx > 0:
                cand = lines[rel_idx - 1]
                # Remove any label prefix before colon (English or Devanagari noise like 'BOF :', 'डक." :', 'झट. :')
                cand = re.sub(r'^[^:\n\r]{1,12}[:\-–]\s*', '', cand)
                # Keep only valid Hindi/text characters
                cand = re.sub(r'^[^\u0900-\u097F]+', '', cand).strip()
                if len(cand) >= 2:
                    name = clean_hindi_text(cand)

        # Strip stray Devanagari/symbol header prefixes from name (e.g. 'डक." : रूपवती' -> 'रूपवती', 'झट : संजीव' -> 'संजीव')
        if name:
            name = re.sub(r'^(?:लड़|कक|डक|झट|लीड|क्र[०\.]?|सं[०\.]?|४०|०|[A-Za-z0-9])[^:\n\r]{0,8}[:\-–]\s*', '', name).strip()
            name = cls.IS_DELETED_REGEX.sub('', name).strip()
            name = re.sub(r'^[,\s\-–:]+|[,\s\-–:]+$', '', name).strip()

        if is_deleted and (not name or len(name) < 2):
            name = "विलोपित (DELETED)"
        
        # 4. Extract Relation Type and Relative Name (across newlines to preserve full names)
        relation_type = "पिता"
        relation_name = ""
        
        # End lookahead for relative name: house number or age/gender
        rel_end_lookahead = r'(?=(?:\n\s*(?:[मभसम]\s*[\.०]?\s*सं[०\.]?|[मभसम]कान|House|H\.?\s*No|आयु|उम्र|Age|लिंग|Gender)|$))'
        
        # Check for Father's Name (tolerant to OCR misreads like पिता का नाव, पिता :, पिता का :, फिता का नाम, पैता)
        father_match = re.search(
            r'(?:^|\n)\s*(?:(?:पिता|फिता|पिला|प्ता|पिना|पैता)\s*(?:का\s*)?(?:नाम|नाव|नाभ|जाम|नास)?|Father(?:\'s)?\s*Name)\s*[:\-–]?\s*([\s\S]+?)' + rel_end_lookahead,
            box_text,
            re.IGNORECASE
        )
        # Check for Husband's Name (tolerant to पति का नाव, पति :, पति का :, पत्ति का नाम, पती का नाम)
        husband_match = re.search(
            r'(?:^|\n)\s*(?:(?:पति|पत्ति|पती)\s*(?:का\s*)?(?:नाम|नाव|नाभ|जाम|नास)?|Husband(?:\'s)?\s*Name)\s*[:\-–]?\s*([\s\S]+?)' + rel_end_lookahead,
            box_text,
            re.IGNORECASE
        )
        # Check for Mother's Name
        mother_match = re.search(
            r'(?:^|\n)\s*(?:(?:माता|मातृ)\s*(?:का\s*)?(?:नाम|नाव|नाभ|जाम)?|Mother(?:\'s)?\s*Name)\s*[:\-–]?\s*([\s\S]+?)' + rel_end_lookahead,
            box_text,
            re.IGNORECASE
        )
        # Check for Guardian / Other
        other_match = re.search(
            r'(?:^|\n)\s*(?:(?:अन्य|संरक्षक|अभिभावक|गार्जियन)\s*(?:का\s*)?(?:नाम|नाव)?|Guardian(?:\'s)?\s*Name)\s*[:\-–]?\s*([\s\S]+?)' + rel_end_lookahead,
            box_text,
            re.IGNORECASE
        )
        
        if husband_match and husband_match.group(1).strip():
            relation_type = "पति"
            relation_name = clean_hindi_text(husband_match.group(1))
        elif father_match and father_match.group(1).strip():
            relation_type = "पिता"
            relation_name = clean_hindi_text(father_match.group(1))
        elif mother_match and mother_match.group(1).strip():
            relation_type = "माता"
            relation_name = clean_hindi_text(mother_match.group(1))
        elif other_match and other_match.group(1).strip():
            relation_type = "अन्य"
            relation_name = clean_hindi_text(other_match.group(1))
            
        # Fallback for relative name if label was completely garbled by OCR
        if not relation_name or len(relation_name) < 2:
            for line in lines:
                if any(kw in line for kw in ['मकान', 'आयु', 'उम्र', 'लिंग', 'House', 'Age', 'Gender']):
                    continue
                if name and name in line:
                    continue
                if re.search(r'^\s*(?:\d{1,4}\b|[A-Za-z]{2,4}\d{4,8})', line):
                    continue
                clean_cand = re.sub(r'^[^:\n\r]{1,15}[:\-–]\s*', '', line).strip()
                clean_cand = re.sub(r'^[^\u0900-\u097F]+', '', clean_cand).strip()
                clean_cand = clean_hindi_text(clean_cand)
                if len(clean_cand) >= 2:
                    relation_name = clean_cand
                    if any(hk in line for hk in ['पति', 'पती', 'पत्ति', 'Husband']):
                        relation_type = "पति"
                    elif any(mk in line for mk in ['माता', 'Mother']):
                        relation_type = "माता"
                    elif any(ok in line for ok in ['अन्य', 'संरक्षक']):
                        relation_type = "अन्य"
                    else:
                        relation_type = "पिता"
                    break

        # Clean relative name of photo noise, brackets, and stray English letters
        if relation_name:
            relation_name = re.sub(r'\[?फोटो\s*उपलब्ध\s*है\]?|उपलब्ध\s*है|फोटो', '', relation_name)
            relation_name = re.sub(r'(?:मकान|House|H\.?\s*No).*$', '', relation_name, flags=re.IGNORECASE)
            # Remove isolated Latin characters (e.g. 'A', 'Hea,', 'wag') if Hindi text exists
            if re.search(r'[\u0900-\u097F]', relation_name):
                relation_name = re.sub(r'\b[A-Za-z0-9\.\,\:\;_\*~`\'"\?]+\b', '', relation_name)
            relation_name = clean_hindi_text(relation_name)
            if relation_name:
                relation_name = cls.IS_DELETED_REGEX.sub('', relation_name).strip()
                relation_name = re.sub(r'^[,\s\-–:]+|[,\s\-–:]+$', '', relation_name).strip()
            
        if is_deleted and (not relation_name or len(relation_name) < 2):
            relation_name = "—"
            
        # 5. Extract House Number (मकान संख्या / House No) with Cross-Lingual Hindi + English Intelligence
        h_hin = ""
        # Candidate A: from Hindi / combined text with line-start or word boundary
        # Note: Do NOT match bare 'मकार' without word boundary, which wrongly triggers on names ending in 'मकार' (e.g. 'ओमकार सिंह')
        house_match = re.search(
            r'(?:^|[\n\r\|\[\s])(?:मकान|भकान|मफान|मकात|कान|गृह|House|H\.?\s*No\.?)\s*(?:संख्या|सं(?:०|\.)?|नं(?:०|\.)?|नंबर|नम्बर|seer|sisa|No\.?)?\s*[:\-–\.]?\s*([^\n\r]+?)(?=(?:\n|आयु|उम्र|लिंग|Age|Gender|$))',
            joined_text,
            re.IGNORECASE
        )
        if house_match:
            h_hin = clean_house_no(house_match.group(1))
        
        # Candidate B: Positional line scan between Relative line and Age line
        # In Indian voter cards, the line between Relative and Age is ALWAYS the House Number line!
        if not h_hin:
            rel_idx = -1
            age_idx = -1
            for idx, line in enumerate(lines):
                if any(k in line for k in ['पिता', 'पति', 'माता', 'अन्य']) and rel_idx == -1:
                    rel_idx = idx
                if any(k in line for k in ['आयु', 'उम्र', 'Age', 'लिंग', 'Gender']) and age_idx == -1:
                    age_idx = idx

            if rel_idx != -1 and age_idx > rel_idx + 1:
                for mid in range(rel_idx + 1, age_idx):
                    line_val = re.sub(r'^(?:मकान|भकान|कान|संख्या|सं|नं)\s*[:\-–\.]\s*', '', lines[mid], flags=re.IGNORECASE)
                    cand_mid = clean_house_no(line_val)
                    if cand_mid:
                        h_hin = cand_mid
                        break

        # Candidate C: Extract from English OCR to fix 1 / 4 confusion, dropped 1s, and sub-houses
        h_eng = ""
        target_eng = box_eng if box_eng else box_text
        if target_eng:
            for line in target_eng.splitlines():
                l = line.strip()
                if not l:
                    continue
                m_kw = cls.ENG_HOUSE_REGEX.search(l)
                if m_kw:
                    c_kw = clean_house_no(m_kw.group(1))
                    if c_kw and any(ch.isdigit() for ch in c_kw):
                        h_eng = c_kw
                        break

            if not h_eng:
                eng_lines = [l.strip() for l in target_eng.splitlines() if l.strip()]
                age_eng_idx = -1
                for idx, el in enumerate(eng_lines):
                    if re.search(r'\b(?:org|ay|age|omg|og|ary|ar|Wy|My|fe|ye)\b', el, re.IGNORECASE) and any(c.isdigit() for c in el):
                        age_eng_idx = idx
                        break
                if age_eng_idx > 0:
                    prev_line = eng_lines[age_eng_idx - 1]
                    m_prev = re.search(r'[:\-–\.]\s*([0-9A-Za-z\u0900-\u097F\-/]+)', prev_line)
                    if m_prev:
                        cand_prev = clean_house_no(m_prev.group(1))
                        if cand_prev and any(ch.isdigit() for ch in cand_prev):
                            h_eng = cand_prev

        # Cross-Evaluate Hindi vs English House Number:
        # Hindi Tesseract frequently drops '1' (e.g. 114->4, 81->8, 102->2) or turns '1' into '4' (e.g. 17->47)
        # BUT Hindi is authoritative for Devanagari sub-house letters (अ, ब, क, ग, etc.)
        # English Tesseract universally misreads these:  ब→4/a/9/G,  अ→3/7/a,  ग→G/9,  क→q
        
        # Detect Devanagari sub-house characters in Hindi result
        hin_has_dev_subhouse = bool(re.search(r'[\u0905-\u0939]', h_hin))
        
        if not h_hin:
            house_no = h_eng
        elif not h_eng:
            house_no = h_hin
        elif h_hin == h_eng:
            house_no = h_hin
        elif hin_has_dev_subhouse:
            # Hindi OCR has Devanagari sub-house chars → it is the AUTHORITATIVE source
            # English OCR misreads these as digits/letters, so we trust Hindi structure
            # BUT the digit prefix in Hindi may still have dropped '1', so use English digits as guide
            hin_digits = re.match(r'^(\d+)', h_hin)
            eng_digits = re.match(r'^(\d+)', h_eng)
            hin_suffix = re.search(r'([\u0900-\u097F/\-\d]+)$', h_hin)
            
            if hin_digits and eng_digits:
                hd = hin_digits.group(1)
                ed = eng_digits.group(1)
                dev_part = h_hin[len(hd):]  # Devanagari suffix (e.g. 'अ', 'ब', 'क/1')
                
                # If English has '1' that Hindi dropped: e.g. Hindi='3ब' Eng='134' → digit prefix should be '13'
                # BUT be careful: Hindi '3ब' + Eng '34' means '3ब' (Eng '4' is misread of 'ब')
                # Rule: trust English prefix ONLY if it's strictly longer and starts with or ends with Hindi prefix
                if len(ed) > len(hd) and (ed.startswith(hd) or ed.endswith(hd)):
                    # English likely recovered a dropped '1' in the digit prefix
                    # Verify: remove the extra digit(s) from English and check they look like recovered 1s
                    if ed.startswith(hd):
                        extra = ed[len(hd):]
                    else:
                        extra = ed[:len(ed)-len(hd)]
                    # Only trust if the extra chars are '1' (recovered danda) and not a full new digit sequence
                    if extra in ('1', '11'):
                        house_no = ed + dev_part
                    else:
                        house_no = h_hin
                else:
                    house_no = h_hin
            else:
                house_no = h_hin
        elif re.search(r'[\u0900-\u097F]$', h_hin) and h_eng.endswith('/1'):
            # Devanagari sub-letter in Hindi + missing /1 recovered from English (e.g. '7बी' + '74/1' -> '7बी/1')
            house_no = f"{h_hin}/1"
        elif '1' in h_eng:
            # Handle English recovering digit 1 dropped or distorted into 4 by Hindi
            if h_hin in ('00', '0') and h_eng in ('1', '01'):
                house_no = "1"
            elif h_hin not in ('00', '0') and (('1' not in h_hin) or (len(h_eng) > len(h_hin)) or ('4' in h_hin and '4' not in h_eng)):
                house_no = h_eng
            else:
                house_no = h_hin
        elif '/' in h_eng and '/' not in h_hin and h_eng.startswith(h_hin):
            house_no = h_eng
        elif '/' in h_hin and '/' not in h_eng and h_hin.startswith(h_eng):
            house_no = h_hin
        elif h_hin in ('00', '0'):
            house_no = h_hin
        else:
            house_no = h_hin
            
        # 6. Extract Age (आयु / उम्र / Age)
        age = cls.parse_age_from_text(joined_text)
                
        # 7. Extract Gender (लिंग / Gender)
        # निर्वाचन नियम: यदि संबंधी 'पति' है, तो लिंग अनिवार्य रूप से 'महिला' होगा
        female_name_markers = {
            'देवी', 'कुमारी', 'बेगम', 'खातून', 'बानो', 'निशा', 'रानी', 'श्रीमती',
            'सुल्ताना', 'अख्तर', 'फातिमा', 'कनीज', 'जबीं', 'सायरा', 'नाज', 'परवीन',
            'आरती', 'सुमन', 'रेखा', 'सीमा', 'पूजा', 'मंजू', 'गीता', 'सरोज', 'अनीता',
            'सुनीता', 'कमलेश', 'उर्मिला', 'किरन', 'ममता', 'आशा', 'संगीता', 'पुष्पा',
            'सुशीला', 'मुन्नी', 'मीना', 'उमा', 'मंजूषा', 'रीता', 'बबली', 'रजनी'
        }
        name_words = set(re.findall(r'[\u0900-\u097F]+', name or ""))
        has_female_name = bool(name_words.intersection(female_name_markers))

        gender = "महिला" if (relation_type == "पति" or has_female_name) else "पुरुष"
        gender_match = re.search(
            r'(?:लिंग|Gender)\s*[:\-–]?\s*(महिला|पुरुष|स्त्री|अन्य|Female|Male|Other|Transgender)',
            joined_text,
            re.IGNORECASE
        )
        if gender_match:
            parsed_g = normalize_gender(gender_match.group(1))
            # 100% Electoral Rule: यदि संबंध 'पति' है, तो लिंग सदैव 'महिला' ही होगा
            if relation_type == "पति":
                gender = "महिला"
            elif parsed_g == "पुरुष" and has_female_name and relation_type in ["पति", "माता"]:
                gender = "महिला"
            else:
                gender = parsed_g
        else:
            # Fallback scan for keywords in box
            if any(kw in joined_text for kw in ['महिला', 'स्त्री', 'Female', 'female']):
                gender = "महिला"
            elif any(kw in joined_text for kw in ['अन्य', 'Other', 'transgender']):
                gender = "अन्य"
            elif relation_type == "पति" or has_female_name:
                gender = "महिला"
            else:
                gender = "पुरुष"
                
        # 8. Check Photo status
        photo_available = not ("फोटो उपलब्ध नहीं" in joined_text or "Photo Not Available" in joined_text)

        # 9. Identify Muslim community identity
        is_m, m_reason, _ = identify_voter_community(name, relation_name, relation_type)
        
        # Build VoterRecord
        record = VoterRecord(
            serial_no=serial_no,
            name=name,
            relation_type=relation_type,
            relation_name=relation_name,
            house_no=house_no,
            age=age,
            gender=gender,
            epic_no=epic_no,
            page_no=page_no,
            assembly=metadata.get("assembly") if metadata else None,
            part_no=metadata.get("part_no") if metadata else None,
            section_no=metadata.get("section_no") if metadata else None,
            photo_available=photo_available,
            is_muslim=is_m,
            muslim_reason=m_reason,
            is_deleted=is_deleted,
            deleted_reason=deleted_reason
        )
        
        # Normalize and validate
        record = validate_voter_record(record)
        if not is_genuine_voter(record):
            return None
        return record

    @classmethod
    def parse_full_page_text(
        cls, 
        page_text: str, 
        page_no: int = 1, 
        current_serial: int = 1,
        metadata: Optional[Dict] = None
    ) -> List[VoterRecord]:
        """
        Splits a full page/column of text into individual voter blocks and parses each one.
        Handles column strip OCR output where voter cards appear sequentially.
        Standard pages have exactly 10 cards per column (30 per page).
        Penultimate/last voter pages may have fewer cards.
        """
        if not page_text or len(page_text.strip()) < 20:
            return []
            
        header_meta = cls.extract_header_metadata(page_text)
        merged_meta = dict(metadata) if metadata else {}
        for k, v in header_meta.items():
            if v and not merged_meta.get(k):
                merged_meta[k] = v

        voters: List[VoterRecord] = []
        
        # Strategy A: Split on gender line which reliably marks the END of every voter card.
        # Pattern: "...लिंग : पुरुष/महिला" ends every single voter card in UP rolls.
        gender_pattern = r'((?:[^\n]*?(?:आयु|उम्र|Age|org)[^\n]*?)?(?:लिंग|Gender)\s*[:\-–]?\s*(?:पुरुष|महिला|अन्य|स्त्री)[^\n]*)'
        parts = re.split(gender_pattern, page_text, flags=re.IGNORECASE)
        
        # Reconstruct blocks: pair content with its gender delimiter
        blocks = []
        i = 0
        while i < len(parts):
            if i + 1 < len(parts) and re.search(r'(?:लिंग|Gender)', parts[i + 1], re.IGNORECASE):
                # Content + gender line = one complete voter block
                blocks.append(parts[i] + parts[i + 1])
                i += 2
            else:
                if parts[i].strip():
                    blocks.append(parts[i])
                i += 1
        
        # If gender splitting produced fewer than 3 blocks, try splitting on "नाम :" keyword
        if len(blocks) < 3:
            name_pattern = r'(?=(?:\n|^)\s*(?:\d{1,4}\s+)?(?:[A-Za-z]{2,4}\d{5,8}\s+)?(?:मतदाता\s*का\s*नाम|नाम\s*[:\-–]))'
            alt_blocks = re.split(name_pattern, page_text)
            if len(alt_blocks) > len(blocks):
                blocks = alt_blocks

        # Strategy B: Deletion boundary refinement.
        # When a card is marked DELETED/विलोपित, the stamp may obliterate the gender line.
        # This causes the deleted card to be fused with the subsequent voter card.
        # We split any compound block that contains a deletion stamp followed by a next voter card start.
        refined_blocks = []
        for b in blocks:
            sub_queue = [b]
            while sub_queue:
                curr = sub_queue.pop(0)
                splits = cls.split_compound_voter_block(curr)
                if len(splits) > 1:
                    sub_queue.extend(splits)
                else:
                    refined_blocks.append(curr)
        blocks = refined_blocks
        
        serial_tracker = current_serial
        for block in blocks:
            cleaned_block = block.strip()
            is_del_block = bool(cls.IS_DELETED_REGEX.search(cleaned_block))
            if len(cleaned_block) < 10 and not is_del_block:
                continue
            # If block has voter-relevant keywords or deletion markers
            if is_del_block or any(kw in cleaned_block for kw in ['नाम', 'Name', 'आयु', 'उम्र', 'लिंग', 'मकान', 'DELETED', 'DELETE', 'विलोपित', 'निरस्त']):
                voter = cls.parse_single_voter_box(
                    cleaned_block,
                    default_serial=serial_tracker,
                    page_no=page_no,
                    metadata=merged_meta
                )
                if voter and is_genuine_voter(voter):
                    voters.append(voter)
                    serial_tracker = voter.serial_no + 1
                    
        return voters
