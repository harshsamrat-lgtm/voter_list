"""
Data normalization, cleansing, and validation rules for UP Voter records.
Handles Hindi character normalization, OCR error correction, and integrity verification.
"""

import re
from typing import Tuple, Optional
from ..models.voter import VoterRecord


def clean_hindi_text(text: str) -> str:
    """Normalizes whitespace and removes stray special characters from Hindi text."""
    if not text:
        return ""
    # Remove unwanted punctuation at start/end but keep standard Hindi characters, numerals, and slashes
    text = re.sub(r'[\r\n\t]+', ' ', text)
    # Remove leading colons, hyphens, dots, bullet symbols
    text = re.sub(r'^[\s:\-–—\.\*\|]+', '', text)
    text = re.sub(r'[\s:\-–—\.\*\|]+$', '', text)
    # Collapse multiple spaces
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def normalize_gender(raw_gender: str) -> str:
    """Normalizes gender string to standardized 'पुरुष', 'महिला', or 'अन्य'."""
    raw = clean_hindi_text(raw_gender).lower()
    
    if any(k in raw for k in ['महिला', 'स्त्री', 'female', 'f', 'स्त्रीलिंग', 'औरत', 'म०']):
        return "महिला"
    elif any(k in raw for k in ['अन्य', 'तृतीय', 'transgender', 'other', 't', 'o']):
        return "अन्य"
    else:
        # Default or male match (पुरुष, purush, male, m, etc.)
        return "पुरुष"


def normalize_relation_type(raw_type: str) -> str:
    """Normalizes relation label to 'पिता', 'पति', 'माता', or 'अन्य'."""
    raw = clean_hindi_text(raw_type)
    
    if any(k in raw for k in ['पति', 'husband', 'Husband', 'हसबैंड']):
        return "पति"
    elif any(k in raw for k in ['माता', 'mother', 'Mother', 'माँ']):
        return "माता"
    elif any(k in raw for k in ['संरक्षक', 'अभिभावक', 'guardian', 'Guardian', 'अन्य']):
        return "अन्य"
    else:
        return "पिता"


def is_valid_epic_format(epic: Optional[str]) -> Tuple[bool, str, dict]:
    """
    Validates whether an EPIC number strictly conforms to official Election Commission formats.
    Supports both:
    1. Modern Standard ECI / UP Format: 3 Uppercase Letters + 7 Digits = 10 Chars (e.g. YOM2966612, CGV2042794).
    2. Legacy Slash Format: State/District/AC/Voter serial (e.g. UP/09/042/012345, UP/9/42/123456).
    
    Returns: (is_valid: bool, defect_reason: str, metadata: dict)
    """
    if not epic or not str(epic).strip():
        return False, "EPIC संख्या रिक्त / अनुपलब्ध है", {"type": "MISSING", "length": 0}
        
    clean_val = str(epic).strip().upper()
    
    # 1. Legacy Slash format (e.g. UP/09/042/012345, UP/9/42/123456)
    if '/' in clean_val:
        if re.match(r'^(?:UP|[A-Z]{2})/\d{1,3}/\d{1,4}/\d{3,8}$', clean_val):
            return True, "वैध (लीगेसी स्लैश प्रारूप)", {"type": "LEGACY_VALID", "length": len(clean_val)}
        else:
            return False, f"अमान्य स्लैश प्रारूप: '{epic}'", {"type": "INVALID_SLASH", "length": len(clean_val)}
            
    # 2. Modern Standard Alphanumeric format
    # Check for stray noise characters
    if re.search(r'[^A-Za-z0-9]', clean_val):
        return False, f"EPIC में अवांछित प्रतीक चिह्न हैं: '{epic}'", {"type": "NOISE_SYMBOLS", "length": len(clean_val)}
        
    total_len = len(clean_val)
    if total_len < 10:
        return False, f"EPIC में अंक/अक्षर कम हैं ({total_len}/10): '{epic}'", {"type": "TOO_SHORT", "length": total_len}
    elif total_len > 10:
        return False, f"EPIC में अंक/अक्षर अधिक हैं ({total_len}/10): '{epic}'", {"type": "TOO_LONG", "length": total_len}
        
    # Exactly 10 characters: First 3 letters, last 7 digits
    prefix = clean_val[:3]
    digits = clean_val[3:]
    
    if not prefix.isalpha():
        return False, f"EPIC के प्रथम 3 वर्ण अक्षर होने चाहिए: '{prefix}'", {"type": "INVALID_PREFIX", "length": 10}
        
    if not digits.isdigit():
        return False, f"EPIC के अंतिम 7 वर्ण अंक होने चाहिए: '{digits}'", {"type": "INVALID_DIGITS", "length": 10}
        
    return True, "वैध मानक प्रारूप (3 अक्षर + 7 अंक)", {"type": "STANDARD_VALID", "length": 10, "prefix": prefix, "digits": digits}


