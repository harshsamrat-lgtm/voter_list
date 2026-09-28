"""
Community & Muslim Voter Detection Engine for UP Electoral Rolls.

Rule:
- If voter's name is a Muslim name -> Identified as Muslim voter.
- OR if relative's (father / husband) name is a Muslim name -> Identified as Muslim voter.
- "कोई एक मुस्लिम नाम हो तो वह मुस्लिम मतदाता होगा" (Either voter or relative is Muslim).

Features:
- Token-based word boundary analysis (avoids false substring matching e.g. 'बी' in 'बीना' or 'अमित').
- Broad Devanagari & Latin/English lexicon covering UP electoral roll naming conventions.
- False-positive safeguards for non-Muslim composites (e.g. 'खान चांद / खान चंद्रा', 'गुलशन सिंह').
- Returns boolean flag, explanatory reason string, and structured token matches.
"""

import re
from typing import Tuple, List, Dict, Any, Optional, Set


# Surnames, Titles, and Honorifics characteristic of Muslim community in UP
MUSLIM_SURNAMES: Set[str] = {
    # Hindi Surnames & Titles
    'अली', 'खान', 'खां', 'खाँ', 'अहमद', 'अह़मद', 'अहम़द', 'हुसैन', 'हुसेन', 'हसन', 
    'अंसारी', 'अन्सारी', 'कुरैशी', 'कुरेशी', 'कुरेशा', 'सैफी', 'सैफ़ी', 'सिद्दीकी', 
    'सिद्दीक़ी', 'उस्मानी', 'शेख', 'शेख़', 'मंसूरी', 'इदरीसी', 'इदरीशी', 'मलिक', 
    'अब्बासी', 'काजमी', 'काज़मी', 'रिजवी', 'रिज़वी', 'अलवी', 'नक़वी', 'नकवी', 'गाजी', 
    'गाज़ी', 'शाह', 'शाही', 'बेगम', 'वेगम', 'खातून', 'निशां', 'निशाँ', 'बानो', 'जहाँ', 
    'जहां', 'मुग़ल', 'मुगल', 'सिद्दीक', 'अंसारी', 'उस्मानी', 'पठान',

    # English / Latin Surnames
    'ali', 'khan', 'ahmad', 'ahmed', 'hussain', 'husain', 'hasan', 'ansari', 'qureshi', 
    'quresha', 'saifi', 'siddiqui', 'siddiki', 'usmani', 'mansoori', 'mansuri', 'idrisi', 
    'idrishi', 'malik', 'abbasi', 'kazmi', 'rizvi', 'alvi', 'naqvi', 'begum', 'vegam', 
    'khatoon', 'khatun', 'nishaan', 'bano', 'jahan', 'pathan'
}

