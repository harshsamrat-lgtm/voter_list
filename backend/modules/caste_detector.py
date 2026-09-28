"""
Caste and Surname Detection Module for UP Voter Lists.
Identifies caste/community based on the last name (surname) of:
1. The voter, OR
2. The father, OR
3. The husband.

Rule: "जाति नाम, पिता अथवा पति के अंतिम नाम से पहिचानी जाए"
"""

import re
from typing import Dict, List, Set, Tuple, Optional

# Standard presets for UP/North India castes and their recognized surnames
CASTE_PRESETS: Dict[str, Dict] = {
    "muslim": {
        "label": "मुस्लिम / अंसारी / कुरैशी / खान / शेख",
        "description": "मुस्लिम समुदाय: खान, अंसारी, कुरैशी, शेख, सिद्दीकी, सैफी, पठान, मलिक, शाह आदि",
        "surnames": [
            "खान", "खां", "खाँ", "अंसारी", "अन्सारी", "कुरैशी", "कुरेशी", "कुरेशा", "शेख", "शेख़",
            "सिद्दीकी", "सिद्दीक़ी", "सिद्दीक", "सैफी", "सैफ़ी", "उस्मानी", "पठान", "अब्बासी", "मंसूरी",
            "इदरीसी", "इदरीशी", "मलिक", "कस्सार", "गाजी", "गाज़ी", "शाह", "शाही", "अली", "अहमद",
            "हुसैन", "हुसेन", "हसन", "बेग", "मिर्जा", "मिर्ज़ा", "चिश्ती", "कादरी", "रजा", "रज़ा",
            "साबरी", "अल्वी", "काजमी", "रिजवी", "मुगल", "मुग़ल"
        ]
    },
    "yadav": {
        "label": "यादव / अहीर",
        "description": "यादव, अहीर, अहिर, ग्वाला, गोप आदि",
        "surnames": ["यादव", "अहीर", "अहिर", "ग्वाला", "गोप"]
    },
    "jatav_sc": {
        "label": "जाटव / अनुसूचित जाति (SC)",
        "description": "जाटव, भारती, बौद्ध, जाटवजी, चमार, कर्दम, माहौर, दोहरे, सूर्यवंशी आदि",
        "surnames": [
            "जाटव", "भारती", "बौद्ध", "जाटवजी", "चमार", "कर्दम",
            "माहौर", "दोहरे", "सूर्यवंशी", "पिप्पल", "प्रभाकर", "मेघवाल", "रविदास", "सोनकर"
        ]
    },
    "brahmin": {
        "label": "ब्राह्मण / शर्मा / तिवारी / मिश्रा / गौतम",
        "description": "शर्मा, तिवारी, मिश्रा, मिश्र, दुबे, पाण्डेय, पांडे, शुक्ला, शुक्ल, गौतम आदि",
        "surnames": [
            "शर्मा", "तिवारी", "मिश्रा", "मिश्र", "दुबे", "पांडेय", "पाण्डेय", "पांडे",
            "शुक्ला", "शुक्ल", "उपाध्याय", "त्रिपाठी", "दीक्षित", "चौबे", "पाठक",
            "द्विवेदी", "त्रिवेदी", "जोशी", "व्यास", "गौतम", "भारद्वाज", "शास्त्री",
            "अग्निहोत्री", "बाजपेयी", "वाजपेयी", "अवस्थी", "वत्स", "झा", "ओझा", "नागर", "अत्रि", "अत्री"
        ]
    },
    "rajput": {
        "label": "ठाकुर / राजपूत / चौहान / भदौरिया",
        "description": "ठाकुर, राजपूत, चौहान, राठौर, तोमर, भदौरिया, जादौन, राघव आदि",
        "surnames": [
            "राजपूत", "ठाकुर", "चौहान", "राठौर", "राठौड़", "तोमर", "सोमवंशी",
            "सिसोदिया", "राघव", "परिहार", "पुंडीर", "भदौरिया", "जादौन", "सेंगर",
            "बैस", "सोलंकी", "चंदेल", "पंवार", "परमार", "शेखावत", "राजावत",
            "कछवाहा", "बिष्ट", "नेगी", "रावत", "राणावत"
        ]
    },
    "vaishya": {
        "label": "वैश्य / वार्ष्णेय / गुप्ता / अग्रवाल / बंसल",
        "description": "वार्ष्णेय, गुप्ता, गुप्त, अग्रवाल, बंसल, गोयल, मित्तल, जैन, मोदी आदि",
        "surnames": [
            "वार्ष्णेय", "गुप्ता", "गुप्त", "अग्रवाल", "अग्रबाल", "बंसल", "गोयल",
            "मित्तल", "सिंघल", "माहेश्वरी", "जैन", "मोदी", "केसरवानी", "कपूर",
            "कंसल", "महाजन", "रुंगटा", "गर्ग", "तायल", "पोरवाल", "सराफ", "खंडेलवाल"
        ]
    },
    "kushwaha": {
        "label": "कुशवाहा / कुशवाह / मौर्य / शाक्य",
        "description": "कुशवाहा, कुशवाह, मौर्य, शाक्य, कोइरी, महतो, दांगी, माली आदि",
        "surnames": ["कुशवाहा", "कुशवाह", "मौर्य", "मौर्या", "शाक्य", "कोइरी", "महतो", "दांगी", "माली"]
    },
    "saini": {
        "label": "सैनी / सैनीवाल",
        "description": "सैनी, सैनीवाल आदि",
        "surnames": ["सैनी", "सैनीवाल"]
    },
    "pal": {
        "label": "बघेल / गडरिया / धनगर",
        "description": "बघेल, धनगर, गडरिया आदि",
        "surnames": ["बघेल", "धनगर", "गडरिया"]
    },
    "kurmi": {
        "label": "वर्मा / कुर्मी / पटेल",
        "description": "वर्मा, कुर्मी, पटेल, गंगवार, कटियार, सचान आदि",
        "surnames": ["वर्मा", "कुर्मी", "पटेल", "गंगवार", "कटियार", "सचान", "कनोडिया"]
    },
    "lodhi": {
        "label": "लोधी / लोध / राजपूत",
        "description": "लोधी, लोध, लोधा, राजपूत लोधी आदि",
        "surnames": ["लोधी", "लोध", "लोधा"]
    },
    "jat_gurjar": {
        "label": "जाट / गूजर / गुर्जर / चौधरी",
        "description": "जाट, गुर्जर, गूजर, चौधरी, राणा आदि",
        "surnames": ["जाट", "गुर्जर", "गूजर", "चौधरी", "राना", "अहलावत", "गुलिया", "दलाल", "हुड्डा", "तंवर", "भाटी"]
    },
    "kashyap": {
        "label": "कश्यप / निषाद / कहार / बिंद",
        "description": "कश्यप, निषाद, बिंद, कहार, मल्लाह, धीवर, मांझी, केवट आदि",
        "surnames": ["कश्यप", "निषाद", "बिंद", "बिन्द", "कहार", "मल्लाह", "धीवर", "मांझी", "केवट", "तुरहा", "साहनी", "बाथम"]
    },
    "prajapati": {
        "label": "प्रजापति / कुम्हार",
        "description": "प्रजापति, कुम्हार, चक्रवर्ती आदि",
        "surnames": ["प्रजापति", "प्र्जापति", "कुम्हार", "चक्रवर्ती", "प्रजापत"]
    },
    "kori": {
        "label": "कोरी / कबीरपंथी / बुनकर",
        "description": "कोरी, कबीरपंथी, बुनकर आदि",
        "surnames": ["कोरी", "कोरीवाल", "बुनकर", "कबीरपंथी", "शाक्यवार", "तंतवाय", "कोरीया", "कोली"]
    },
    "valmiki": {
        "label": "वाल्मीकि / बाल्मीकी",
        "description": "वाल्मीकि, बाल्मीकि, वाल्मीक, बाल्मीकी, लालबेगी आदि",
        "surnames": ["वाल्मीकि", "वाल्मीक", "बाल्मीकि", "बाल्मीकी", "बालमीकी", "लालबेगी", "भंगी", "घारू", "चांडाल", "मेहतर"]
    },
    "sain": {
        "label": "सैन / सेन / नाई / हज्जाम",
        "description": "सैन, सेन, नाई, हज्जाम आदि",
        "surnames": ["सैन", "सेन", "नाई", "हज्जाम", "उस्ता", "हजाम"]
    },
    "diwakar": {
        "label": "दिवाकर / धोबी / रजक",
        "description": "दिवाकर, धोबी, रजक, कनौजिया, बरेठा आदि",
        "surnames": ["दिवाकर", "धोबी", "रजक", "कनौजिया", "बरेठा", "बैठा"]
    },
    "kayastha": {
        "label": "कायस्थ / श्रीवास्तव / सक्सेना / निगम",
        "description": "श्रीवास्तव, सक्सेना, निगम, माथुर, भटनागर, अस्थाना, कुलश्रेष्ठ आदि",
        "surnames": ["श्रीवास्तव", "सक्सेना", "निगम", "माथुर", "भटनागर", "अस्थाना", "कुलश्रेष्ठ", "अम्बष्ट", "सिन्हा"]
    },
    "khatik": {
        "label": "खटीक / सोनकर",
        "description": "खटीक, सोनकर, राजौरा, खींची आदि",
        "surnames": ["खटीक", "सोनकर", "राजौरा", "खींची"]
    },
    "vishwakarma": {
        "label": "विश्वकर्मा / पांचाल / जांगिड़ / बढ़ई",
        "description": "विश्वकर्मा, पांचाल, जांगिड़, धीमान, बढ़ई, लोहार, मिस्त्री आदि",
        "surnames": ["विश्वकर्मा", "पांचाल", "जांगिड़", "धीमान", "बढ़ई", "लोहार", "लुहार", "मिस्त्री"]
    },
    "sonar": {
        "label": "सोनार / स्वर्णकार / सोनी",
        "description": "सोनी, स्वर्णकार, सुनार आदि",
        "surnames": ["सोनी", "स्वर्णकार", "सुनार", "पोद्दार"]
    },
    "jaiswal": {
        "label": "जायसवाल / कलवार",
        "description": "जायसवाल, कलवार, ब्याहुत आदि",
        "surnames": ["जायसवाल", "कलवार", "ब्याहुत"]
    },
    "sikh_punjabi": {
        "label": "सिख / पंजाबी",
        "description": "कौर, ढिल्लों, सेठी, चीमा, संधू, अरोड़ा, खन्ना, भल्ला आदि",
        "surnames": ["कौर", "ढिल्लों", "सेठी", "चीमा", "संधू", "अरोड़ा", "खन्ना", "भल्ला", "मल्होत्रा"]
    },
    "giri": {
        "label": "गिरि / गोस्वामी / गोसाईं",
        "description": "गिरि, गिरी, गोस्वामी, गोसाई, गोसाईं, पुरी आदि",
        "surnames": ["गिरि", "गिरी", "गोस्वामी", "गोसाई", "गोसाईं", "पुरी", "पूरी"]
    },
    "gihar": {
        "label": "गिहार / गीहार / विमुक्त",
        "description": "गिहार, गीहार, गिहारे, कंजर आदि",
        "surnames": ["गिहार", "गीहार", "गिहारे", "कंजर", "झांझ", "सोडा", "विमुक्त"]
    },
    "bhumihar": {
        "label": "भूमिहार / त्यागी / राय",
        "description": "त्यागी, राय, शाही आदि",
        "surnames": ["त्यागी"]
    },
    "prajapati": {
        "label": "प्रजापति / कुम्हार",
        "description": "प्रजापति, कुम्हार, शिल्पकार आदि",
        "surnames": ["प्रजापति", "कुम्हार", "शिल्पकार", "कुम्हारन"]
    },
    "dhobi_sc": {
        "label": "धोबी / रजक / कनौजिया (SC)",
        "description": "रजक, कनौजिया, धोबी, दिवाकर आदि",
        "surnames": ["रजक", "कनौजिया", "धोबी", "दिवाकर"]
    }
}


