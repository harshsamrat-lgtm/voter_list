"""
Sample UP Voter List PDF Generator.
Creates authentic-looking synthetic Uttar Pradesh Electoral Roll PDFs
for instant demonstration and automated unit testing.
"""

import os
import pymupdf as fitz
from typing import List, Dict, Any


class SamplePDFGenerator:
    """Generates synthetic UP voter list PDFs with realistic structure and typography."""

    SAMPLE_VOTERS = [
        {"name": "राजेश कुमार शर्मा", "rel_type": "पिता", "rel_name": "मोहन लाल शर्मा", "house": "12/A", "age": 42, "gender": "पुरुष", "epic": "UHQ1049281"},
        {"name": "सुनीता शर्मा", "rel_type": "पति", "rel_name": "राजेश कुमार शर्मा", "house": "12/A", "age": 38, "gender": "महिला", "epic": "UHQ1049282"},
        {"name": "अमित कुमार शर्मा", "rel_type": "पिता", "rel_name": "राजेश कुमार शर्मा", "house": "12/A", "age": 20, "gender": "पुरुष", "epic": "UHQ3948102"},
        {"name": "रामसेवक यादव", "rel_type": "पिता", "rel_name": "रामसरन यादव", "house": "14", "age": 55, "gender": "पुरुष", "epic": "SJP8392019"},
        {"name": "कमला देवी", "rel_type": "पति", "rel_name": "रामसेवक यादव", "house": "14", "age": 50, "gender": "महिला", "epic": "SJP8392020"},
        {"name": "दिनेश यादव", "rel_type": "पिता", "rel_name": "रामसेवक यादव", "house": "14", "age": 26, "gender": "पुरुष", "epic": "SJP9481029"},
        {"name": "पूजा यादव", "rel_type": "पति", "rel_name": "दिनेश यादव", "house": "14", "age": 24, "gender": "महिला", "epic": "SJP9481030"},
        {"name": "मोहम्मद आरिफ", "rel_type": "पिता", "rel_name": "मोहम्मद हनीफ", "house": "15/B", "age": 48, "gender": "पुरुष", "epic": "FDG7381920"},
        {"name": "शबाना परवीन", "rel_type": "पति", "rel_name": "मोहम्मद आरिफ", "house": "15/B", "age": 42, "gender": "महिला", "epic": "FDG7381921"},
        {"name": "मोहम्मद इमरान", "rel_type": "पिता", "rel_name": "मोहम्मद आरिफ", "house": "15/B", "age": 22, "gender": "पुरुष", "epic": "FDG8391029"},
        {"name": "सुरेश चंद्र वर्मा", "rel_type": "पिता", "rel_name": "द्वारिका प्रसाद", "house": "16", "age": 64, "gender": "पुरुष", "epic": "XUA1029384"},
        {"name": "शांति देवी", "rel_type": "पति", "rel_name": "सुरेश चंद्र वर्मा", "house": "16", "age": 60, "gender": "महिला", "epic": "XUA1029385"},
        {"name": "विशाल वर्मा", "rel_type": "पिता", "rel_name": "सुरेश चंद्र वर्मा", "house": "16", "age": 32, "gender": "पुरुष", "epic": "XUA2938471"},
        {"name": "आरती वर्मा", "rel_type": "पति", "rel_name": "विशाल वर्मा", "house": "16", "age": 28, "gender": "महिला", "epic": "XUA2938472"},
        {"name": "मनोज कुमार गुप्ता", "rel_type": "पिता", "rel_name": "रामगोपाल गुप्ता", "house": "18", "age": 46, "gender": "पुरुष", "epic": "IKT4819201"},
        {"name": "सीमा गुप्ता", "rel_type": "पति", "rel_name": "मनोज कुमार गुप्ता", "house": "18", "age": 41, "gender": "महिला", "epic": "IKT4819202"},
        {"name": "अजय कुमार सिंह", "rel_type": "पिता", "rel_name": "हरिशंकर सिंह", "house": "20/1", "age": 52, "gender": "पुरुष", "epic": "UP/01/001/019283"},
        {"name": "रेखा सिंह", "rel_type": "पति", "rel_name": "अजय कुमार सिंह", "house": "20/1", "age": 47, "gender": "महिला", "epic": "UP/01/001/019284"},
        {"name": "राहुल सिंह", "rel_type": "पिता", "rel_name": "अजय कुमार सिंह", "house": "20/1", "age": 23, "gender": "पुरुष", "epic": "UP/01/001/019285"},
        {"name": "प्रिया सिंह", "rel_type": "पिता", "rel_name": "अजय कुमार सिंह", "house": "20/1", "age": 19, "gender": "महिला", "epic": "UP/01/001/019286"},
        {"name": "संजय मौर्य", "rel_type": "पिता", "rel_name": "मंशाराम मौर्य", "house": "22", "age": 39, "gender": "पुरुष", "epic": "UHQ9201928"},
        {"name": "अनिता मौर्य", "rel_type": "पति", "rel_name": "संजय मौर्य", "house": "22", "age": 35, "gender": "महिला", "epic": "UHQ9201929"},
        {"name": "किशन लाल प्रजापति", "rel_type": "पिता", "rel_name": "बाबूराम प्रजापति", "house": "25", "age": 58, "gender": "पुरुष", "epic": "FDG9283019"},
        {"name": "विद्या देवी", "rel_type": "पति", "rel_name": "किशन लाल", "house": "25", "age": 53, "gender": "महिला", "epic": "FDG9283020"},
        {"name": "दीपक प्रजापति", "rel_type": "पिता", "rel_name": "किशन लाल", "house": "25", "age": 27, "gender": "पुरुष", "epic": "FDG9283021"},
        {"name": "अंजली प्रजापति", "rel_type": "पति", "rel_name": "दीपक प्रजापति", "house": "25", "age": 24, "gender": "महिला", "epic": "FDG9283022"},
        {"name": "अनवर अली", "rel_type": "पिता", "rel_name": "शौकत अली", "house": "28", "age": 36, "gender": "पुरुष", "epic": "SJP4910293"},
        {"name": "नजमा खातून", "rel_type": "पति", "rel_name": "अनवर अली", "house": "28", "age": 32, "gender": "महिला", "epic": "SJP4910294"},
        {"name": "प्रेम शंकर दीक्षित", "rel_type": "पिता", "rel_name": "रामेश्वर दीक्षित", "house": "30/A", "age": 67, "gender": "पुरुष", "epic": "XUA8491029"},
        {"name": "सरला दीक्षित", "rel_type": "पति", "rel_name": "प्रेम शंकर दीक्षित", "house": "30/A", "age": 62, "gender": "महिला", "epic": "XUA8491030"}
    ]

    @classmethod
    def _get_devanagari_font(cls) -> str:
        candidates = [
            r"C:\Windows\Fonts\Nirmala.ttc",
            r"C:\Windows\Fonts\mangal.ttf",
            r"C:\Windows\Fonts\ARIALUNI.TTF"
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        return ""

    @classmethod
    def create_sample_pdf(
        cls, 
        output_path: str, 
        pages: int = 2,
        assembly_name: str = "174 - लखनऊ मध्य",
        part_no: str = "125"
    ) -> str:
        """
        Creates a multi-page authentic UP voter list PDF with 30 voters per page.
        """
        doc = fitz.open()
        font_file = cls._get_devanagari_font()
        
        voters_data = cls.SAMPLE_VOTERS
        total_sample = len(voters_data)
        serial_counter = 1
        
        for p in range(pages):
            page = doc.new_page(width=595.28, height=841.89)
            font_id = "f_hin"
            if font_file:
                page.insert_font(fontname=font_id, fontfile=font_file)
            else:
                font_id = "helv"
            
            # 1. Header Box
            header_rect = fitz.Rect(20, 20, 575, 75)
            page.draw_rect(header_rect, color=(0.1, 0.2, 0.5), width=1.5)
            
            header_text = (
                f"भारत निर्वाचन आयोग — मतदाता सूची 2026 S24\n"
                f"विधान सभा निर्वाचन क्षेत्र की संख्या व नाम : {assembly_name}\n"
                f"भाग संख्या : {part_no} | अनुभाग : 1-सदर बाजार वार्ड-4 | पृष्ठ संख्या : {p + 1}"
            )
            page.insert_textbox(header_rect, header_text, fontsize=9, fontname=font_id, color=(0.1, 0.1, 0.1), align=0)
            
            # 2. Draw 3 columns x 10 rows grid
            cols = 3
            rows = 10
            x_start = 20
            y_start = 85
            x_end = 575
            y_end = 800
            
            col_w = (x_end - x_start) / cols
            row_h = (y_end - y_start) / rows
            
            for r in range(rows):
                for c in range(cols):
                    box_x0 = x_start + (c * col_w)
                    box_y0 = y_start + (r * row_h)
                    box_x1 = box_x0 + col_w
                    box_y1 = box_y0 + row_h
                    
                    box_rect = fitz.Rect(box_x0, box_y0, box_x1, box_y1)
                    page.draw_rect(box_rect, color=(0.7, 0.7, 0.7), width=0.7)
                    
                    # Voter Data
                    sample_idx = (serial_counter - 1) % total_sample
                    v_info = voters_data[sample_idx]
                    rel_label = "पति का नाम" if v_info["rel_type"] == "पति" else "पिता का नाम"
                    
                    card_content = (
                        f"{serial_counter}    {v_info['epic']}\n"
                        f"मतदाता का नाम : {v_info['name']}\n"
                        f"{rel_label} : {v_info['rel_name']}\n"
                        f"मकान संख्या : {v_info['house']}\n"
                        f"आयु : {v_info['age']}  लिंग : {v_info['gender']}\n"
                        f"[फोटो उपलब्ध है]"
                    )
                    
                    text_rect = fitz.Rect(box_x0 + 4, box_y0 + 3, box_x1 - 4, box_y1 - 3)
                    page.insert_textbox(text_rect, card_content, fontsize=7.5, fontname=font_id, color=(0, 0, 0))
                    serial_counter += 1
                    
            # Footer
            footer_rect = fitz.Rect(20, 810, 575, 830)
            page.insert_textbox(footer_rect, f"निर्वाचक रजिस्ट्रीकरण अधिकारी, {assembly_name} | पृष्ठ {p+1} / {pages}", fontsize=8, fontname=font_id, color=(0.4, 0.4, 0.4), align=1)
            
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        doc.save(output_path)
        doc.close()
        return output_path
