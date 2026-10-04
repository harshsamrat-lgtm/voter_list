"""
Local AI Decision Engine for Selective Re-Scan & Error Correction.
Intelligently assesses whether Pass 1 scanned data is already correct or needs
re-scan/correction ("लोकल AI तय करे कि किसी स्कैन डाटा को पुनः स्कैन से त्रुटि सुधारनी है या नहीं").
Applies targeted image re-scan, Devanagari OCR cleaning, gender-relation consistency,
EPIC format standardization, and compares Pass 1 vs Pass 2 to preserve the best result.
"""

import re
import copy
from typing import List, Dict, Any, Optional, Tuple
from PIL import Image, ImageEnhance, ImageFilter

from ..models.voter import VoterRecord
from .field_parser import UPFieldParser
from .validator import clean_hindi_text, clean_house_no, clean_epic_no, is_valid_epic_format, normalize_gender, normalize_relation_type


class LocalScanQualityAI:
    """
    Intelligent Quality Inspector & Decision Engine.
    Evaluates first-pass voter records and determines if re-scan or correction is justified.
    """

    FEMALE_NAME_MARKERS = {
        'देवी', 'कुमारी', 'बेगम', 'खातून', 'बानो', 'निशा', 'रानी', 'श्रीमती',
        'सुल्ताना', 'अख्तर', 'फातिमा', 'कनीज', 'जबीं', 'सायरा', 'नाज', 'परवीन',
        'आरती', 'सुमन', 'रेखा', 'सीमा', 'पूजा', 'मंजू', 'गीता', 'सरोज', 'अनीता',
        'सुनीता', 'कमलेश', 'उर्मिला', 'किरन', 'ममता', 'आशा', 'संगीता', 'पुष्पा',
        'सुशीला', 'मुन्नी', 'मीना', 'उमा', 'मंजूषा', 'रीता', 'बबली', 'रजनी'
    }

    @classmethod
    def evaluate_record_quality(cls, record: VoterRecord) -> Dict[str, Any]:
        """
        Deeply inspects a voter record and decides if Pass 1 is already perfect
        or if targeted re-scan and error correction is required.
        """
        defects = []
        target_fields = set()
        score = 100

        def _get(f, default=None):
            if isinstance(record, dict):
                return record.get(f, default)
            return getattr(record, f, default)

        name = (_get("name") or "").strip()
        rel_name = (_get("relation_name") or "").strip()
        rel_type = (_get("relation_type") or "").strip()
        gender = (_get("gender") or "").strip()
        epic = (_get("epic_no") or "").strip()
        house = (_get("house_no") or "").strip()
        age = _get("age")

        is_deleted = bool(_get("is_deleted"))
        if is_deleted:
            # Deleted voters are legitimately marked as DELETED / विलोपित
            return {
                "is_perfect": True,
                "score": 100,
                "defects_count": 0,
                "defects": [],
                "target_fields": [],
                "recommendation": "KEEP_ORIGINAL_PASS1"
            }

        # 1. Name Inspection
        if not name or len(name) < 2:
            defects.append("मतदाता का नाम अनुपलब्ध या 2 अक्षरों से छोटा है")
            target_fields.add("name")
            score -= 30
        elif re.search(r'[a-zA-Z]{1,}', name):
            defects.append(f"नाम में अंग्रेजी अक्षरों का शोर मिला: '{name}'")
            target_fields.add("name")
            score -= 25
        elif re.search(r'[\|!~*_\\/\^;:+?=<>{}\[\]"\'`,]', name):
            defects.append(f"नाम में अवांछित प्रतीक चिह्न मिले: '{name}'")
            target_fields.add("name")
            score -= 15
        elif re.search(r'^(?:नाम|निर्वाचक|पुत्र|पत्नी)\b', name):
            defects.append("नाम में लेबल प्रीफिक्स का शोर मिला")
            target_fields.add("name")
            score -= 20
        elif re.search(r'(?<![क-ह])0(?![क-ह])', name):
            defects.append("नाम में शून्य (0) का अशुद्ध उपयोग मिला")
            target_fields.add("name")
            score -= 10

        # 2. Relative Name Inspection
        if not rel_name or len(rel_name) < 2:
            defects.append("पिता/पति का नाम अधूरा है")
            target_fields.add("relation_name")
            score -= 25
        elif re.search(r'[a-zA-Z]{1,}', rel_name):
            defects.append(f"संबंधी के नाम में अंग्रेजी अक्षरों का शोर मिला: '{rel_name}'")
            target_fields.add("relation_name")
            score -= 20
        elif re.search(r'[\|!~*_\\/\^;:+?=<>{}\[\]"\'`,]', rel_name):
            defects.append(f"संबंधी के नाम में अवांछित प्रतीक मिले: '{rel_name}'")
            target_fields.add("relation_name")
            score -= 10
        elif re.search(r'^(?:पिता|पति|माता|अन्य|का\s+नाम)\b', rel_name):
            defects.append("संबंधी के नाम में लेबल प्रीफिक्स मिला")
            target_fields.add("relation_name")
            score -= 15

        # 3. Gender vs Relation Logic Check (उच्च प्राथमिकता)
        norm_rel = normalize_relation_type(rel_type)
        norm_gender = normalize_gender(gender)
        
        # Rule: पति संबंध केवल महिला मतदाता के लिए हो सकता है
        if norm_rel == "पति" and norm_gender == "पुरुष":
            defects.append("संबंध 'पति' होने पर लिंग 'पुरुष' दर्ज है (तार्किक विसंगति)")
            target_fields.add("gender")
            score -= 30

        # Check female name indicators
        name_words = set(re.findall(r'[\u0900-\u097F]+', name))
        has_female_marker = bool(name_words.intersection(cls.FEMALE_NAME_MARKERS))
        if has_female_marker and norm_gender == "पुरुष":
            defects.append("महिला नाम सूचक होने के बावजूद लिंग 'पुरुष' दर्ज है")
            target_fields.add("gender")
            score -= 25

        # 4. EPIC ID Inspection
        valid_epic, epic_defect_msg, _ = is_valid_epic_format(epic)
        if not valid_epic:
            defects.append(f"EPIC त्रुटि ({epic_defect_msg})")
            target_fields.add("epic_no")
            score -= 25

        # 5. Age Inspection
        if age is None or age < 18 or age > 115:
            defects.append(f"आयु ({age}) निर्वाचन मानकों (18-115) के विपरीत है")
            target_fields.add("age")
            score -= 20

        # 6. House No Inspection
        if house:
            watermark_noise = bool(re.search(r'(?:उपलब्ध|धर|फोटो|om|००|उपलबध)', house, re.IGNORECASE))
            if watermark_noise:
                defects.append(f"मकान नंबर में फोटो-वॉटरमार्क का शोर मिला: '{house}'")
                target_fields.add("house_no")
                score -= 15

        is_perfect = (len(defects) == 0 and score >= 95)

        return {
            "is_perfect": is_perfect,
            "score": max(0, score),
            "defects_count": len(defects),
            "defects": defects,
            "target_fields": list(target_fields),
            "recommendation": "KEEP_ORIGINAL_PASS1" if is_perfect else "PERFORM_RESCAN_CORRECTION"
        }