COMPOUND_FIRST_NAMES_SEN = {"भीम", "चन्द्र", "इन्द्र", "उग्र", "सत्य", "कुल", "हरि", "राम", "जय", "नरेन्द्र"}

# Common individual given names that must NEVER be treated as surnames when appearing alone
COMMON_GIVEN_NAMES = {
    "सविता", "संगीता", "सुनीता", "अनिता", "कविता", "बबीता", "बबिता", "मंजू", "कमलेश",
    "विमलेश", "मिथिलेश", "राजेश", "मुकेश", "दिनेश", "सुरेश", "रमेश", "महेश", "नरेश",
    "सुनील", "अनिल", "सुशील", "मनोज", "विनोद", "प्रमोद", "अशोक", "विजय", "संजय", "अजय",
    "राजो", "नेमवती", "शीला", "माया", "उषा", "कमला", "सरोज", "सन्तोष", "संतोष",
    "सागर", "कमल", "निर्मल", "आनंद", "आनन्द", "किरण", "किरन",
    "राहुल", "रोहित", "अमित", "सुमित", "दीपक", "सचिन", "संदीप", "प्रदीप", "कुलदीप",
    "मनवीर", "रनवीर", "धर्मवीर", "सत्यवीर", "कर्मवीर", "महीपाल", "नेकराम", "छोटेलाल"
}