def clean_epic_no(raw_epic: str) -> str:
    """
    Cleans, normalizes, and validates EPIC (Voter ID) number format.
    Prioritizes exact 10-char ECI standards and official UP legacy slash formats.
    """
    if not raw_epic:
        return ""
        
    # Quick direct check on raw text preserving word boundaries
    eci_direct = re.search(r'(?i)\b([A-Za-z]{3})[\s\-_]*([0-9]{7})\b', raw_epic)
    if eci_direct:
        return eci_direct.group(1).upper() + eci_direct.group(2)
    slash_direct = re.search(r'(?i)\b((?:UP|[A-Za-z]{2})/\d{1,3}/\d{1,4}/\d{3,8})\b', raw_epic)
    if slash_direct:
        return slash_direct.group(1).upper()

    # Normalize special OCR artifacts: Yen symbol, apostrophe, backtick, quotes, spaces, tilde
    t = raw_epic.replace('¥', 'Y').replace("'", '').replace('`', '').replace(' ', '').replace('"', '').replace('~', '')
    
    # Normalize common Devanagari or symbol misreads of YOM and UP prefixes anywhere in text
    t = re.sub(r'[४4][0oO०]{2}[/I|]?', 'YOM', t)
    t = re.sub(r'[\|/][oO0][\|/]', 'YOM', t)
    t = re.sub(r'[७7][7vV][४4]/', 'UP/', t)
    
    # 1. State slash format e.g. UP/09/042/012345 or UP/9/04/0324386
    # Also fix common OCR misreads where slashes became | or \ or :
    t_slash = re.sub(r'(?:UP|[A-Za-z]{2})[\|\\:](\d{1,3})[\|\\:](\d{1,4})[\|\\:](\d{3,8})', r'UP/\1/\2/\3', t)
    slash_match = re.search(r'((?:UP|[A-Za-z]{2})/\d{1,3}/\d{1,4}/\d{3,8})', t_slash)
    if slash_match:
        return slash_match.group(1).upper()
        
    # Fuzzy slash format e.g. 1/9/04/0324386
    fuzzy_slash = re.search(r'([A-Za-z0-9]{1,4}/\d{1,4}/\d{1,4}/\d{3,8})', t)
    if fuzzy_slash:
        return re.sub(r'^[^\/]+/', 'UP/', fuzzy_slash.group(1)).upper()

    # 2. Priority: Exact Standard ECI Format: 3 Letters + 7 Digits (e.g. YOM2966612, CGV2042794)
    exact_match = re.search(r'\b([A-Za-z]{3})[\s\'\.\-_]*([0-9]{7})(?:[^\d]|$)', t)
    if exact_match:
        return exact_match.group(1).upper() + exact_match.group(2)

    # 3. Standard alphanumeric format with OCR character correction
    # Strip trailing border pipe or noise so it doesn't get converted into digit '1'
    t_stripped = re.sub(r'[\|\\/]+$', '', t)
    std_match = re.search(r'([A-Za-z]{2,4})[\s\'\.\-_]*([0-9OIoIl\|]{5,9})', t_stripped)
    if std_match:
        prefix = std_match.group(1).upper()
        digits_raw = std_match.group(2)
        
        # Strip trailing noise pipes from digits_raw
        digits_raw = re.sub(r'[\|/\\_\-]+$', '', digits_raw)
        
        # 1. Strip leading noise character before 3-letter prefix (e.g. FYOM -> YOM, ICGV -> CGV, IYN -> YN)
        if len(prefix) == 4 and prefix[1:] in ('YOM', 'CGV', 'TZE', 'UHQ', 'XUA', 'SJE', 'DZX', 'WPB', 'LWE', 'BRG', 'KLG', 'TND', 'JKT'):
            prefix = prefix[1:]
        elif len(prefix) == 4 and prefix[0] in ('I', 'L', 'F', 'S', 'T', '1', '|'):
            prefix = prefix[1:]
        # 2. If 4 letters and 6 digits (total 10 chars), 4th letter is almost certainly a misread digit (e.g. JKTO104406 -> JKT0104406)
        elif len(prefix) == 4 and len(digits_raw) == 6:
            fourth = prefix[3]
            d_map = {'O': '0', 'I': '1', 'L': '1', 'Z': '2', 'B': '8', 'S': '5', 'G': '6'}
            if fourth in d_map:
                digits_raw = d_map[fourth] + digits_raw
                prefix = prefix[:3]
        elif len(prefix) == 4 and prefix[:3] in ('YOM', 'CGV', 'TZE', 'UHQ', 'XUA', 'SJE', 'DZX', 'WPB', 'LWE', 'BRG', 'KLG', 'TND', 'JKT'):
            fourth = prefix[3]
            d_map = {'O': '0', 'I': '1', 'L': '1', 'Z': '2', 'B': '8', 'S': '5', 'G': '6'}
            if fourth in d_map:
                digits_raw = d_map[fourth] + digits_raw
            prefix = prefix[:3]
        elif prefix in ('TOM', 'VOM', 'WOM', 'UOM', 'HOM', 'IOM', 'EOM', 'Y0M', 'IYN', 'IYO'):
            prefix = 'YOM'
            
        digits = digits_raw.replace('O', '0').replace('o', '0').replace('I', '1').replace('l', '1').replace('|', '1')
        
        # 3. Standardize 2-letter prefix misreads when followed by 7 digits
        if len(prefix) == 2 and len(digits) == 7:
            pref_map = {'YN': 'YOM', 'YO': 'YOM', 'OM': 'YOM', 'GV': 'CGV', 'VV': 'CGV', 'CV': 'CGV'}
            if prefix in pref_map:
                prefix = pref_map[prefix]
                
        # If 8 digits and ends with 1, check if trailing pipe was misread as 1 e.g. YOM27603381 from 'YOM2760338 |'
        if len(digits) == 8 and len(prefix) == 3:
            if digits_raw.endswith('|') or digits_raw.endswith('I') or digits_raw.endswith('l'):
                digits = digits[:7]
                
        return prefix + digits
        
    return ""


