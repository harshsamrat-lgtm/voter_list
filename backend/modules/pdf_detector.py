"""
PDF Type Detector & Pre-flight Inspector.
Determines whether a PDF is Digital (searchable text layer) or Scanned (image-based).
Extracts page counts, metadata, and quality attributes.
"""

import os
from typing import Dict, Any, List
import pymupdf as fitz
from .ulb_extractor import ULBExtractor


class PDFDetector:
    """Inspects and classifies Voter List PDFs."""

    @staticmethod
    def inspect_pdf(pdf_path: str) -> Dict[str, Any]:
        """
        Inspects the PDF to determine page count, text availability,
        voter list format (Nagar Nikay vs ECI Assembly), and whether OCR is required.
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
            
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        
        digital_pages = 0
        scanned_pages = 0
        total_chars = 0
        sample_snippets = []
        page_types = []
        
        # Check first up to 10 pages for character density
        check_limit = min(10, total_pages)
        for page_idx in range(check_limit):
            page = doc[page_idx]
            text = page.get_text("text").strip()
            char_count = len(text)
            total_chars += char_count
            
            # If a page contains at least 60 characters, it has a digital text layer
            if char_count > 60:
                digital_pages += 1
                page_types.append("digital")
                if len(sample_snippets) < 2:
                    sample_snippets.append(text[:250])
            else:
                scanned_pages += 1
                page_types.append("scanned")
                
        doc.close()
        
        # Classify primary mode
        if digital_pages >= scanned_pages:
            primary_type = "digital"
            confidence = digital_pages / check_limit if check_limit > 0 else 1.0
        else:
            primary_type = "scanned"
            confidence = scanned_pages / check_limit if check_limit > 0 else 1.0

        # Classify voter list format: UP Nagar Nikay (ULB) vs ECI Legislative Assembly
        is_ulb = ULBExtractor.is_ulb_pdf(pdf_path)
        voter_format = "UP_NAGAR_NIKAY" if is_ulb else "ECI_ASSEMBLY"
        format_label = "उत्तर प्रदेश नगर निकाय (ULB)" if is_ulb else "भारत निर्वाचन आयोग (ECI विधानसभा)"
            
        return {
            "total_pages": total_pages,
            "sample_pages_checked": check_limit,
            "primary_type": primary_type,  # 'digital' or 'scanned'
            "is_searchable": digital_pages > 0,
            "confidence": round(confidence, 2),
            "digital_page_count": digital_pages,
            "scanned_page_count": scanned_pages,
            "sample_text": "\n---\n".join(sample_snippets) if sample_snippets else "",
            "requires_ocr": False if is_ulb and digital_pages > 0 else (primary_type == "scanned"),
            "voter_list_format": voter_format,
            "format_label": format_label,
            "is_ulb": is_ulb
        }