# Distinctive Muslim Given Names (Male & Female)
MUSLIM_GIVEN_NAMES: Set[str] = {
# Hindi Male Names & Titles
    'मोहम्मद', 'मोह०', 'मो०', 'मु०', 'मौहम्मद', 'मुहम्मद', 'मोहम्मद', 'सैयद', 'सय्यद', 'मीर',
    'हाजी', 'कारी', 'मुफ्ती', 'मौलाना', 'अब्दुल', 'शम्मो', 'लियाकत', 'इदरीश', 'इदरीस', 'नौशे',
    'जैवुन', 'कवीर', 'कबीर', 'तनवीर', 'नफीस', 'इसरार', 'रहमत', 'गुलामुद्दीन', 'इरफान', 'जैनुद्दीन',
    'असलम', 'यामीन', 'सलीमुददीन', 'सलीमुद्दीन', 'सफरूददीन', 'सफरुद्दीन', 'बशीर', 'शाहिद', 'सलीम',
    'अख्तर', 'अतर', 'जली', 'नवी', 'अजमेरी', 'समसुल', 'गुलाममोहम्मद', 'इमरान', 'काकुल', 'अलिहसन',
    'जाकिर', 'इकराम', 'नासिर', 'जाहिद', 'साजिद', 'यासीन', 'हसनैन', 'अकरम', 'हबीब', 'मुमताज',
    'मुमताज़', 'जावेद', 'शाहांजा', 'अन्सार', 'आसिफ', 'साबिर', 'साविर', 'शाकिर', 'विस्मिल्ला',
    'इशाहक', 'दिलशाद', 'हमीद', 'रौनक', 'इरशाद', 'रहमान', 'आदिल', 'फ़ैज़ी', 'फैजी', 'शौकत',
    'मोनिस', 'दीन', 'मुकीन', 'तौकीर', 'जीशान', 'अरहान', 'अदनान', 'फैजान', 'फ़ैज़ान', 'मुशर्रफ',
    'मुश्ताक', 'मुशताक', 'वाहिद', 'माजिद', 'शब्बीर', 'ताहिर', 'जमील', 'खलील', 'हकीम', 'नसीम', 'शमीम',
    'अजहर', 'मजहर', 'अकबर', 'बाबर', 'उमर', 'उस्मान', 'फारूक', 'याकूब', 'मुनीर', 'वसीम',
    'नाजिम', 'काजिम', 'सऊद', 'सुभान', 'मुबारिक', 'मुबारक', 'रियाज', 'रियाज़', 'शहाबुद्दीन',
    'सराजुद्दीन', 'सिराज', 'शफीक', 'रफीक', 'अतीक', 'मतीन', 'रईस', 'अनीस', 'मुस्तफा', 'मुर्तजा',
    'मर्तुजा', 'जफर', 'जाफर', 'अय्यूब', 'सय्यूब', 'यूसुफ', 'शमशाद', 'इस्तखार', 'इंतजार', 'इन्तजार',
    'आबिद', 'वाजिद', 'आलम', 'कमर', 'अफसर', 'अफ़सर', 'इमरोज', 'शमशीर', 'अजगर', 'अहमदी',
    'शमशुद्दीन', 'शमसुद्दीन', 'मंसूर', 'मक्सूद', 'मकसूद', 'अहसान', 'एहसान', 'मेहरबान', 'साकिब',
    'अजमल', 'अरशद', 'शहजाद', 'शहजादे', 'शहजादा', 'शाहरुख', 'शाहरूख', 'शारुख', 'अनवर', 'तहसीन', 'मेहराज', 'मुस्तकीम', 'तालिब',
    'तासिम', 'मुजाहिद', 'मुर्तज़ा', 'मुशर्रफ़', 'सगीर', 'सद्दाम', 'सादान', 'साहिल', 'सुहैल',
    'सोहेल', 'हनीफ', 'हसीब', 'हाशिम', 'हैदर', 'आसिम', 'आकिब', 'अहसन', 'इब्राहिम', 'इस्माइल',
    'सुलेमान', 'बिलाल', 'हमीदुल्लाह', 'नजीबुल्लाह', 'अज़ीम', 'अजीम', 'अतीकुर्रहमान',
    'सिद्दीक', 'नईम', 'फईम', 'शकील', 'गुलफाम', 'अजीज', 'जुम्मन', 'रज्जाक', 'साकिर', 'नवाव',
    'फरियाद', 'निशार', 'मुख्तयार', 'सब्बन', 'मुकीम', 'निजामुददीन', 'शौकीन', 'गफूर', 'मोमिन',
    'सिकन्दर', 'रज्जन', 'भूरे', 'मसूद', 'रियासत', 'इकबाल', 'इक़बाल', 'राशिद', 'सरदारी', 'अशमीर', 'असमीर',
    'अमजद', 'अशरफ', 'इरशाद', 'इम्तियाज', 'इफ्तेखार', 'महमूद', 'जुनैद', 'अमान', 'अरमान', 'दानिश',
    'शाहनवाज', 'शहनवाज', 'सरफराज', 'फिरोज', 'शफी', 'कलीम', 'नईमुद्दीन', 'समीर', 'सोहेल', 'रशीद', 'इशहाक',

    # Hindi Female Names
    'अमिना', 'अमीना', 'जहीना', 'शकीला', 'सन्नो', 'सन्नौ', 'ईमसेन', 'हमीदन', 'आफरोस', 'परवीन', 'हाजरा',
    'शहीदन', 'नगीना', 'शमा', 'शकीना', 'सलीमन', 'वहीदन', 'शबनम', 'अफ़साना', 'अफसाना', 'इमराना',
    'गुलबहार', 'नगमा', 'रिज़वाना', 'रिजवाना', 'शहनाज', 'शहनाज़', 'शायरा', 'सायरा', 'फरहाना',
    'रुकसाना', 'रुखसाना', 'ज़ेहरा', 'जेहरा', 'फातिमा', 'अकबरी', 'अजरा', 'अज़रा', 'नूर', 'नूरजहाँ',
    'नूरजहां', 'शगुफ्ता', 'शमीमा', 'शहला', 'साहिबा', 'सबीहा', 'हुमेरा', 'हुमैरा', 'तसलीम', 'शाहीन',
    'शाइस्ता', 'शाहिस्ता', 'गुलशनआरा', 'जन्नत', 'महविश', 'नाज़नीन', 'नाजनीन', 'रुमीना', 'साजिया',
    'रईसन', 'नसरीन', 'नरगिस', 'शमीना', 'जरीफन', 'शरीफन', 'समरीन', 'रजिया', 'जरीना', 'अल्ला',
    'नईमा', 'इशरत', 'जाइदा', 'रूबीना', 'आसिया', 'आशिया', 'हुमा', 'तंजीम', 'शबाना', 'फरहा', 'गुलनाज',
    'शाइना', 'शाहिना', 'इरशाना', 'फरजाना',

    # English / Latin equivalents
    'mohammed', 'mohammad', 'mohd', 'md', 'sayeed', 'sayed', 'syed', 'abdul', 'liyaqat', 
    'liyakat', 'shaukat', 'salim', 'saleem', 'irfan', 'rizwan', 'imran', 'salman', 'sajid', 
    'arif', 'dilshad', 'irshad', 'shabnam', 'shakeela', 'shakila', 'mumtaz', 'javed', 'tauqeer', 
    'tauqir', 'zeeshan', 'parveen', 'parvin', 'wahidan', 'hamidan', 'sufiyan', 'naseem', 'shamim', 
    'azhar', 'akbar', 'babar', 'umar', 'usman', 'farooq', 'yaqoob', 'muneer', 'waseem', 'wasim', 
    'najim', 'saud', 'faizan', 'musharraf', 'mushtaq', 'wahid', 'majidd', 'shabbir', 'tahir', 
    'jameel', 'khalil', 'hakeem', 'raees', 'mustafa', 'jafar', 'zafar', 'yusuf', 'shamshad', 
    'abid', 'wajid', 'aalam', 'alam', 'qamar', 'ehsan', 'meherban', 'sakib', 'ajmal', 'arshad', 
    'shahzad', 'shahrukh', 'anwar', 'tahseen', 'mehraj', 'talib', 'saddam', 'sahil', 'suhail', 
    'sohel', 'hanif', 'hashim', 'haider', 'asim', 'ibrahim', 'ismail', 'suleman', 'bilal', 
    'rubina', 'farhana', 'ruksana', 'rooksana', 'fatima', 'zehra', 'nagma', 'rizwana', 'shahnaz'
}

