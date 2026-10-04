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
from PIL import Image
from ..models.voter import VoterRecord, PageProcessingResult
from .validator import clean_house_no, clean_hindi_text, normalize_relation_type
from .ocr_extractor import OCRExtractor


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
    def clean_compound_hindi_name(cls, name: str) -> str:
        """
        Intelligently separates glued suffixes like सिंह, देवी, कुमार, कुमारी, प्रसाद, आदि
        while preserving genuine single-word names.
        """
        if not name:
            return ""
        name = clean_hindi_text(name)
        suffixes = ['सिंह', 'देवी', 'कुमार', 'कुमारी', 'प्रसाद', 'शर्मा', 'वर्मा', 'गुप्ता', 'यादव', 'चन्द', 'चंद', 'कौर', 'प्रकाश', 'लाल']
        for suf in suffixes:
            name = re.sub(rf'([\u0900-\u097F]{{2,}})({suf})(?=$|\s)', r'\1 \2', name)
        return re.sub(r'\s+', ' ', name).strip()

    @classmethod
    def decode_sec_up_font(cls, text: str) -> str:
        """
        Decodes State Election Commission (SEC) UP / Nagar Nikay font subsets (Identity-H / Mangal / TTF).
        Restores full conjuncts (युक्ताक्षर), vowels, reph, and characters across all font subsets.
        """
        if not text:
            return ""
        # 1. Pre-convert legacy \xa0 to 'स' before any Python stripping or word processing
        s = text.replace('\xa0', 'स')
        
        # 2. Pre-process specific SEC UP ligatures containing ¡ and special tokens
        s = s.replace('Ȳ¡', 'सिंह')
        s = s.replace('ह¡', 'हि')
        s = s.replace('¡\x9aोज', 'सरोज')
        s = s.replace('¡रोज', 'सरोज')
        s = s.replace('म¡Ʌġ', 'महेंद्र')
        s = s.replace('म¡Ʌ', 'महेंद्र')
        s = s.replace('जा\x92ा ¡', 'जाधा सिंह')
        s = s.replace('जाधा ह', 'जाधा सिंह')
        s = s.replace('फतीह ह', 'फतीह सिंह')
        s = s.replace('राम ह', 'राम सिंह')
        s = s.replace('\x93×\x90Ǘ', 'नत्थू')
        s = s.replace('×\x90', 'त्थ')
        
        # 3. Pre-convert ¡ (0xa1) to 'ह' (fixes harsh -> harsh instead of aarsh)
        s = s.replace('¡', 'ह')

        # 4. Critical SEC UP font glyph fixes for complex names
        s = s.replace('ȣपक', 'दीपक')
        s = s.replace('ȣ', 'दी')
        s = s.replace('वĤ', 'प्र')
        s = s.replace('Ĥ', 'प्र')
        s = s.replace('žु', 'शु')
        s = s.replace('ž', 'श')
        s = s.replace('मã›ू', 'मल्लू')
        s = s.replace('ã›', 'ल्लू')
        s = s.replace('ã', 'ल्ल')
        s = s.replace('›', 'ल')
        s = s.replace(' ाçणȶय', 'वार्ष्णेय')
        s = s.replace('ाçणȶय', 'वार्ष्णेय')
        s = s.replace('çणȶय', 'ष्णेय')
        s = s.replace('जियपाल', 'जयपाल')
        s = s.replace('्ेवी', 'देवी')
        s = s.replace('वपता', 'पिता')
        s = s.replace('पतत', 'पति')
        s = s.replace('स›ंग', 'लिंग')
        s = s.replace('Đ०िं०', 'क्र०सं०')

        # Direct phrase-level & compound name mappings first
        phrases = [
            ("कपुमारी", "कुमारी"),
            ("कपुमार", "कुमार"),
            ("कपुन्दती", "कुन्ती"),
            ("कपुन्ती", "कुन्ती"),
            ("मपुन्दनी", "मुन्नी"),
            ("मपुन्नी", "मुन्नी"),
            ("हहमांशपु", "हिमांशु"),
            ("हहमांशु", "हिमांशु"),
            ("हिमांशपु", "हिमांशु"),
            ("बाबपु", "बाबू"),
            ("पपु", "पु"),
            ("पप्तपू", "पप्पू"),
            ("वीरन्द्रदर", "वीरेंद्र"),
            ("महन्द्रन्द्रंह", "महेंद्र सिंह"),
            ("महन्द्रन्द्र", "महेंद्र"),
            ("महेंद्र ह", "महेंद्र सिंह"),
            ("जाधा ह", "जाधा सिंह"),
            ("फतीह ह", "फतीह सिंह"),
            ("राम ह", "राम सिंह"),
            ("नजथूराम", "नत्थूराम"),
            ("नजथू", "नत्थू"),
            ("शकर लाल", "शंकर लाल"),
            ("शकरलाल", "शंकर लाल"),
            ("शकर", "शंकर"),
            ("कष्ण", "कृष्ण"),
            ("गुप्दा", "गुप्ता"),
            ("दाप", "दीप"),
            ("बटी", "बंटी"),
            ("कक न", "किशन"),
            ("रामननवा", "रामनवल"),
            ("राजेशरी", "राजेश्वरी"),
            ("नरायन", "नारायण"),
            ("हरोज", "सहरोज"),
            ("सिह", "सिंह"),
            ("ईæवि द™ाल", "ईश्वर दयाल"),
            ("ईæवि द\x99ाल", "ईश्वर दयाल"),
            ("ईæवि", "ईश्वर"),
            ("æयȫ", "श्यो"),
            ("æय", "श्य"),
            ("æव", "श्व"),
            ("द\x99ाल", "दयाल"),
            ("द™ाल", "दयाल"),
            ("क ृुमािȣ", "कुमारी"),
            ("क ृुमािी", "कुमारी"),
            ("क ृुमाि", "कुमार"),
            ("कुमािȣ", "कुमारी"),
            ("कुमािी", "कुमारी"),
            ("कुमाि", "कुमार"),
            ("ससंह", "सिंह"),
            ("ससॊह", "सिंह"),
            ("सॊह", "सिंह"),
            ("Ĥवेश", "प्रवेश"),
            ("Ĥहलाद", "प्रहलाद"),
            ("Ĥमोद", "प्रमोद"),
            ("Ĥदीप", "प्रदीप"),
            ("Ĥकाश", "प्रकाश"),
            ("Ĥ", "प्र"),
            ("क ृष्ण", "कृष्ण"),
            ("क ृष", "कृष्ण"),
            ("क ृ", "कृ"),
            ("क ु", "कु"),
            ("क ू", "कू"),
            ("गेÛदा", "गेंदा"),
            ("गेन्ददा", "गेंदा"),
            ("Ûदा", "न्दा"),
            ("Ûद", "न्द"),
            ("Ûहे", "न्हे"),
            ("Ûने", "न्ने"),
            ("Ûना", "न्ना"),
            ("Ûनी", "न्नी"),
            ("Ûġ", "न्द्र"),
            ("शिदा", "शारदा"),
            ("हिपाल", "हरिपाल"),
            ("सिोज", "सरोज"),
            ("पु्पा", "पुष्पा"),
            ("समथलेश", "मिथिलेश"),
            ("वÛ“े", "बन्ने"),
            ("“Ûहे", "नन्हे"),
            ("हस“Ȱ“", "हुसैन"),
            ("अलȣ", "अली"),
            ("साजज™ा", "साजिया"),
            ("“ाजज™ा", "नाज़िया"),
            ("“ाजजश", "साजिद"),
            ("आसशक", "आशिक"),
            ("िासशद", "राशिद"),
            ("िाससद", "राशिद"),
            ("मुहàमद", "मोहम्मद"),
            ("मोहàमद", "मोहम्मद"),
            ("मौहàमद", "मोहम्मद"),
            ("मो¡àम", "मोहम्मद"),
            ("िाजेश", "राजेश"),
            ("िाकेश", "राकेश"),
            ("िाहुल", "राहुल"),
            ("िाजीव", "राजीव"),
            ("िाम", "राम"),
            ("िा", "रा"),
            ("िे", "रे"),
            ("िै", "रै"),
            ("िो", "रो"),
            ("िौ", "रौ"),
            ("िू", "रू"),
            ("िु", "रु"),
            ("बबराा", "बबराला"),
            ("बबिाला", "बबराला"),
            ("शमार्", "शर्मा"),
            ("वमार्", "वर्मा"),
            ("वा्ैय", "वार्ष्णेय"),
            ("वा्णैय", "वार्ष्णेय"),
            ("वाष्णैय", "वार्ष्णेय"),
            ("स्मनेा", "स्नेहा"),
            ("चं्र", "चन्द्र"),
            ("ोानपाल", "भानपाल"),
            ("ोान", "भान"),
            ("गुप्तता", "गुप्ता"),
            ("प्रीित", "प्रीति"),
            ("दुगैश", "दुर्गेश"),
            ("सूिज", "सूरज"),
            ("राकृेश", "राकेश"),
            ("jमेन्द्र", "रामेन्द्र"),
            ("jमपाल", "रामपाल"),
            ("jम", "राम"),
        ]
        for o, n in phrases:
            s = s.replace(o, n)

        # Single Character & Glyph Code Map
        char_map = {
            '\x93': 'न',
            '\x94': 'प',
            '\x9b': 'ल',
            '\x8f': 'त',
            '\x91': 'द',
            '\x99': 'य',
            '\x92': 'ध',
            '\x98': 'म',
            '\x8e': 'ण',
            '\x8c': 'ड',
            '\x80': 'क',
            '\x90': 'थ',
            '\x9c': 'ह',
            '\x9d': 'व',
            '\x9f': 'ष',
            '\xa0': 'स',
            '\xa1': 'ह',
            '¡': 'ह',
            '\x9e': 'श',
            '\x9a': 'र',
            '\xcf': 'ज़',
            '\xdb': 'न्द',
            'Û': 'न्द',
            'Ü': 'प्त',
            '“': 'न',
            '”': 'न',
            '™': 'य',
            'æ': 'श',
            'à': 'म्म',
            'É': 'ख्य',
            '¢': 'क्ष',
            '£': 'ज्ञ',
            'È': 'क्ख',
            'Ê': 'ज्ञ',
            'Í': 'च्छ',
            'Ï': 'ज्',
            '×': 'ज',
            'Ø': 'थ्वी',
            'Ú': 'ध्या',
            'Þ': 'ब्बी',
            'ã': 'ल्ल',
            'å': 'थि',
            'ç': 'ष्',
            'è': 'स्त',
            'é': 'हे',
            'ê': 'क्ष्मी',
            'Đ': 'क्र',
            'Ē': 'ग्र',
            'Ě': 'ट्री',
            'ğ': 'त्र',
            'ġ': 'न्द्र',
            'Ģ': 'ध्रु',
            'ģ': 'श्री',
            'Ĥ': 'प्र',
            'Ħ': 'बृ',
            'ħ': 'श्रे',
            'Į': 'श्री',
            'Ř': 'रु',
            'Ŭ': 'प्र',
            'ŷ': 'र्ष',
            'ƣ': 'त्त',
            'ǒ': 'ि',
            'Ǔ': 'ि',
            'ǔ': 'ि',
            'Ǖ': 'पु',
            'Ǘ': 'ू',
            'ǽ': 'रु',
            'Ǿ': 'रू',
            'Ȣ': 'ी',
            'ȣ': 'ी',
            'Ȱ': 'ै',
            'ȶ': 'े',
            'ȸ': 'ी',
            'ȡ': 'ा',
            'ȯ': 'े',
            'Ȫ': 'ो',
            'ȫ': 'ौ',
            'Ȳ': 'ं',
            'ȧ': 'ी',
            'ǐ': 'ि',
            'Ǒ': 'ि',
            'Ǚ': 'ृ',
            'Ö': 'ण्ड',
            'Ŷ': 'र्ष्',
            'ɋ': 'ौ',
            'Ⱦ': 'ेश',
            'ɾ': 'द्र',
            '°': '०',
            'Ʌ': 'न्द्र',
            'ɉ': 'ो',
            'ɟ': 'ो',
            'ɪ': 'ट्ट',
            'ɫ': 'ठ्ठ',
            'ɬ': 'ड्ड',
            'ɮ': 'द्दी',
            'ʜ': 'क',
            'ͧ': 'ि',
            'ͨ': '',
            'ͪ': '',
            'ͬ': 'ी',
            '›': 'ल',
            'š': 'श',
            'ž': 'श',
            'Ž': 'श',
            'Œ': 'छ',
            '’': 'अ',
            '\\': 'अ',
            ']': 'आ',
        }
        s = "".join(char_map.get(c, c) for c in s)

        # Word-ending or post-vowel 'ि' -> 'र' (e.g. कुमाि -> कुमार, रामवीि -> रामवीर, नजीि -> नजीर)
        s = re.sub(r'([ाीूेैोौ])ि(?=\s|$|[^\u0900-\u097F])', r'\1र', s)
        s = re.sub(r'([ाीूेैोौ])ि([ीेै])', r'\1र\2', s)

        # Reph [ (e.g. शमा[ -> शर्मा, वषा[ -> वर्षा, वाड[ -> वार्ड)
        s = re.sub(r'\s+\[', '[', s)
        s = re.sub(r'([क-ह])([ा-ू]?)(?:\[)', r'र्\1\2', s)
        s = re.sub(r'\[', 'र्', s)

        # Reorder prefix 'ि' before a single consonant or conjunct: e.g. िन -> नि, िम -> मि, ित -> ति
        s = re.sub(r'ि([क-ह](?:्[क-ह])*)', r'\1ि', s)

        # Common Hindi name corrections from SEC UP font anomalies
        s = re.sub(r'\bवजय\b', 'विजय', s)

        # Repair leading floating matras where 'स' (rendered as \xa0 whitespace or omitted before matras) was dropped
        s = re.sub(r'(?<![\u0900-\u097F])([ािीुूृेैोौ])', r'स\1', s)

        # Cleanup stray spaces before matras and double matras
        s = re.sub(r'([क-ह])\s+([ािीुूेैोौंः्ृ])', r'\1\2', s)
        s = re.sub(r'ा+', 'ा', s)
        s = re.sub(r'ी+', 'ी', s)
        s = re.sub(r'ु+', 'ु', s)
        s = re.sub(r'ू+', 'ू', s)

        # SEC UP Font doubled consonant / duplicate base repairs (where pre-matra glyph mapped to base letter)
        s = re.sub(r'\bदद', 'दि', s)
        s = re.sub(r'\bवव', 'वि', s)
        s = re.sub(r'\bकक(?=[शपम])', 'कि', s)
        s = re.sub(r'\bगग(?=[र])', 'गि', s)
        s = re.sub(r'\bखख(?=[ल])', 'खि', s)
        s = re.sub(r'\bअनन(?=[लत])', 'अनि', s)
        s = re.sub(r'\bसुनन(?=[त])', 'सुनी', s)
        s = re.sub(r'ददन([ेै]?)\s*श', 'दिनेश', s)
        s = re.sub(r'ददन([ेै]?)ष', 'दिनेश', s)
        s = re.sub(r'मुक\s*क(?=[ेै])', 'मुक', s)
        s = re.sub(r'ससह', 'सिंह', s)
        s = re.sub(r'सस(?=[ा-ौ])', 'सिं', s)
        s = re.sub(r'सस\b', 'सिंह', s)
        s = re.sub(r'क\s+क(?=[श])', 'कि', s)
        s = re.sub(r'ररशी', 'ऋषि', s)
        s = re.sub(r'हररओम', 'हरिओम', s)
        s = re.sub(r'हरर', 'हरि', s)

        # Targeted Devanagari ligatures and font artifacts
        s = re.sub(r'वा[्षर्ण\s]{1,4}ैय', 'वार्ष्णेय', s)
        s = re.sub(r'स्मने[ा|]', 'स्नेहा', s)
        s = re.sub(r'चं[्र|]+', 'चन्द्र', s)
        s = re.sub(r'ोान(?=[पव])', 'भान', s)
        s = re.sub(r'\bनवी\s+जान\b', 'नबी जान', s)
        s = re.sub(r'गुप्त+ता', 'गुप्ता', s)
        s = re.sub(r'प्री[ित]{2,3}', 'प्रीति', s)
        s = re.sub(r'परर[ित]{2,3}', 'प्रीति', s)
        s = re.sub(r'(?<![क-ह])ौरभ', 'सौरभ', s)
        s = re.sub(r'(?<![क-ह])ोरभ', 'सौरभ', s)
        s = re.sub(r'शौरभ', 'सौरभ', s)
        s = re.sub(r'(?<![क-ह])गोरव', 'गौरव', s)
        s = re.sub(r'मोह?म्म?म?दद?', 'मोहम्मद', s)
        s = re.sub(r'मौाम्म?म?द', 'मोहम्मद', s)
        s = re.sub(r'ुशवपरी', 'शिवपुरी', s)
        s = re.sub(r'िशवपुरी', 'शिवपुरी', s)

        # Clean split spaces in names
        s = re.sub(r'\bमो\s+नी\b', 'मोनी', s)
        s = re.sub(r'\bकि\s+शश\b', 'किशश', s)

        # Remove non-Devanagari stray symbols while preserving Hindi text, numbers, spaces, colons, hyphens
        s = re.sub(r'[^\u0900-\u097Fa-zA-Z0-9\s\.\-/:,]', '', s)
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
            "body_type": "nagar_panchayat",
            "body_type_label": "नगर पंचायत",
            "district": None,
            "nikay_name": None,
            "ward": None,
            "ward_no": None,
            "ward_name": None,
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
                w_top = [w for w in words if w[1] < 125]
            elif page is not None:
                w_top = [w for w in page.get_text("words") if w[1] < 125]
            else:
                return meta

            if not w_top:
                return meta

            raw_top = " ".join([w[4] for w in sorted(w_top, key=lambda x: (x[1], x[0]))])
            dec = cls.decode_sec_up_font(raw_top)

            # Detect Body Type
            if "नगर पालिका" in dec:
                meta["body_type"] = "nagar_palika"
                meta["body_type_label"] = "नगर पालिका परिषद"
            elif "नगर निगम" in dec:
                meta["body_type"] = "nagar_nigam"
                meta["body_type_label"] = "नगर निगम"
            elif "ग्राम पंचायत" in dec or "पंचायत निर्वाचन" in dec:
                meta["body_type"] = "gram_panchayat"
                meta["body_type_label"] = "ग्राम पंचायत"
            else:
                meta["body_type"] = "nagar_panchayat"
                meta["body_type_label"] = "नगर पंचायत"

            # Boundary lookahead specifically for section headers
            stop_pat = r'(?=(?:\s+[1-7]\s*[-–]\s*(?:जिला|जजला|मजल|निकाय|िनकाय|वार्ड|वाड|मतदान|भाग|सजम्ममसलत|सम्मिलित|सम्मसलत|मोहल्ले))|मका|क्र|िनर्वाचक|$)'

            # 1-जिला
            dist_m = re.search(r'1\s*[-–]\s*(?:जिला|जजला|मजल[ा|]?|जजा|जज\w+)\s*[:\-–]?\s*(.+?)' + stop_pat, dec)
            if dist_m:
                val = re.sub(r'^\d+\s*[-–]\s*', '', dist_m.group(1).strip())
                if any(k in val for k in ("सम्भल", "म्मभ", "स्भल")):
                    val = "सम्भल"
                meta["district"] = val

            # 2-निकाय का नाम
            nikay_m = re.search(r'2\s*[-–]\s*(?:निकाय|िनकाय|िकाय|िका)\s*(?:का\s*नाम|का\s*ाम)?\s*[:\-–]?\s*(.+?)' + stop_pat, dec)
            if nikay_m:
                raw_n = nikay_m.group(1).strip()
                clean_n = re.sub(r'^\d+\s*[-–]\s*', '', raw_n).strip()
                meta["nikay_name"] = clean_n or raw_n

            # 3-वार्ड
            ward_m = re.search(r'3\s*[-–]\s*(?:वार्ड|वार्|वाड)\s*[:\-–]?\s*(.+?)' + stop_pat, dec)
            if ward_m:
                raw_w = ward_m.group(1).strip()
                meta["ward"] = raw_w
                w_num = re.search(r'^(\d+)', raw_w)
                if w_num:
                    meta["ward_no"] = w_num.group(1)
                    w_name = re.sub(r'^\d+\s*[-–]\s*', '', raw_w).strip()
                    meta["ward_name"] = w_name or raw_w
                else:
                    meta["ward_no"] = ""
                    meta["ward_name"] = raw_w

            # 4-मतदान केंद्र
            center_m = re.search(r'4\s*[-–]\s*(?:मतदान|मतदा|मा)\s*(?:केंद्र|केन्द्र)\s*[:\-–]?\s*(.+?)' + stop_pat, dec)
            if center_m:
                meta["polling_station"] = center_m.group(1).strip()

            # 5-भाग संख्या (निकाय की कोई भी सूची बिना भाग संख्या के नहीं हो सकती)
            part_m = re.search(r'5\s*[-–]\s*(?:भाग\s*(?:संख्या|संÉया|ंख्यया|संख्यया|संख्या|ंÉया|सं०|\w*))\s*[:\-–]?\s*(\d+)', dec)
            if not part_m:
                part_m = re.search(r'भाग\s*(?:संख्या|संÉया|ंÉया|सं०|\w*)?\s*[:\-–]?\s*(\d+)', dec)
            if part_m:
                meta["part_no"] = part_m.group(1).strip()

            # 6-मतदान स्थल (बूथ / कक्ष)
            booth_m = re.search(r'6\s*[-–]\s*(?:मतदान|मतदा|मा)\s*(?:स्थल|स्मथल|स्तथल|स्म)\s*[:\-–]?\s*(.+?)' + stop_pat, dec)
            if booth_m:
                raw_b = booth_m.group(1).strip()
                raw_b = re.sub(r'^(?:स्थल|स्मथल|स्तथल|स्म)\s*[:\-–]?', '', raw_b).strip()
                meta["polling_booth"] = raw_b

            # 7-सम्मिलित मोहल्ले के नाम
            moh_m = re.search(r'7\s*[-–]\s*(?:सम्मिलित|सम्मसलत|स[िजी]?म्म\w*|जम्म\w*|मोहल्ले)?\s*(?:मोहल्ले\s*के\s*नाम|मोहल्लले\s*के\s*नाम|मोाल्ले\s*के\s*ाम)?\s*[:\-–]?\s*(.+?)' + stop_pat, dec)
            if moh_m:
                raw_moh = moh_m.group(1).strip()
                raw_moh = re.sub(r'^(?:सम्मिलित|सम्मसलत|स[िजी]?म्म\w*|जम्म\w*|मोहल्ले|मोहल्लले|मोाल्ले)\s*(?:के\s*नाम|के\s*ाम)?\s*[:\-–]?', '', raw_moh).strip()
                if ':' in raw_moh:
                    raw_moh = raw_moh.split(':', 1)[1].strip()
                meta["mohalla"] = raw_moh

            # If 7- was merged inside polling_booth due to font encoding artifact, split cleanly
            if meta.get("polling_booth") and (" 7-" in meta["polling_booth"] or " 7 -" in meta["polling_booth"] or " 7–" in meta["polling_booth"]):
                parts_7 = re.split(r'\s+7\s*[-–]\s*', meta["polling_booth"], maxsplit=1)
                meta["polling_booth"] = parts_7[0].strip()
                if not meta.get("mohalla") and len(parts_7) > 1:
                    raw_m = parts_7[1].strip()
                    if ':' in raw_m:
                        raw_m = raw_m.split(':', 1)[1].strip()
                    else:
                        raw_m = re.sub(r'^(?:सम्मिलित|सम्मसलत|स[िजी]?म्म\w*|जम्म\w*|मोहल्ले|मोहल्लले|मोाल्ले)?\s*(?:के\s*नाम|के\s*ाम)?\s*[:\-–]?', '', raw_m).strip()
                    meta["mohalla"] = raw_m

            # Enforce that every Nikay list must have a valid Part Number (भाग संख्या)
            # In UP SEC lists, booth leading digits (e.g. 4-बाबूरामसिंह or 19-प्रा0पा0) represent part number
            if not meta.get("part_no") or meta.get("part_no") == meta.get("ward_no"):
                if meta.get("polling_booth"):
                    lead_m = re.match(r'^\s*(\d+)\s*[-–]', meta["polling_booth"])
                    if lead_m:
                        meta["part_no"] = lead_m.group(1).strip()

            if not meta.get("part_no"):
                if fallback_meta and fallback_meta.get("part_no"):
                    meta["part_no"] = fallback_meta["part_no"]
                elif meta.get("ward_no"):
                    meta["part_no"] = meta["ward_no"]
                else:
                    meta["part_no"] = "1"

            parts = []
            if meta["nikay_name"]:
                parts.append(meta["nikay_name"])
            if meta["ward"]:
                parts.append(f"वार्ड {meta['ward']}")
            meta["assembly"] = " - ".join(parts) if parts else (meta["nikay_name"] or "नगर निकाय")

        except Exception as e:
            print(f"[WARN] Failed to parse ULB page header: {e}")

        # Final guarantee: part_no is never None
        if not meta.get("part_no"):
            meta["part_no"] = meta.get("ward_no") or "1"

        return meta

    @classmethod
    def inspect_pdf_all_parts(cls, doc: fitz.Document) -> Dict[str, Any]:
        """
        Rapidly scans header region of every page in the PDF.
        Identifies all distinct Part blocks, Ward numbers, Polling stations, Booths,
        and their respective Page Ranges (page_start to page_end).
        """
        total_pages = len(doc)
        blocks = []
        current_block = None

        global_meta = {
            "nikay_name": "",
            "body_type": "nagar_panchayat",
            "district": ""
        }

        for page_idx in range(total_pages):
            page_num = page_idx + 1
            page = doc[page_idx]
            h = cls.extract_page_header_metadata(page)

            if not global_meta["nikay_name"] and h.get("nikay_name"):
                global_meta["nikay_name"] = h["nikay_name"]
            if not global_meta["district"] and h.get("district"):
                global_meta["district"] = h["district"]
            if h.get("body_type"):
                global_meta["body_type"] = h["body_type"]

            ward_no = h.get("ward_no")
            part_no = h.get("part_no")
            booth = h.get("polling_booth")
            ps = h.get("polling_station")
            moh = h.get("mohalla")
            ward_name = h.get("ward_name")

            # Check if this is a summary or empty page (e.g. at end of part)
            if not ward_no and not booth and not ps:
                if current_block:
                    current_block["page_end"] = page_num
                continue

            part_key = (ward_no, part_no, booth)

            if current_block is None:
                current_block = {
                    "page_start": page_num,
                    "page_end": page_num,
                    "ward_no": ward_no or "",
                    "ward_name": ward_name or "",
                    "part_no": part_no or "1",
                    "polling_station": ps or "",
                    "polling_booth": booth or "",
                    "mohalla": moh or "",
                    "_key": part_key
                }
            else:
                if current_block["_key"] == part_key:
                    current_block["page_end"] = page_num
                else:
                    del current_block["_key"]
                    blocks.append(current_block)
                    current_block = {
                        "page_start": page_num,
                        "page_end": page_num,
                        "ward_no": ward_no or "",
                        "ward_name": ward_name or "",
                        "part_no": part_no or "1",
                        "polling_station": ps or "",
                        "polling_booth": booth or "",
                        "mohalla": moh or "",
                        "_key": part_key
                    }

        if current_block:
            if "_key" in current_block:
                del current_block["_key"]
            blocks.append(current_block)

        # Fallback if no blocks detected
        if not blocks:
            first_h = cls.extract_page_header_metadata(doc[0]) if len(doc) > 0 else {}
            blocks.append({
                "page_start": 1,
                "page_end": total_pages,
                "ward_no": first_h.get("ward_no", "1"),
                "ward_name": first_h.get("ward_name", ""),
                "part_no": first_h.get("part_no", "1"),
                "polling_station": first_h.get("polling_station", ""),
                "polling_booth": first_h.get("polling_booth", ""),
                "mohalla": first_h.get("mohalla", "")
            })

        for idx, b in enumerate(blocks, 1):
            b["part_id"] = idx

        return {
            "success": True,
            "total_pages": total_pages,
            "nikay_name": global_meta["nikay_name"],
            "body_type": global_meta["body_type"],
            "district": global_meta["district"],
            "detected_parts": blocks
        }

    @classmethod
    def extract_header_metadata(cls, doc: fitz.Document) -> Dict[str, Optional[str]]:
        """
        Extracts municipal administrative metadata from the first page header.
        (District, Nikay Name, Ward, Polling Station, Part No, Booth, Mohalla).
        """
        meta = {}
        if len(doc) > 0:
            meta = cls.extract_page_header_metadata(page=doc[0])
            if (not meta.get("nikay_name") or not meta.get("ward_no") or not meta.get("part_no")) and len(doc) > 1:
                meta = cls.extract_page_header_metadata(page=doc[1], fallback_meta=meta)
        if not meta.get("part_no"):
            meta["part_no"] = meta.get("ward_no") or "1"
        return meta

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

            # Prepare 300 DPI high-resolution page rendering if OCR is ready
            use_ai_ocr = OCRExtractor.is_tesseract_ready()
            page_img = None
            zoom = 300.0 / 72.0
            if use_ai_ocr:
                try:
                    mat = fitz.Matrix(zoom, zoom)
                    pix = page.get_pixmap(matrix=mat)
                    page_img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                except Exception as e:
                    print(f"[WARN] ULB 300 DPI page render failed: {e}")
                    page_img = None

            doc.close()

            # If page has fewer than 10 words, it is a scanned image-based page
            if len(words) < 10:
                from .ulb_scanner_ai import ULBScannedPageExtractor
                return ULBScannedPageExtractor.process_scanned_page(
                    pdf_path=pdf_path,
                    page_index=page_index,
                    metadata=metadata
                )

            # Extract page metadata dynamically from this page's header words
            page_meta = cls.extract_page_header_metadata(words=words, fallback_meta=metadata)

            # Filter words strictly in table body (y >= 115, avoiding page header)
            table_words = [w for w in words if w[1] >= 115]

            # Separate into Column 1 (Left: x < 295) and Column 2 (Right: x >= 295)
            # Calibrated boundaries based on real SEC UP table headers:
            # Col 1: Serial < 30, House 30..62, Name 62..155, Rel 155..250, Gender 250..275, Age 275..295
            # Col 2: Serial 295..325, House 325..357, Name 357..450, Rel 450..545, Gender 545..570, Age 570..595
            col_ranges = [
                (1, 0, 295, 30, 62, 155, 250, 275, 295),      # Col 1
                (2, 295, 595, 325, 357, 450, 545, 570, 595)   # Col 2
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

                px_x0 = max(0, int(x_min * zoom)) if page_img else 0
                px_x1 = min(page_img.width, int(x_max * zoom)) if page_img else 0

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
                            extra_h = cls.decode_sec_up_font("".join(h_tokens)).strip()
                            if extra_h and not re.match(r'^[0०]+$', extra_h):
                                last_voter.house_no = clean_house_no(f"{last_voter.house_no}{extra_h}")
                        continue

                    try:
                        serial_no = int(s_tokens[0])
                    except ValueError:
                        continue

                    # House Number (decode SEC UP font glyphs e.g. \x80 -> क)
                    raw_house = cls.decode_sec_up_font("".join(h_tokens)).strip()
                    if re.match(r'^[0०]+$', raw_house):
                        raw_house = ""
                    house_no = clean_house_no(raw_house) if raw_house else ""
                    if not house_no and last_voter and last_voter.house_no:
                        house_no = last_voter.house_no

                    # Voter Name & Relative Name (Digital Fallback)
                    raw_name = " ".join(n_tokens)
                    name = cls.clean_compound_hindi_name(cls.decode_sec_up_font(raw_name))
                    name = clean_hindi_text(name)

                    raw_rel = " ".join(r_tokens)
                    rel_name = cls.clean_compound_hindi_name(cls.decode_sec_up_font(raw_rel))
                    rel_name = clean_hindi_text(rel_name)

                    # High-Quality AI Micro-Cell OCR (exact same system as "उच्च क्वालिटी से पुनः स्कैन करें (AI Auto-Fill)")
                    if page_img:
                        try:
                            target_y0_pt = min(w[1] for w in r) - 1
                            target_y1_pt = max(w[3] for w in r) + 1
                            t_card_y0 = max(0, int(target_y0_pt * zoom))
                            t_card_y1 = min(page_img.height, int(target_y1_pt * zoom))
                            target_card_crop = page_img.crop((px_x0, t_card_y0, px_x1, t_card_y1))
                            cw, ch = target_card_crop.size

                            if cw > 20 and ch > 5:
                                name_crop = target_card_crop.crop((int(0.24 * cw), 0, int(0.51 * cw), ch))
                                rel_crop = target_card_crop.crop((int(0.50 * cw), 0, int(0.81 * cw), ch))

                                ocr_n = OCRExtractor._ocr_image(name_crop, lang="hin", psm=7).strip()
                                ocr_r = OCRExtractor._ocr_image(rel_crop, lang="hin", psm=7).strip()

                                clean_ocr_n = clean_hindi_text(ocr_n)
                                clean_ocr_r = clean_hindi_text(ocr_r)

                                if clean_ocr_n and len(clean_ocr_n) >= 2:
                                    name = clean_ocr_n
                                if clean_ocr_r and len(clean_ocr_r) >= 2:
                                    rel_name = clean_ocr_r
                        except Exception as e:
                            pass

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
                        if any(m in name for m in ["देवी", "श्रीमती", "बेगम", "खातून", "बाई", "रानी", "कौर"]) or (age and age >= 21 and rel_name):
                            relation_type = "पति"

                    # Enforce that every voter in ULB/Nagar Nikay has a Part Number (भाग संख्या)
                    assigned_part = page_meta.get("part_no") or (metadata.get("part_no") if metadata else None) or page_meta.get("ward_no") or "1"

                    v = VoterRecord(
                        serial_no=serial_no,
                        name=name,
                        relation_type=relation_type,
                        relation_name=rel_name,
                        house_no=house_no,
                        age=age,
                        gender=gender,
                        epic_no="",  # SEC UP / Nagar Nikay / Panchayat lists do not contain EPIC numbers
                        page_no=page_no,
                        assembly=page_meta.get("assembly"),
                        part_no=assigned_part,
                        polling_station=page_meta.get("polling_station"),
                        polling_booth=page_meta.get("polling_booth"),
                        ward_no=page_meta.get("ward_no"),
                        ward_name=page_meta.get("ward_name"),
                        mohalla=current_mohalla or page_meta.get("mohalla"),
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

            # Apply ULBSequenceHealer (Zero-Error Healing Pipeline)
            from .ulb_scanner_ai import ULBSequenceHealer
            all_voters = ULBSequenceHealer.heal_serial_sequence(all_voters)
            all_voters = ULBSequenceHealer.enforce_gender_and_relations(all_voters)
            all_voters = ULBSequenceHealer.fill_household_continuity(all_voters)
            all_voters = ULBSequenceHealer.clean_linguistic_errors(all_voters)

            # If voter 1 has empty house number but subsequent household member has valid house number, inherit it
            if all_voters and not all_voters[0].house_no and len(all_voters) > 1 and all_voters[1].house_no:
                all_voters[0].house_no = all_voters[1].house_no

            # Local AI Post-Processing: Run LocalScanQualityAI and DualPassErrorCorrector on each voter
            from .error_corrector import DualPassErrorCorrector, LocalScanQualityAI
            cleaned_voters = []
            for v in all_voters:
                eval_res = LocalScanQualityAI.evaluate_record_quality(v)
                v_clean, _ = DualPassErrorCorrector.apply_intelligent_corrections(v, eval_res)
                cleaned_voters.append(v_clean)
            all_voters = cleaned_voters

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