# Multi-caste / neutral suffixes that can be used across multiple communities
# User Rule: "किसी नाम के अंत मे अन्य जाती के भी 'पाल' 'कुमार', 'सिंह', 'चन्द्र' लिख सकते हैं। जाति स्वनिर्धारण के लिए इसको भी नियम मे जोड़ो"
AMBIGUOUS_MULTI_CASTE_SUFFIXES: Set[str] = {
    "पाल", "कुमार", "सिंह", "चन्द्र", "चंद्र", "लाल", "प्रसाद", "दीन", "शरण", "दास", "दत्त"
}

# OCR Misprint & Typo Normalization Mapping
# User Rule: "जिस प्रकार 'वाष्रणेय' मिस प्रिंटेड जाति 'वार्ष्णेय' हो सकती है"
OCR_SURNAME_TYPO_MAP: Dict[str, str] = {
    # वार्ष्णेय (वैश्य)
    "वाष्रणेय": "वार्ष्णेय",
    "वाष्र्णेय": "वार्ष्णेय",
    "वारष्णेय": "वार्ष्णेय",
    "वार्षणेय": "वार्ष्णेय",
    "वाष्णेय": "वार्ष्णेय",
    "वार्ष्णे": "वार्ष्णेय",
    "वार्ष्णेयी": "वार्ष्णेय",
    "वाष्णय": "वार्ष्णेय",
    "वार्ष्णीय": "वार्ष्णेय",
    "वाष्णैय": "वार्ष्णेय",
    "वार्ष्णेय़": "वार्ष्णेय",

    # अग्रवाल (वैश्य)
    "अग्रबाल": "अग्रवाल",
    "अग्रवल": "अग्रवाल",
    "अग्रवाल़": "अग्रवाल",
    "अग्र्वाल": "अग्रवाल",

    # गुप्ता (वैश्य)
    "गुप्रता": "गुप्ता",
    "गुपता": "गुप्ता",
    "गुप्ता़": "गुप्ता",

    # प्रजापति
    "प्र्जापति": "प्रजापति",
    "प्रजापती": "प्रजापति",
    "प्रजापति़": "प्रजापति",

    # उपाध्याय (ब्राह्मण)
    "उपाध्याए": "उपाध्याय",
    "उपाद्याय": "उपाध्याय",
    "उपाध्‍याय": "उपाध्याय",
    "उपाध्यय": "उपाध्याय",
    "उपाध्या": "उपाध्याय",
    "उपाध्याय़": "उपाध्याय",

    # भारद्वाज (ब्राह्मण)
    "भारद्वज": "भारद्वाज",
    "भारदवाज": "भारद्वाज",
    "भारद्धाज": "भारद्वाज",

    # चौधरी (जाट / गुर्जर)
    "चौधरि": "चौधरी",
    "चौधुरी": "चौधरी",
    "चोधरी": "चौधरी",
    "चौदरी": "चौधरी",

    # चौहान (ठाकुर / राजपूत)
    "चौहन": "चौहान",
    "चोहान": "चौहान",
    "चौहान्": "चौहान",

    # राठौर (ठाकुर / राजपूत)
    "राठौड़": "राठौर",
    "राठोर": "राठौर",
    "राठोड": "राठौर",

    # यादव (यादव)
    "यादब": "यादव",
    "यादव्": "यादव",

    # प्रजापति (प्रजापति / कुम्हार)
    "प्र्जापति": "प्रजापति",
    "प्रजापती": "प्रजापति",
    "प्रजापत": "प्रजापति",

    # वाल्मीकि (वाल्मीकि)
    "बाल्मीकी": "वाल्मीकि",
    "वाल्मीक": "वाल्मीकि",
    "बाल्मीकि": "वाल्मीकि",
    "बालमीकी": "वाल्मीकि",

    # कश्यप (कश्यप / निषाद)
    "कस्यप": "कश्यप",
    "कश्याप": "कश्यप",
    "कश्यप्प": "कश्यप",

    # कुशवाहा (कुशवाहा / मौर्य)
    "कुशवाह": "कुशवाहा",
    "कुसवाहा": "कुशवाहा",
    "कुसवाह": "कुशवाहा",

    # कायस्थ
    "श्रीवास्तवा": "श्रीवास्तव",
    "श्रीवास्तब": "श्रीवास्तव",
    "स्रीवास्तव": "श्रीवास्तव",
    "सक्शेना": "सक्सेना",
    "सकसेना": "सक्सेना",

    # ब्राह्मण अन्य
    "शमार्": "शर्मा",
    "शर्म": "शर्मा",
    "शर्मा़": "शर्मा",
    "गोतम": "गौतम",
    "दिक्षित": "दीक्षित",
    "द्वेवेदी": "द्विवेदी",
    "दुवेदी": "द्विवेदी",

    # वैश्य अन्य
    "महेश्वरी": "माहेश्वरी",
    "माहेस्वरी": "माहेश्वरी",
}


