"""
Local AI Phonetic & Multilingual Search Engine for UP Voter Service.
Enables instant, typo-tolerant, and script-agnostic voter searching:
- Normalizes Devanagari matras, short/long vowels, and sound-alike consonants (e.g. अरुण vs अरूण).
- Transliterates English/Hinglish to Hindi (e.g. 'arun' -> 'अरुण', 'suresh' -> 'सुरेश').
- Computes unified Phonetic Soundex keys so Latin and Devanagari variations map to identical sound keys.
- Completely offline, ultra-fast (sub-millisecond indexed SQL search), zero external dependencies.
"""

import re
import unicodedata
from typing import List, Dict, Any, Tuple, Optional
import sqlite3

# ==============================================================================
# 1. Devanagari Normalization (देवनागरी समानीकरण)
# ==============================================================================

# Vowels & Matras (ह्रस्व vs दीर्घ भेद समानीकरण)
DEVA_MATRA_MAP = {
    '\u0942': '\u0941',  # ू (Badi u) -> ु (Chhoti u)
    '\u093f': '\u0940',  # ि (Chhoti i) -> ी (Badi i)
    '\u0948': '\u0947',  # ै (ai) -> े (e)
    '\u094c': '\u094b',  # ौ (au) -> ो (o)
    '\u090a': '\u0909',  # ऊ -> उ
    '\u0907': '\u0908',  # इ -> ई
    '\u0910': '\u090f',  # ऐ -> ए
    '\u0914': '\u0913',  # औ -> ओ
    '\u0903': '',        # Visarga -> remove
    '\u0901': '\u0902',  # Chandrabindu -> Anusvara
}

# Phonetically interchangeable consonants in Hindi election rolls
DEVA_CONSONANT_MAP = {
    '\u0923': '\u0928',  # ण -> न (अरुण -> अरुन)
    '\u0936': '\u0938',  # श -> स (सुरेश -> सुरेस)
    '\u0937': '\u0938',  # ष -> स (संतोष -> सन्तोस)
    '\u092c': '\u0935',  # ब -> व (बिकास -> विकास, बिजय -> विजय)
    '\u0958': '\u0915',  # क़ -> क
    '\u0959': '\u0916',  # ख़ -> ख
    '\u095a': '\u0917',  # ग़ -> ग
    '\u095b': '\u091c',  # ज़ -> ज
    '\u095c': '\u0921',  # ड़ -> ड
    '\u095d': '\u0922',  # ढ़ -> ढ
    '\u095e': '\u092b',  # फ़ -> फ
    '\u0931': '\u0930',  # ऱ -> र
}