DEVANAGARI_DIGITS = str.maketrans('०१२३४५६७८९', '0123456789')


def normalize_digits(text: str) -> str:
    """Normalizes Devanagari numerals ०-९ to ASCII 0-9."""
    if not text:
        return ""
    return text.translate(DEVANAGARI_DIGITS)


def clean_house_no(raw_house: str) -> str:
    """
    Robustly cleans, normalizes, and strips photo watermark artifacts and address leakages from house numbers.
    Preserves real house numbers, sub-house characters (e.g. 12-A, 14/2, 15-क, 204/2, 53अ1), '0', '00', and '--'.
    Normalizes Devanagari dandas (॥ -> 11, । -> 1), leading zeros (04 -> 4), and spacing between digits and letters.
    Rejects relative name leaks (S/O, W/O), surname leaks (सिंह, यादव), and standalone colony names.
    """
    if not raw_house:
        return ""
    val = raw_house.strip()
    
    # Strip zero-width characters and BOM
    val = re.sub(r'[\u200c\u200d\uFEFF]', '', val)
    
    # Normalize Devanagari digits (०-९ -> 0-9)
    val = normalize_digits(val)
    
    # 1. Reject kinship / relative name leaks immediately:
    # E.g. 'एस/ओ अशोक कुमार', 'डबलयू/ओ दीपक यादव', 'डब्ल्यू/ओ', 'डी/ओ', 'C/O', 'S/O', 'W/O', 'D/O'
    if re.search(r'(?i)\b(?:एस\s*/\s*ओ|डबलयू\s*/\s*ओ|डब्ल्यू\s*/\s*ओ|डी\s*/\s*ओ|s\s*/\s*o|w\s*/\s*o|d\s*/\s*o|c\s*/\s*o)\b', val):
        return ""
        
    # 2. Strip age/gender keywords if they leaked onto this line
    val = re.sub(r'(?:आयु|उम्र|Age|arg|org|लिंग|Gender).*$', '', val, flags=re.IGNORECASE).strip()
    
    # 3. Strip trailing column border / purna-viram danda / pipe when preceded by space
    # Prevents '00 ।' -> '00 11', '9 ।' -> '9 11', '20/82 ।' -> '20/82 11'
    val = re.sub(r'\s+[।॥|!\]\)\-–:]+\s*$', '', val).strip()
    
    # 4. Standalone danda / pipe / bracket is digit '1'
    if val in ('।', '|', '!', ']', 'I', 'l', '॥', '||', '!!', ']]'):
        return '1'

    # 5. Normalize Devanagari danda / double danda and brackets attached to digits (॥4 -> 14, 0॥ -> 01, ]4 -> 14, 8। -> 81)
    val = re.sub(r'0[॥\|!\]\)]+', '01', val)
    val = val.replace('\u0965', '1')   # ॥ -> 1 (double danda in electoral rolls is almost always digit 1)
    val = val.replace('\u0964', '1')   # । -> 1
    # Fix bracket/pipe BEFORE Devanagari sub-house letter: ']अ' -> '1अ', ']ब' -> '1ब', '|क' -> '1क'
    val = re.sub(r'^[\]\|!Ili\(\)\[\}]+(?=[\u0905-\u0939])', '1', val)
    val = re.sub(r'(?<=\d)[\]\|!Ili\(\)\[\}]+(?=[\u0905-\u0939])', '1', val)
    val = re.sub(r'^[\]\|!Ili\(\)\[\}]+(?=\d)', '1', val)
    val = re.sub(r'(?<=\d)[\]\|!Ili\(\)\[\}]+(?=\d)', '/', val)
    val = re.sub(r'(?<=\d)[\]\|!Ili\(\)\[\}]+$', '1', val)
    
    # 4. Remove known photo watermark phrases and OCR misreads
    val = re.sub(r'\[?\s*(?:फोटो\s*)?(?:उपलब्ध|उपलबध|उपलवध|उपलग्ध|उपलब्थ)\s*(?:है|हो)?\s*\]?[\|!।॥]?', '', val, flags=re.IGNORECASE)
    val = re.sub(r'\[?\s*फोटो\s*\]?', '', val, flags=re.IGNORECASE)
    val = re.sub(r'\[?\s*(?:Photo|Available|Not\s*Available)\s*\]?', '', val, flags=re.IGNORECASE)
    
    # 5. Remove photo box border/OCR noise tokens
    val = re.sub(r'\b(?:धर|उपलब्ध|उपलबध|उपलवध|फोटो|है|om|em|eof|oof|ro|fid|fl|Po)\b', '', val, flags=re.IGNORECASE)
    
    # 6. Handle Quarter / Room / House / संख्या prefix: e.g. 'क़्वार्टर नंबर76', 'हाऊस नं. 61', 'सख्या : 5'
    val = re.sub(r'^(?:क़्वार्टर|क्वार्टर|क्वॉर्टर|कमरा|फ्लैट|Flat|Room|Quarter|Qtr|हाऊस|हाउस|सख्या|संख्या|मकान|गृह|म०\s*सं|म०\s*नं)\s*(?:नं(?:०|\.|॑)?|नम्बर|नंबर|संख्या|सं|No\.?)?\s*[:\-–\.,]?\s*', '', val, flags=re.IGNORECASE)
    
    # If string is of format '[Leaked Name / Words], [House No]' (e.g. 'भुवनेश्वर यादव, 86'), extract the house number part
    m_leaked_name = re.match(r'^(?:[\u0900-\u097F\s]{2,})[,\s]+(\d+[\w\u0900-\u097F\-/]*)$', val)
    if m_leaked_name:
        val = m_leaked_name.group(1).strip()
    elif re.match(r'^[\u0900-\u097F\s]{3,}[0०]$', val):
        # Long word ending in stray OCR zero (e.g. 'बाबराले0', 'शीतल क्लॉथ एंपोरिअम0', 'प्रवेश गुप्ता होम0')
        return ""

    # 7. Split on colony / landmark / address words before cleaning so full address does not leak into house number
    # e.g. '22हरी बाबा मार्ग' -> '22', '3/हरी भवन' -> '3', '28-ए,वार्ड नं. 2' -> '28-ए', '7बी/, मोह. कल्लू नागला' -> '7बी'
    val = re.split(
        r'[,/]?\s*(?:मोहल्ला|मोहाला|मोह\b|वार्ड|कॉलनी|कॉलोनी|मार्ग|नियर|निअर|गली|रोड|सड़क|फेज़|फेस|नगर|नगला|नागला|पोस्ट|मंदिर|स्टोर|भवन|निवास|आश्रम|बबराला|बाबराला|संभल|हरी\s*बाबा|हरी|कटकू|कल्लू|यादवन|लेखपाल|म0न0|म०न०|उत्तर\s*प्रदेश|प्रदेश)',
        val,
        flags=re.IGNORECASE
    )[0].strip()
    
    # 8. Remove brackets, pipes, dandas, quotes, colons, noise symbols (>00, +-0, **, ##, &)
    val = re.sub(r'[><+~*|!।॥”"“\'`#?@^&;_\(\)\[\]{}]+', ' ', val)
    
    # Strip photo watermark zero/noise suffix after space (e.g. '67 00' -> '67', '67 0' -> '67', '67 -0' -> '67')
    val = re.sub(r'(\b[1-9]\d*)\s+0+\b', r'\1', val)
    val = re.sub(r'(\b[1-9]\d*)\s*[\-–/]\s*0+\b', r'\1', val)
    
    # 9. Normalize Devanagari numerals to ASCII digits (०३ -> 03, १२ -> 12)
    val = val.translate(DEVANAGARI_DIGITS)
    
    # 10. Normalize sub-houses: '204 2' -> '204/2', '200 /2' -> '200/2', '6 0/11' -> '60/11'
    val = re.sub(r'(?<=\d)\s+(?=\d[/])', '', val)
    val = re.sub(r'(?<=\d)\s*/\s*(?=\d)', '/', val)
    val = re.sub(r'(?<=\d)\s+(?=[1-9]\b)', '/', val)
    
    # 11. Strip trailing stray OCR Devanagari noise / consonants / halants (e.g. '00न्', '3न्', '8न्', ' रे', ' धर', ' है')
    # BUT preserve single Devanagari sub-house characters after digits (e.g. '76 क' → keep 'क', '9 अ' → keep 'अ')
    val = re.sub(r'[\s\u094d]*[न\u0928][\s\u094d]*$', '', val)
    # Only strip 2+ Devanagari chars (noise words like 'रे', 'धर', 'है'), not single sub-house chars
    val = re.sub(r'\s+[\u0900-\u097F]{2,}\s*$', '', val)
    val = re.sub(r'\s+[a-z]\s*$', '', val)
    val = re.sub(r'\s+[\-/]+\s*$', '', val)
    
    # 12. Normalize leading zeros for numbers (04 -> 4, 004 -> 4, 04A -> 4A), but preserve pure '0' or '00'
    val = val.strip()
    if re.match(r'^0+[1-9]', val):
        val = re.sub(r'^0+', '', val)
    
    # 13. Clean space between digits and Devanagari letter/subhouse (3 क -> 3क, 95 अ -> 95अ, 3 व -> 3व)
    val = re.sub(r'(?<=\d)\s+(?=[\u0900-\u097F])', '', val)
    val = re.sub(r'(?<=[\u0900-\u097F])\s+(?=\d)', '', val)
    val = re.sub(r'\s*([/\-])\s*', r'\1', val)

    # 14. Fix Hindi OCR misread of '3' / '३' as 'उ' before sub-house letters or digits
    # In Devanagari electoral rolls, '3' or '३' touching 'ब'/'अ' is read as 'उब', 'उबू', 'उव', 'उअ', etc.
    # Restoring 'उ' to '3' preserves real house numbers like '3ब', '3अ', '3क' instead of stripping them
    val = re.sub(r'^उ(?=[बअवकखग\d/\-])', '3', val)
    val = re.sub(r'(?<=\d)उ+([बअवकखग])', r'\1', val)
    # Strip stray font serifs / matras attached to sub-house letters (e.g. 3बृ -> 3ब, 3बू -> 3ब)
    val = re.sub(r'([बअवकखग])[ूुृ]+', r'\1', val)
    # In Hindi electoral sub-houses, 'व' after digit (e.g. '3व') is a faint-stroke misread of '3ब'
    val = re.sub(r'(?<=\d)व$', 'ब', val)
    val = re.sub(r'(?<=\d)G$', 'ग', val)
    
    # 14. Clean leading/trailing punctuation and collapse spaces
    if val in ('--', '-', '—', '–'):
        return '--'
    val = re.sub(r'^[–\-:\.\s,/]+|[–\-:\.\s,/]+$', '', val).strip()
    val = re.sub(r'\s+', ' ', val)
    
    # 15. Validation: Indian house numbers almost always contain a digit (or standard '--')
    # Reject pure text like 'सिंह', 'यादव', 'कप्तान सिंह', 'कल्लू नगला'
    has_digit = any(c.isdigit() for c in val)
    if not has_digit and val not in ('--', '-', '—', '–'):
        return ""
        
    # 16. If value contains 2+ consecutive Devanagari letters after digit, strip it (e.g. '42लेखपाल' -> '42', '1अप' -> '1')
    # Genuine sub-house suffixes are single letters (e.g. 1अ, 3ब, 7क), not multi-letter words
    val = re.sub(r'(?<=\d)[\u0900-\u097F]{2,}.*$', '', val).strip()
    val = re.sub(r'^[–\-:\.\s,/]+|[–\-:\.\s,/]+$', '', val).strip()
    
    return val