def _init_decompounding_surnames() -> List[str]:
    surnames = set()
    for key, preset in CASTE_PRESETS.items():
        if key == "muslim":
            continue
        for s in preset.get("surnames", []):
            if s not in AMBIGUOUS_MULTI_CASTE_SUFFIXES and len(s) >= 2:
                surnames.add(s)
    for typo in OCR_SURNAME_TYPO_MAP.keys():
        surnames.add(typo)
    # Sort descending by length so longer surnames match first (e.g. 'वार्ष्णेय' before 'शर्मा')
    return sorted(list(surnames), key=len, reverse=True)


DECOMPOUNDING_SURNAMES: List[str] = _init_decompounding_surnames()


def split_compound_surname(token: str) -> Optional[Tuple[str, str]]:
    """
    Checks if a single Hindi word token has a caste surname appended without space.
    User Rule: "बिना स्पेस के भी जाति लिखी हो सकती है।"
    e.g. 'सुनीलवार्ष्णेय' -> ('सुनील', 'वार्ष्णेय')
         'अमितशर्मा' -> ('अमित', 'शर्मा')
         'सुनीलवाष्रणेय' -> ('सुनील', 'वार्ष्णेय') (via typo normalization)
         'रेखायादव' -> ('रेखा', 'यादव')
    Requires prefix (given name) to be >= 2 characters.
    Never splits on ambiguous suffixes ('पाल', 'कुमार', 'सिंह', 'चन्द्र').
    """
    if not token or len(token) < 4:
        return None
    for sur in DECOMPOUNDING_SURNAMES:
        if token.endswith(sur):
            prefix = token[:-len(sur)].strip()
            # Prefix must be at least 2 characters (valid Hindi given name part)
            if len(prefix) >= 2:
                canonical = OCR_SURNAME_TYPO_MAP.get(sur, sur)
                return prefix, canonical
    return None