# Strong non-Muslim markers (Hindu/Sikh/Jain names/surnames) that protect against false positives
NON_MUSLIM_INDICATORS: Set[str] = {
    # Hindi
    'सिंह', 'शर्मा', 'वर्मा', 'यादव', 'गुप्ता', 'गुप्त', 'वार्ष्णेय', 'कुमार', 'कुमारी', 
    'भारती', 'जाटव', 'वाल्मीकि', 'सैनी', 'चौहान', 'कश्यप', 'ठाकुर', 'अग्रवाल', 'जैन', 
    'मिश्रा', 'पाण्डेय', 'पांडेय', 'तिवारी', 'दीक्षित', 'शुक्ला', 'द्विवेदी', 'लाल', 'पाल',
    'देवी', 'प्रसाद', 'राम', 'प्रकाश', 'चन्द', 'चंद', 'चन्द्र', 'चन्द्रा', 'चन्द्रकान्त',
    'दीपक', 'सुरेश', 'रमेश', 'महेश', 'दिनेश', 'राकेश', 'राजेश', 'मुकेश', 'हरिओम', 'ब्रिजेश',
    'शिव', 'शंकर', 'ओम', 'विष्णु', 'कृष्ण', 'गोपाल', 'नंद', 'नन्द', 'राजेन्द्र', 'राजेन्दर',
    'महेन्द्र', 'सत्येन्द्र', 'धर्मेन्द्र', 'जितेन्द्र', 'वीरेंद्र', 'रवि', 'सुनील', 'अनिल',

    # English
    'singh', 'sharma', 'verma', 'yadav', 'gupta', 'varshney', 'kumar', 'kumari', 'bharti', 
    'jatav', 'saini', 'chauhan', 'kashyap', 'thakur', 'agarwal', 'jain', 'mishra', 'pandey', 
    'tiwari', 'dixit', 'shukla', 'lal', 'pal', 'devi', 'prasad', 'ram', 'prakash', 'chand'
}

# Strict composite phrase exclusions (e.g. 'खान चंद्रा', 'गुलशन सिंह')
FALSE_POSITIVE_PATTERNS = [
    r'खान\s*चांद', r'खान\s*चंद', r'खान\s*चंद्र', r'खान\s*चंद्रा', r'खानचन्द', r'खानचंद',
    r'khan\s*chand', r'khan\s*chandra',
    r'गुलशन\s*सिंह', r'गुलशन\s*कुमार', r'gulshan\s*singh', r'gulshan\s*kumar',
    r'चमन\s*लाल', r'रोशन\s*लाल', r'chaman\s*lal', r'roshan\s*lal'
]