def validate_voter_record(record: VoterRecord) -> VoterRecord:
    """Runs consistency checks on a VoterRecord and flags warnings if anomalies exist."""
    warnings = []
    
    # Check Name
    record.name = clean_hindi_text(record.name)
    if not record.name or len(record.name) < 2:
        warnings.append("मतदाता का नाम अधूरा या अनुपलब्ध है")
    
    # Clean Relative Name
    record.relation_name = clean_hindi_text(record.relation_name)
    record.relation_type = normalize_relation_type(record.relation_type)
    
    # Clean House No with advanced photo watermark and noise filter
    record.house_no = clean_house_no(record.house_no)
    
    # Normalize Part No if present
    if record.part_no:
        record.part_no = normalize_digits(clean_hindi_text(record.part_no))
    
    # Normalize Gender
    record.gender = normalize_gender(record.gender)
    
    # Clean & validate EPIC
    record.epic_no = clean_epic_no(record.epic_no)
    is_valid_epic, epic_defect, _ = is_valid_epic_format(record.epic_no)
    if not is_valid_epic:
        warnings.append(f"पहचान पत्र (EPIC) अमान्य: {epic_defect}")
    
    # Validate Age
    if record.age is not None:
        if record.age < 18 or record.age > 120:
            warnings.append(f"आयु ({record.age}) अमान्य हो सकती है (18-120 अपेक्षित)")
    else:
        warnings.append("आयु का उल्लेख नहीं मिला")
        
    if warnings:
        record.has_warning = True
        record.warning_message = " | ".join(warnings)
        record.confidence_score = max(0.4, 1.0 - (0.2 * len(warnings)))
    else:
        record.has_warning = False
        record.warning_message = None
        record.confidence_score = 1.0
        
    return record