def is_single_given_name(name_str: Optional[str]) -> bool:
    """
    Returns True if name_str contains only a single given name (e.g. 'सविता', 'मनवीर', 'राजो'),
    or a single given name followed by a generic honorific/suffix like 'देवी', 'कुमारी', 'जी'.
    Per rule: "केवल नाम के अनुसार जाति निर्धारित नहीं करनी है"
    Such voters do not have an independent caste surname.
    Note: If name_str is a concatenated word containing a caste surname without space
    (e.g. 'सुनीलवार्ष्णेय', 'अमितशर्मा'), it is treated as a full name (returns False).
    """
    if not name_str:
        return True
    clean = re.sub(r'[\u200c\u200d\uFEFF]', '', str(name_str))
    clean = re.sub(r'[^\w\s\u0900-\u097F]', ' ', clean)
    tokens = clean.split()
    if not tokens:
        return True
    if len(tokens) > 1 and tokens[-1] in ("जी", "साहेब", "साहब", "श्री", "देवी", "कुमारी", "बेगम", "बानो", "खातून", "वेगम"):
        tokens = tokens[:-1]
    if len(tokens) <= 1:
        # Check if single token has a compound caste surname attached without space
        if tokens and split_compound_surname(tokens[0]):
            return False
        return True
    return False