def normalize_devanagari(text: str) -> str:
    """
    Normalizes Devanagari text for spelling tolerance:
    - Treats 'रु' and 'रू' as identical ('अरुण' == 'अरूण').
    - Treats 'ण' and 'न' as identical ('किरण' == 'किरन').
    - Treats 'श', 'ष', and 'स' as identical ('सुरेश' == 'सुरेस').
    - Treats 'व' and 'ब' as identical ('विकास' == 'बिकास').
    - Removes nuktas, zero-width characters, and excess spaces.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text).strip())
    # Remove nukta (U+093C)
    text = text.replace('\u093c', '')
    # Remove zero-width joiners/non-joiners
    text = text.replace('\u200c', '').replace('\u200d', '').replace('\ufeff', '')

    chars = []
    for ch in text:
        if ch in DEVA_MATRA_MAP:
            chars.append(DEVA_MATRA_MAP[ch])
        elif ch in DEVA_CONSONANT_MAP:
            chars.append(DEVA_CONSONANT_MAP[ch])
        else:
            chars.append(ch)
            
    result = "".join(chars)
    # Common conjunct variations
    result = result.replace('ज्ञ', 'ग्य').replace('क्ष', 'छ').replace('ऋ', 'रि')
    # Collapse spaces
    return re.sub(r'\s+', ' ', result).strip()


# ==============================================================================
# 2. English / Hinglish to Hindi Transliteration (अंग्रेजी से हिन्दी रूपांतरण)
# ==============================================================================

# High-frequency Indian electoral names vocabulary
COMMON_NAME_DICTIONARY = {
    # First Names
    'arun': ['अरुण', 'अरूण', 'अरुन'],
    'suresh': ['सुरेश', 'सुरेस'],
    'ramesh': ['रमेश', 'रमेस'],
    'rajesh': ['राजेश', 'राजेस'],
    'dinesh': ['दिनेश', 'दीनेश', 'दिनेस'],
    'mukesh': ['मुकेश', 'मुकेस'],
    'sunil': ['सुनील', 'सुनिल'],
    'anil': ['अनिल', 'अनील'],
    'amit': ['अमित', 'अमीत'],
    'sumit': ['सुमित', 'सुमीत'],
    'deepak': ['दीपक', 'दिपक'],
    'dipak': ['दीपक', 'दिपक'],
    'rahul': ['राहुल'],
    'rohit': ['रोहित'],
    'mohit': ['मोहित'],
    'pooja': ['पूजा', 'पुजा'],
    'puja': ['पूजा', 'पुजा'],
    'neetu': ['नीतू', 'नीतु'],
    'nitu': ['नीतू', 'नीतु'],
    'pushpendra': ['पुष्पेंद्र', 'पुष्पेन्द्र'],
    'pushpender': ['पुष्पेंद्र', 'पुष्पेन्द्र'],
    'vikas': ['विकास', 'बिकास'],
    'bikas': ['बिकास', 'विकास'],
    'bikash': ['विकास', 'बिकास'],
    'vijay': ['विजय', 'बिजय'],
    'bijay': ['बिजय', 'विजय'],
    'vinod': ['विनोद', 'बिनोद'],
    'binod': ['बिनोद', 'विनोद'],
    'santosh': ['संतोष', 'सन्तोष'],
    'satyendra': ['सत्येन्द्र', 'सत्येंद्र', 'सतेन्द्र'],
    'satyender': ['सत्येन्द्र', 'सत्येंद्र'],
    'ravindra': ['रविन्द्र', 'रविंद्र'],
    'ravinder': ['रविन्द्र', 'रविंद्र'],
    'arvind': ['अरविन्द', 'अरविंद'],
    'arbind': ['अरविन्द', 'अरविंद'],
    'ajay': ['अजय'],
    'manoj': ['मनोज'],
    'pramod': ['प्रमोद'],
    'ashok': ['अशोक'],
    'kamlesh': ['कमलेश'],
    'rajendra': ['राजेंद्र', 'राजेन्द्र'],
    'surendra': ['सुरेंद्र', 'सुरेन्द्र'],
    'mahendra': ['महेंद्र', 'महेन्द्र'],
    'virendra': ['वीरेंद्र', 'वीरेन्द्र', 'बीरेन्द्र'],
    'dharmendra': ['धर्मेंद्र', 'धर्मेन्द्र'],
    'jitendra': ['जितेंद्र', 'जितेन्द्र'],
    'sandeep': ['संदीप', 'सन्दीप'],
    'kuldeep': ['कुलदीप'],
    'pradeep': ['प्रदीप'],
    'pankaj': ['पंकज'],
    'neeraj': ['नीरज', 'निरज'],
    'dheeraj': ['धीरज'],
    'dhiraj': ['धीरज'],
    'suraj': ['सूरज', 'सुरज'],
    'roshan': ['रोशन'],
    'vishal': ['विशाल', 'बिशाल'],
    'vivek': ['विवेक', 'बिबेक'],
    'naveen': ['नवीन', 'नबिन'],
    'praveen': ['प्रवीण', 'प्रवीन'],
    'satish': ['सतीश'],
    'harish': ['हरीश'],
    'girish': ['गिरीश'],
    'jagdish': ['जगदीश'],
    'ram': ['राम'],
    'shyam': ['श्याम'],
    'krishna': ['कृष्णा', 'कृष्ण', 'किशन'],
    'gopal': ['गोपाल'],
    'mohan': ['मोहन'],
    'sohan': ['सोहन'],
    'rohan': ['रोहन'],
    'aarti': ['आरती', 'आरति'],
    'arti': ['आरती', 'आरति'],
    'sunita': ['सुनीता', 'सुनिता'],
    'anita': ['अनिता', 'अनीता'],
    'kavita': ['कविता'],
    'sangeeta': ['संगीता', 'संगिता'],
    'sangita': ['संगीता', 'संगिता'],
    'manju': ['मंजू', 'मन्जू'],
    'rekha': ['रेखा'],
    'seema': ['सीमा', 'सिमा'],
    'reena': ['रीना', 'रिना'],
    'meena': ['मीना', 'मिना'],
    'pinki': ['पिंकी'],
    'pinky': ['पिंकी'],
    'mamta': ['ममता'],
    'usha': ['उषा'],
    'asha': ['आशा'],
    'geeta': ['गीता', 'गिता'],
    'babita': ['बबिता'],
    'shakuntala': ['शकुन्तला', 'शकुंतला', 'सकुन्तला'],
    'kamla': ['कमला'],
    'vimla': ['विमला', 'बिमला'],
    'pushpa': ['पुष्पा'],
    'sushma': ['सुषमा'],
    'rajni': ['रजनी'],
    'manikant': ['मणिकांत', 'मणिकान्त'],
    'kallu': ['कल्लू', 'कल्लु'],
    'aman': ['अमन'],
    'ankush': ['अंकुश'],
    'sanket': ['संकेत'],
    'bhanu': ['भानु'],
    'radha': ['राधा'],
    'rupa': ['रूपा', 'रूपावती', 'रूपवती'],
    'rupwati': ['रूपवती'],
    'renu': ['रेनू', 'रेनु'],

    # Surnames & Relations
    'sharma': ['शर्मा'],
    'verma': ['वर्मा'],
    'varma': ['वर्मा'],
    'singh': ['सिंह'],
    'kumar': ['कुमार'],
    'yadav': ['यादव'],
    'devi': ['देवी'],
    'gupta': ['गुप्ता', 'गुप्त'],
    'gupt': ['गुप्त', 'गुप्ता'],
    'chaudhary': ['चौधरी'],
    'choudhary': ['चौधरी'],
    'choudhari': ['चौधरी'],
    'pal': ['पाल'],
    'rani': ['रानी'],
    'kumari': ['कुमारी'],
    'prasad': ['प्रसाद'],
    'prakash': ['प्रकाश'],
    'shankar': ['शंकर'],
    'mishra': ['मिश्रा', 'मिश्र'],
    'tiwari': ['तिवारी'],
    'pandey': ['पांडेय', 'पाण्डेय'],
    'agrawal': ['अग्रवाल'],
    'agarwal': ['अग्रवाल'],
    'varshney': ['वार्ष्णेय'],
    'saini': ['सैनी'],
    'kashyap': ['कश्यप'],
    'maurya': ['मौर्य'],
    'jaiswal': ['जायसवाल'],
    'tyagi': ['त्यागी'],
    'thakur': ['ठाकुर'],
    'chauhan': ['चौहान'],
    'lodhi': ['लोधी'],
    'kushwaha': ['कुशवाहा'],
    'jatav': ['जाटव'],
    'khan': ['खान'],
    'ansari': ['अंसारी'],
    'ali': ['अली'],
    'ahmad': ['अहमद'],
    'ahmed': ['अहमद']
}

HINDI_CONSONANT_RULES: List[Tuple[str, str]] = [
    ('ksh', 'क्ष'), ('ksha', 'क्ष'),
    ('gya', 'ज्ञ'), ('gy', 'ज्ञ'),
    ('sh', 'श'), ('sha', 'श'),
    ('ch', 'च'), ('cha', 'च'),
    ('th', 'थ'), ('tha', 'थ'),
    ('dh', 'ध'), ('dha', 'ध'),
    ('bh', 'भ'), ('bha', 'भ'),
    ('ph', 'फ'), ('pha', 'फ'),
    ('kh', 'ख'), ('kha', 'ख'),
    ('gh', 'घ'), ('gha', 'घ'),
    ('jh', 'झ'), ('jha', 'झ'),
    ('tr', 'त्र'), ('tra', 'त्र'),
    ('k', 'क'), ('g', 'ग'),
    ('c', 'क'), ('j', 'ज'),
    ('t', 'त'), ('d', 'द'),
    ('n', 'न'), ('p', 'प'),
    ('b', 'ब'), ('m', 'म'),
    ('y', 'य'), ('r', 'र'),
    ('l', 'ल'), ('v', 'व'),
    ('w', 'व'), ('s', 'स'),
    ('h', 'ह'), ('z', 'ज'),
    ('f', 'फ')
]

VOWEL_MAP = {
    'aa': 'ा', 'a': '', 'ee': 'ी', 'i': 'ि',
    'oo': 'ू', 'u': 'ु', 'e': 'े', 'ai': 'ै',
    'o': 'ो', 'au': 'ौ'
}

INITIAL_VOWEL_MAP = {
    'aa': 'आ', 'a': 'अ', 'ee': 'ई', 'i': 'इ',
    'oo': 'ऊ', 'u': 'उ', 'e': 'ए', 'ai': 'ऐ',
    'o': 'ओ', 'au': 'औ'
}


def transliterate_word(word: str) -> List[str]:
    """Transliterates a single Latin word to Devanagari candidates."""
    w = word.lower().strip()
    if not w or not re.match(r'^[a-z]+$', w):
        return [word] if word else []

    candidates = []
    if w in COMMON_NAME_DICTIONARY:
        candidates.extend(COMMON_NAME_DICTIONARY[w])

    # Algorithmic phonetic parsing
    i = 0
    res = []
    n = len(w)

    # Initial vowel
    for vl in ['aa', 'ai', 'au', 'ee', 'oo', 'a', 'i', 'u', 'e', 'o']:
        if w.startswith(vl):
            res.append(INITIAL_VOWEL_MAP.get(vl, 'अ'))
            i += len(vl)
            break

    while i < n:
        matched_c = None
        c_len = 0
        for pat, dev in HINDI_CONSONANT_RULES:
            if w[i:].startswith(pat):
                matched_c = dev
                c_len = len(pat)
                break

        if matched_c:
            i += c_len
            matched_v = None
            v_len = 0
            for vpat in ['aa', 'ai', 'au', 'ee', 'oo', 'a', 'i', 'u', 'e', 'o']:
                if w[i:].startswith(vpat):
                    matched_v = VOWEL_MAP.get(vpat, '')
                    v_len = len(vpat)
                    break
            if matched_v is not None:
                res.append(matched_c + matched_v)
                i += v_len
            else:
                res.append(matched_c)
        else:
            i += 1

    algo_word = "".join(res)
    if algo_word and algo_word not in candidates:
        candidates.append(algo_word)
        if algo_word.endswith('न'):
            candidates.append(algo_word[:-1] + 'ण')
        elif algo_word.endswith('ण'):
            candidates.append(algo_word[:-1] + 'न')

    return candidates


def transliterate_latin_to_hindi(text: str) -> List[str]:
    """
    Transliterates single or multi-word English/Hinglish query to Hindi.
    E.g. 'arun' -> ['अरुण', 'अरूण', 'अरुन']
         'arun kumar' -> ['अरुण कुमार', 'अरूण कुमार', 'अरुन कुमार']
    """
    if not text:
        return []
    words = text.strip().split()
    if not words:
        return []

    if len(words) == 1:
        return transliterate_word(words[0])

    # Multi-word cartesian product for top variations
    word_options = [transliterate_word(w) for w in words]
    # Take first 2 variations per word to keep query fast
    combined = []
    import itertools
    for prod in itertools.product(*[opts[:2] for opts in word_options if opts]):
        combined.append(" ".join(prod))

    return combined[:5]


# ==============================================================================
# 3. Unified Phonetic Soundex (समान ध्वनि कोड)
# ==============================================================================

DEV_TO_LATIN = {
    'क': 'k', 'ख': 'k', 'ग': 'g', 'घ': 'g', 'ङ': 'n',
    'च': 'ch', 'छ': 'ch', 'ज': 'j', 'झ': 'j', 'ञ': 'n',
    'ट': 't', 'ठ': 't', 'ड': 'd', 'ढ': 'd', 'ण': 'n',
    'त': 't', 'थ': 't', 'द': 'd', 'ध': 'd', 'न': 'n',
    'प': 'p', 'फ': 'p', 'ब': 'b', 'भ': 'b', 'म': 'm',
    'य': 'y', 'र': 'r', 'ल': 'l', 'व': 'v',
    'श': 's', 'ष': 's', 'स': 's', 'ह': 'h',
    'क्ष': 'ksh', 'त्र': 'tr', 'ज्ञ': 'gy',
    'अ': 'a', 'आ': 'a', 'इ': 'i', 'ई': 'i', 'उ': 'u', 'ऊ': 'u',
    'ए': 'e', 'ऐ': 'e', 'ओ': 'o', 'औ': 'o', 'ऋ': 'ri',
    'ा': 'a', 'ि': 'i', 'ी': 'i', 'ु': 'u', 'ू': 'u',
    'े': 'e', 'ै': 'e', 'ो': 'o', 'ौ': 'o', 'ं': 'n', 'ँ': 'n'
}


def get_phonetic_key(text: str) -> str:
    """
    Generates a unified phonetic fingerprint.
    Guarantees:
      get_phonetic_key('अरुण') == get_phonetic_key('अरूण') == get_phonetic_key('arun') == 'ARN'
      get_phonetic_key('सुरेश') == get_phonetic_key('सुरेस') == get_phonetic_key('suresh') == 'SRS'
      get_phonetic_key('दीपक') == get_phonetic_key('dipak') == get_phonetic_key('deepak') == 'DBK'
    """
    if not text:
        return ""

    t = str(text).lower().strip()
    latin_chars = []
    for ch in t:
        if ch in DEV_TO_LATIN:
            latin_chars.append(DEV_TO_LATIN[ch])
        elif 'a' <= ch <= 'z' or ch.isspace():
            latin_chars.append(ch)

    lat = "".join(latin_chars)
    words = lat.split()
    word_keys = []

    for w in words:
        if not w:
            continue
        w = re.sub(r'ee', 'i', w)
        w = re.sub(r'oo', 'u', w)
        w = re.sub(r'aa', 'a', w)
        w = re.sub(r'sh', 's', w)
        w = re.sub(r'th', 't', w)
        w = re.sub(r'dh', 'd', w)
        w = re.sub(r'bh', 'b', w)
        w = re.sub(r'ph', 'p', w)
        w = re.sub(r'kh', 'k', w)
        w = re.sub(r'gh', 'h', w)  # Map singh -> snh
        w = re.sub(r'ch', 'c', w)

        k_chars = []
        for i, ch in enumerate(w):
            if ch in 'aeiouy':
                if i == 0:
                    k_chars.append('A')
                else:
                    k_chars.append('V')
            elif ch in 'sz':
                k_chars.append('S')
            elif ch in 'bvwp':
                k_chars.append('B')
            elif ch in 'kcqg':
                k_chars.append('K')
            elif ch in 'td':
                k_chars.append('D')
            elif ch in 'nm':
                k_chars.append('N')
            elif ch in 'r':
                k_chars.append('R')
            elif ch in 'l':
                k_chars.append('L')
            elif ch in 'h':
                k_chars.append('H')
            elif ch in 'j':
                k_chars.append('J')

        collapsed = []
        prev = None
        for c in k_chars:
            if c != prev:
                collapsed.append(c)
                prev = c

        filtered = [collapsed[0]] if collapsed else []
        for c in collapsed[1:]:
            if c != 'V':
                filtered.append(c)

        word_keys.append("".join(filtered))

    return " ".join(word_keys)


# ==============================================================================
# 4. Search Query Expansion (क्वेरी विस्तार)
# ==============================================================================

def expand_search_query(raw_query: str) -> Dict[str, Any]:
    """
    Analyzes raw search query and prepares multi-pronged matching terms:
    - original exact terms
    - transliterated candidates (if English/Hinglish)
    - normalized Devanagari terms
    - phonetic keys
    """
    if not raw_query or not raw_query.strip():
        return {
            "raw": "",
            "is_latin": False,
            "exact_variants": [],
            "normalized_variants": [],
            "phonetic_key": ""
        }

    q = raw_query.strip()
    is_latin = bool(re.search(r'[a-zA-Z]', q))
    exact_variants = [q]
    norm_variants = []

    if is_latin:
        translit_list = transliterate_latin_to_hindi(q)
        for t in translit_list:
            if t not in exact_variants:
                exact_variants.append(t)
            norm = normalize_devanagari(t)
            if norm and norm not in norm_variants:
                norm_variants.append(norm)
    else:
        norm = normalize_devanagari(q)
        if norm and norm != q:
            norm_variants.append(norm)

    phonetic_key = get_phonetic_key(q)

    return {
        "raw": q,
        "is_latin": is_latin,
        "exact_variants": exact_variants,
        "normalized_variants": norm_variants,
        "phonetic_key": phonetic_key
    }


# ==============================================================================
# 5. Database Schema Migration & Maintenance
# ==============================================================================

def ensure_ai_search_columns(conn: sqlite3.Connection):
    """
    Ensures that voters table has normalized & phonetic columns and indexes.
    Populates them automatically for all existing records.
    """
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(voters);")
    existing_cols = {r[1] for r in cursor.fetchall()}

    columns_to_add = [
        ("name_normalized", "TEXT"),
        ("name_phonetic", "TEXT"),
        ("rel_normalized", "TEXT"),
        ("rel_phonetic", "TEXT")
    ]

    for col_name, col_type in columns_to_add:
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE voters ADD COLUMN {col_name} {col_type};")

    # Create indexes for sub-millisecond lookups
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_voters_name_norm ON voters(name_normalized);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_voters_name_phon ON voters(name_phonetic);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_voters_rel_norm ON voters(rel_normalized);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_voters_rel_phon ON voters(rel_phonetic);")

    # Check if any row needs computation
    cursor.execute("SELECT COUNT(*) FROM voters WHERE name_normalized IS NULL OR name_phonetic IS NULL;")
    unprocessed_count = cursor.fetchone()[0]

    if unprocessed_count > 0:
        cursor.execute("SELECT id, name, relation_name FROM voters WHERE name_normalized IS NULL OR name_phonetic IS NULL;")
        rows = cursor.fetchall()
        updates = []
        for vid, name, rel_name in rows:
            name_norm = normalize_devanagari(name)
            name_phon = get_phonetic_key(name)
            rel_norm = normalize_devanagari(rel_name or "")
            rel_phon = get_phonetic_key(rel_name or "")
            updates.append((name_norm, name_phon, rel_norm, rel_phon, vid))

        cursor.executemany("""
            UPDATE voters 
            SET name_normalized = ?, name_phonetic = ?, rel_normalized = ?, rel_phonetic = ?
            WHERE id = ?;
        """, updates)
        conn.commit()