def normalize_devanagari_nukta(text: Optional[str]) -> str:
    """
    Normalizes Devanagari characters with Nukta (\\u093c) to their base form:
    e.g. 'इक़बाल' -> 'इकबाल', 'फ़ैज़ान' -> 'फैजान', 'ग़ाज़ी' -> 'गाजी', 'शहज़ाद' -> 'शहजाद'.
    Also strips zero-width joiners (\\u200c, \\u200d) and BOM.
    """
    if not text:
        return ""
    t = re.sub(r'[\u200c\u200d\uFEFF]', '', str(text))
    t = t.replace('\u093c', '')
    nukta_map = {
        'क़': 'क', 'ख़': 'ख', 'ग़': 'ग', 'ज़': 'ज', 'ड़': 'ड',
        'ढ़': 'ढ', 'फ़': 'फ', 'य़': 'य', 'ऩ': 'न', 'ऱ': 'र'
    }
    for n_char, base_char in nukta_map.items():
        t = t.replace(n_char, base_char)
    return t


def tokenize_name(text: Optional[str]) -> List[str]:
    """Cleans punctuation, normalizes nuktas, and splits text into discrete word tokens."""
    if not text:
        return []
    cleaned = normalize_devanagari_nukta(text)
    cleaned = re.sub(r'[,.:;\"\'\(\)\[\]\/\\\|\!\?0-9\u0964\u0965\-]', ' ', cleaned)
    tokens = [t.strip() for t in cleaned.split() if len(t.strip()) > 1]
    return tokens


def check_single_name(name_text: Optional[str]) -> Tuple[bool, List[str]]:
    """
    Evaluates whether a single person's name (voter or relative) indicates Muslim identity.
    Returns: (is_muslim, matched_tokens_list)
    """
    if not name_text or not name_text.strip():
        return False, []

    cleaned_for_check = name_text
    for pat in FALSE_POSITIVE_PATTERNS:
        cleaned_for_check = re.sub(pat, ' ', cleaned_for_check, flags=re.IGNORECASE)

    tokens = tokenize_name(cleaned_for_check)
    if not tokens:
        return False, []

    lower_tokens = [t.lower() for t in tokens]

    # Check for strong non-Muslim markers
    has_non_muslim_marker = any(
        (t in NON_MUSLIM_INDICATORS or t.lower() in NON_MUSLIM_INDICATORS)
        for t in tokens
    )
    if has_non_muslim_marker:
        return False, []

    # Normalized lookup sets without nuktas
    norm_muslim_surnames = {normalize_devanagari_nukta(s).lower() for s in MUSLIM_SURNAMES}
    norm_muslim_given = {normalize_devanagari_nukta(s).lower() for s in MUSLIM_GIVEN_NAMES}

    matched: List[str] = []
    for orig_t, low_t in zip(tokens, lower_tokens):
        norm_t = normalize_devanagari_nukta(low_t)
        if norm_t in norm_muslim_surnames or low_t in MUSLIM_SURNAMES or orig_t in MUSLIM_SURNAMES:
            matched.append(orig_t)
        elif norm_t in norm_muslim_given or low_t in MUSLIM_GIVEN_NAMES or orig_t in MUSLIM_GIVEN_NAMES:
            matched.append(orig_t)
        elif re.search(r'(उद्दीन|उददीन|उल्लाह|uddin|uddeen|ullah)$', low_t):
            matched.append(orig_t)

    return (len(matched) > 0), matched


def identify_voter_community(
    voter_name: Optional[str],
    relative_name: Optional[str],
    relation_type: str = "पिता"
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Core Rule:
    - If voter's name is Muslim -> True
    - OR if relative's (father/husband) name is Muslim -> True
    - "कोई एक मुस्लिम नाम हो तो वह मुस्लिम मतदाता होगा"

    Returns:
    - is_muslim: bool
    - reason_display: str (e.g. "पहचान: पति: लियाकत अली (लियाकत, अली)")
    - metadata: dict with details
    """
    is_v, m_v = check_single_name(voter_name)
    is_r, m_r = check_single_name(relative_name)

    is_muslim = is_v or is_r

    if is_muslim:
        reasons = []
        if is_v:
            reasons.append(f"मतदाता: {', '.join(m_v)}")
        if is_r:
            rel_label = relation_type or "सम्बन्धी"
            reasons.append(f"{rel_label}: {', '.join(m_r)}")
        reason_display = " | ".join(reasons)
    else:
        reason_display = ""

    metadata = {
        "is_muslim": is_muslim,
        "voter_name_matched": is_v,
        "voter_tokens": m_v,
        "relative_name_matched": is_r,
        "relative_tokens": m_r,
        "reason": reason_display
    }

    return is_muslim, reason_display, metadata