def is_genuine_voter(record: Optional[VoterRecord]) -> bool:
    """
    Determines whether a parsed VoterRecord represents a genuine voter card
    or is an empty box / OCR border artifact / phantom card.
    
    A record is considered a GENUINE voter if:
    1. It is explicitly marked as deleted with a recognized stamp (is_deleted is True), OR
    2. It has a real Devanagari voter name (at least 2 Devanagari characters, not noise) AND
       at least ONE supporting voter signal:
       - A valid/non-empty EPIC number (length >= 5), OR
       - A real relative name (at least 2 Devanagari characters), OR
       - A valid adult age (18 <= age <= 120), OR
       - A non-empty house number (not '--' or empty)
    """
    if record is None:
        return False
        
    if getattr(record, "is_deleted", False):
        return True
        
    name = (getattr(record, "name", "") or "").strip()
    if not name or len(name) < 2:
        return False
        
    devanagari_chars = re.findall(r'[\u0900-\u097F]', name)
    if len(devanagari_chars) < 2:
        return False
        
    # Check if name is purely noise characters or English
    if re.search(r'^[a-zA-Z0-9\s:\-–—\.\*\|_~`\'",;/\\]+$', name):
        return False
        
    # Corroborating signals: at least one real voter attribute must be present
    epic = (getattr(record, "epic_no", "") or "").strip()
    rel_name = (getattr(record, "relation_name", "") or "").strip()
    rel_dev_chars = len(re.findall(r'[\u0900-\u097F]', rel_name))
    age = getattr(record, "age", None)
    has_valid_age = age is not None and 18 <= age <= 120
    house = (getattr(record, "house_no", "") or "").strip()
    has_house = bool(house and house not in ('--', '-', '—', '0'))
    has_epic = bool(epic and len(epic) >= 5)
    has_rel = rel_dev_chars >= 2
    
    return has_epic or has_rel or has_valid_age or has_house