def is_compound_sen_name(name_str: Optional[str]) -> bool:
    """
    Checks if 'सेन' in name_str is part of a compound Sanskrit/Hindi given name
    (like 'भीम सेन', 'चन्द्र सेन', 'इन्द्र सेन') rather than the 'सैन/सेन' caste.
    """
    if not name_str:
        return False
    clean = re.sub(r'[^\w\s\u0900-\u097F]', ' ', str(name_str))
    tokens = clean.split()
    if len(tokens) >= 2:
        for i in range(len(tokens) - 1):
            if tokens[i] in COMPOUND_FIRST_NAMES_SEN and tokens[i+1] in ("सेन", "सैन"):
                return True
    return False


def normalize_devanagari_nukta(text: Optional[str]) -> str:
    """
    Normalizes Devanagari characters with Nukta (\u093c) to their base form:
    e.g. 'वाष्रणेय़' -> 'वाष्रणेय', 'अग्रवाल़' -> 'अग्रवाल', 'उपाध्याय़' -> 'उपाध्याय'.
    Also strips zero-width joiners (\u200c, \u200d) and BOM.
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


def extract_surname(name_str: Optional[str]) -> str:
    """
    Extracts the clean last name/surname from a full Devanagari or Latin name string.
    Strips punctuation, symbols, invisible zero-width joiners, and nuktas.
    Handles trailing honorifics & suffixes like 'जी', 'साहब', 'देवी', 'कुमारी' by taking the preceding word.
    Handles OCR misprints (e.g. 'वाष्रणेय' -> 'वार्ष्णेय').
    Handles concatenated names without spaces (e.g. 'सुनीलवार्ष्णेय' -> 'वार्ष्णेय').
    """
    if not name_str:
        return ""
        
    # Normalize nuktas and remove zero-width characters and BOM
    clean = normalize_devanagari_nukta(name_str)
    
    # Replace punctuation, slashes, numbers, parentheses with space
    clean = re.sub(r'[^\w\s\u0900-\u097F]', ' ', clean)
    
    tokens = clean.split()
    if not tokens:
        return ""
        
    # If the last token is an honorific or generic suffix, check preceding word
    if len(tokens) > 1 and tokens[-1] in ("जी", "साहेब", "साहब", "श्री", "देवी", "कुमारी", "बेगम", "बानो", "खातून", "वेगम"):
        target_token = tokens[-2].strip()
    else:
        target_token = tokens[-1].strip()

    # Normalize nukta on target token again
    target_token = normalize_devanagari_nukta(target_token)

    # 1. Direct typo normalization (e.g. 'वाष्रणेय' -> 'वार्ष्णेय')
    if target_token in OCR_SURNAME_TYPO_MAP:
        return OCR_SURNAME_TYPO_MAP[target_token]

    # 2. Attached compound surname without space (e.g. 'सुनीलवार्ष्णेय' or 'सुनीलवाष्रणेय')
    compound_res = split_compound_surname(target_token)
    if compound_res:
        _, decompounded_sur = compound_res
        return decompounded_sur

    return target_token


def match_voter_surnames(voter_name: str, relative_name: str, target_surnames: Set[str]) -> Tuple[bool, str]:
    """
    Checks if either the voter's surname or relative's surname matches the target set.
    Returns (is_match, matched_surname).
    """
    if not target_surnames:
        return False, ""
        
    v_sur = extract_surname(voter_name)
    r_sur = extract_surname(relative_name)
    
    if not is_single_given_name(voter_name) and v_sur in target_surnames:
        return True, v_sur
    if not is_single_given_name(relative_name) and r_sur in target_surnames:
        return True, r_sur
        
    return False, ""


def detect_direct_caste(
    voter_name: str,
    relative_name: str,
    relation_type: str = "पिता",
    is_muslim_hint: bool = False
) -> Tuple[Optional[str], Optional[str]]:
    """
    Checks voter and relative identity against:
    1. Muslim Community Detection Engine.
    2. Voter and relative surnames against all CASTE_PRESETS.
       - Rule: "जाति नाम, पिता अथवा पति के अंतिम नाम (सरनेम) से पहिचानी जाए"
       - If a voter or relative has only a single given name (e.g. 'सविता', 'राजो', 'धीरेन्द्र'),
         that given name is NOT treated as a caste surname.
       - Handles OCR misprints (e.g. 'वाष्रणेय' -> 'वार्ष्णेय').
       - Handles non-spaced compound names (e.g. 'सुनीलवार्ष्णेय' -> 'वार्ष्णेय').
       - Multi-caste suffixes ('पाल', 'कुमार', 'सिंह', 'चन्द्र', 'आर्य') do NOT assign a caste in Tier 1.
       - Compound first names like 'भीम सेन', 'चन्द्र सेन' are protected against false 'सैन' match.
       - Composite first names like 'ठाकुर दास' do NOT falsely trigger 'राजपूत'.
    Returns (caste_key, matched_surname) or (None, None).
    """
    # 1. Muslim community check
    from .community_detector import identify_voter_community
    is_m, _, _ = identify_voter_community(voter_name, relative_name, relation_type)
    if is_m or is_muslim_hint:
        return "muslim", "मुस्लिम"

    v_is_single = is_single_given_name(voter_name)
    r_is_single = is_single_given_name(relative_name)

    v_sur = extract_surname(voter_name)
    r_sur = extract_surname(relative_name)

    # 2. Check voter surname (ONLY if voter has a real multi-word name with surname, not a single given name)
    if not v_is_single and v_sur and v_sur not in COMMON_GIVEN_NAMES:
        # Multi-caste neutral titles do not assign caste in Tier 1
        if v_sur in AMBIGUOUS_MULTI_CASTE_SUFFIXES or v_sur == "आर्य":
            pass
        # Check compound Sen exception
        elif v_sur in ("सेन", "सैन") and is_compound_sen_name(voter_name):
            pass
        else:
            norm_v_sur = OCR_SURNAME_TYPO_MAP.get(v_sur, v_sur)
            for key, preset in CASTE_PRESETS.items():
                if key == "muslim":
                    continue
                if norm_v_sur in preset["surnames"]:
                    return key, norm_v_sur

    # 3. Check relative surname (father / husband)
    if not r_is_single and r_sur and r_sur not in COMMON_GIVEN_NAMES:
        # Multi-caste neutral titles do not assign caste in Tier 1
        if r_sur in AMBIGUOUS_MULTI_CASTE_SUFFIXES or r_sur == "आर्य":
            pass
        # Check compound Sen exception
        elif r_sur in ("सेन", "सैन") and is_compound_sen_name(relative_name):
            pass
        else:
            norm_r_sur = OCR_SURNAME_TYPO_MAP.get(r_sur, r_sur)
            for key, preset in CASTE_PRESETS.items():
                if key == "muslim":
                    continue
                if norm_r_sur in preset["surnames"]:
                    return key, norm_r_sur

    return None, None