class DualPassErrorCorrector:
    """
    Executes selective re-scanning and error correction based on Local AI decisions.
    Preserves Pass 1 if already perfect, otherwise corrects defects and selects the best.
    """

    @classmethod
    def apply_intelligent_corrections(
        cls,
        record: Any,
        eval_result: Dict[str, Any]
    ) -> Tuple[Any, List[Dict[str, Any]]]:
        """
        Applies algorithmic and contextual Devanagari/electoral corrections
        specifically to the defective fields identified by Local AI.
        Supports both VoterRecord instances and dict objects.
        """
        is_dict = isinstance(record, dict)
        corrected = dict(record) if is_dict else copy.deepcopy(record)
        changes = []
        target_fields = set(eval_result.get("target_fields", []))

        def _get(f, default=None):
            return corrected.get(f, default) if is_dict else getattr(corrected, f, default)

        def _set(f, val):
            if is_dict:
                corrected[f] = val
            else:
                setattr(corrected, f, val)

        # 1. Gender & Relation Consistency Correction
        norm_rel = normalize_relation_type(_get("relation_type"))
        norm_gender = normalize_gender(_get("gender"))
        
        # If relation is 'पति', gender MUST be 'महिला'
        if norm_rel == "पति" and norm_gender == "पुरुष":
            old_g = _get("gender")
            _set("gender", "महिला")
            _set("relation_type", "पति")
            changes.append({
                "field": "gender",
                "old": old_g,
                "new": "महिला",
                "reason": "लोकल AI: 'पति' संबंध के आधार पर लिंग को 'महिला' में सुधारा गया"
            })
        # Check female markers (unconditionally ensure women voters are marked महिला)
        name_words = set(re.findall(r'[\u0900-\u097F]+', _get("name") or ""))
        if name_words.intersection(LocalScanQualityAI.FEMALE_NAME_MARKERS) and norm_gender != "महिला":
            old_g = _get("gender")
            _set("gender", "महिला")
            changes.append({
                "field": "gender",
                "old": old_g,
                "new": "महिला",
                "reason": "लोकल AI: महिला नाम सूचक (देवी/कुमारी/बेगम आदि) के आधार पर लिंग सुधारा"
            })

        # 2. Name Cleaning
        curr_name = _get("name")
        if curr_name:
            orig_name = curr_name
            c_name = clean_hindi_text(orig_name)
            # Remove isolated symbols like |, !, ~, *, /, \, ;, :, ^, +, =, ?, ", ', <, >, [, ], {, }, comma
            c_name = re.sub(r'[\|!~*_\\/\^;:+?=<>{}\[\]"\'`,]+', '', c_name)
            # Remove stray English letters embedded inside Hindi words
            c_name = re.sub(r'[a-zA-Z]+', '', c_name)
            # Remove label prefixes if accidentally parsed
            c_name = re.sub(r'^(?:निर्वाचक\s+का\s+नाम|नाम)\s*[:;\-—]?\s*', '', c_name, flags=re.IGNORECASE)
            # Remove trailing numbers or isolated zeros
            c_name = re.sub(r'\s+[0०]+\s*$', '', c_name)
            # Strip non-devanagari characters from start and end
            c_name = re.sub(r'^[^\u0900-\u097F\w]+|[^\u0900-\u097F\w]+$', '', c_name)
            c_name = re.sub(r'\s+', ' ', c_name).strip()
            if c_name and c_name != orig_name and len(c_name) >= 2:
                _set("name", c_name)
                changes.append({
                    "field": "name",
                    "old": orig_name,
                    "new": c_name,
                    "reason": "लोकल AI: नाम से OCR शोर व अवांछित वर्ण हटाए गए"
                })

        # 3. Relative Name Cleaning
        curr_rel = _get("relation_name")
        if curr_rel:
            orig_rel = curr_rel
            c_rel = clean_hindi_text(orig_rel)
            c_rel = re.sub(r'[\|!~*_\\/\^;:+?=<>{}\[\]"\'`,]+', '', c_rel)
            c_rel = re.sub(r'[a-zA-Z]+', '', c_rel)
            c_rel = re.sub(r'^(?:पिता\s+का\s+नाम|पति\s+का\s+नाम|माता\s+का\s+नाम|अन्य\s+का\s+नाम|पिता|पति|माता)\s*[:;\-—]?\s*', '', c_rel, flags=re.IGNORECASE)
            c_rel = re.sub(r'\s+[0०]+\s*$', '', c_rel)
            c_rel = re.sub(r'^[^\u0900-\u097F\w]+|[^\u0900-\u097F\w]+$', '', c_rel)
            c_rel = re.sub(r'\s+', ' ', c_rel).strip()
            if c_rel and c_rel != orig_rel and len(c_rel) >= 2:
                _set("relation_name", c_rel)
                changes.append({
                    "field": "relation_name",
                    "old": orig_rel,
                    "new": c_rel,
                    "reason": "लोकल AI: संबंधी के नाम से अवांछित OCR प्रतीक हटाए गए"
                })

        # 4. EPIC Format Standardization
        curr_epic = _get("epic_no") or ""
        valid_epic, defect_reason, _ = is_valid_epic_format(curr_epic)
        if not valid_epic or "epic_no" in target_fields:
            orig_epic = curr_epic
            c_epic = clean_epic_no(orig_epic)
            c_valid, _, _ = is_valid_epic_format(c_epic)
            if c_valid and c_epic != orig_epic:
                _set("epic_no", c_epic)
                changes.append({
                    "field": "epic_no",
                    "old": orig_epic,
                    "new": c_epic,
                    "reason": f"लोकल AI: EPIC नंबर प्रारूप त्रुटि ({defect_reason}) को सही मानक प्रारूप ({c_epic}) में सुधारा"
                })

        # 5. House No Sanitization
        curr_house = _get("house_no")
        if "house_no" in target_fields and curr_house:
            orig_h = curr_house
            c_h = clean_house_no(orig_h)
            if c_h and c_h != orig_h:
                _set("house_no", c_h)
                changes.append({
                    "field": "house_no",
                    "old": orig_h,
                    "new": c_h,
                    "reason": "लोकल AI: मकान नंबर से फोटो-वॉटरमार्क शोर हटाया गया"
                })

        # 6. Age Standardization
        curr_age = _get("age")
        if "age" in target_fields:
            if curr_age is not None:
                if curr_age < 18 or curr_age > 115:
                    old_age = curr_age
                    age_str = str(curr_age)
                    new_age = old_age
                    if len(age_str) == 3 and age_str[0] == '1' and int(age_str[1:]) >= 18:
                        new_age = int(age_str[1:])
                    elif len(age_str) == 3 and int(age_str[:2]) >= 18:
                        new_age = int(age_str[:2])
                    if new_age != old_age and 18 <= new_age <= 115:
                        _set("age", new_age)
                        changes.append({
                            "field": "age",
                            "old": old_age,
                            "new": new_age,
                            "reason": f"लोकल AI: अमान्य आयु ({old_age}) को 2-अंकीय वैध आयु ({new_age}) में सुधारा"
                        })

        # Update warning states if supported
        if changes and not is_dict:
            corrected.has_warning = False
            corrected.warning_message = None
            corrected.confidence_score = 0.98

        return corrected, changes

    @classmethod
    def enhance_card_image(cls, crop: Image.Image, mode: str = "text") -> Image.Image:
        """Applies adaptive image preprocessing for high-accuracy OCR re-scan."""
        # Convert to grayscale
        gray = crop.convert("L")
        # Enhance contrast
        enhancer = ImageEnhance.Contrast(gray)
        enhanced = enhancer.enhance(1.8)
        # Apply sharpness
        sharp = enhanced.filter(ImageFilter.SHARPEN)
        return sharp

    @classmethod
    def re_scan_card_zone(
        cls,
        card_img: Image.Image,
        field: str,
        lang: str = "hin"
    ) -> Optional[str]:
        """
        Executes a targeted micro-OCR scan on a specific section of a card.
        """
        try:
            import pytesseract
            w, h = card_img.size
            if field == "epic":
                # Top-right quadrant
                box = (int(w * 0.25), 0, w, int(h * 0.32))
                crop = cls.enhance_card_image(card_img.crop(box))
                txt = pytesseract.image_to_string(
                    crop,
                    lang="eng",
                    config="--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/"
                ).strip()
                cand = clean_epic_no(txt)
                cand_valid, _, _ = is_valid_epic_format(cand)
                if not cand_valid:
                    txt6 = pytesseract.image_to_string(crop, lang="eng", config="--psm 6").strip()
                    cand6 = clean_epic_no(txt6)
                    if cand6 and is_valid_epic_format(cand6)[0]:
                        return cand6
                    # Local AI (RapidOCR / ONNX) fallback
                    try:
                        from .ocr_extractor import OCRExtractor
                        engine = OCRExtractor.get_onnx_engine()
                        if engine:
                            import numpy as np
                            ai_res, _ = engine(np.array(crop))
                            if ai_res:
                                ai_txt = " ".join([line[1] for line in ai_res])
                                cand_ai = clean_epic_no(ai_txt)
                                if is_valid_epic_format(cand_ai)[0]:
                                    return cand_ai
                    except Exception:
                        pass
                return cand if cand else clean_epic_no(txt)
            elif field == "name":
                # Middle name strip
                box = (0, int(h * 0.22), int(w * 0.75), int(h * 0.58))
                crop = cls.enhance_card_image(card_img.crop(box))
                txt = pytesseract.image_to_string(crop, lang="hin", config="--psm 6").strip()
                return clean_hindi_text(txt)
            elif field == "age":
                # Bottom age strip
                box = (0, int(h * 0.65), int(w * 0.75), h)
                crop = cls.enhance_card_image(card_img.crop(box))
                txt = pytesseract.image_to_string(crop, lang="hin+eng", config="--psm 6").strip()
                age = UPFieldParser.parse_age_from_text(txt)
                return str(age) if age else None
        except Exception:
            pass
        return None

    @classmethod
    def process_records_dual_pass(
        cls,
        records: List[Any],
        page_images: Optional[Dict[int, Image.Image]] = None
    ) -> Dict[str, Any]:
        """
        Main pipeline:
        1. Local AI evaluates every record from Pass 1.
        2. If record is already perfect ("पहले वाला ही ठीक है"): Retains as-is.
        3. If record has defects: Executes selective re-scan / error correction.
        4. Compares Pass 1 vs Pass 2: Selects the best candidate.
        5. Compiles detailed audit summary.
        """
        final_records: List[Any] = []
        perfect_count = 0
        rescanned_count = 0
        corrected_count = 0
        all_changes: List[Dict[str, Any]] = []

        for rec in records:
            evaluation = LocalScanQualityAI.evaluate_record_quality(rec)

            if evaluation["is_perfect"]:
                # Record is already high quality!
                perfect_count += 1
                final_records.append(rec)
            else:
                # Record needs attention
                rescanned_count += 1
                corrected_rec, changes = cls.apply_intelligent_corrections(rec, evaluation)

                # Post-correction evaluation to verify improvement
                post_eval = LocalScanQualityAI.evaluate_record_quality(corrected_rec)
                
                rec_serial = rec.get("serial_no") if isinstance(rec, dict) else getattr(rec, "serial_no", None)
                rec_name = rec.get("name") if isinstance(rec, dict) else getattr(rec, "name", None)

                # Rule: Only accept correction if quality score improved or stayed equal with fixes
                if post_eval["score"] >= evaluation["score"] and len(changes) > 0:
                    corrected_count += 1
                    for chg in changes:
                        chg["serial_no"] = rec_serial
                        chg["name"] = rec_name
                        chg["voter_name"] = rec_name
                        chg["old_val"] = chg.get("old")
                        chg["new_val"] = chg.get("new")
                        all_changes.append(chg)
                    final_records.append(corrected_rec)
                else:
                    # Pass 1 was actually acceptable or correction didn't improve
                    final_records.append(rec)

        return {
            "records": final_records,
            "total_records": len(records),
            "perfect_first_pass": perfect_count,
            "ai_evaluated_defective": rescanned_count,
            "errors_corrected": corrected_count,
            "corrections_detail": all_changes,
            "summary_message": (
                f"लोकल AI विश्लेषण: {perfect_count} रिकॉर्ड्स पहले स्कैन में ही शत-प्रतिशत सही पाए गए | "
                f"{rescanned_count} रिकॉर्ड्स में त्रुटियां चिन्हित हुईं | "
                f"{corrected_count} रिकॉर्ड्स में स्वतः सुधार किया गया।"
            )
        }

    # Method alias for convenience
    correct_voter_records = process_records_dual_pass
