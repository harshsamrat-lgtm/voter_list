"""
Digital PDF Text & Grid Extractor for UP Voter Lists.
Uses PyMuPDF and pdfplumber with column-aware spatial bounding boxes
to prevent text interleaving across voter cards.
"""

import pymupdf as fitz
import pdfplumber
from typing import List, Dict, Any, Optional
from ..models.voter import VoterRecord, PageProcessingResult
from .field_parser import UPFieldParser


class DigitalVoterExtractor:
    """Extracts voter records from digital (text-layer) Electoral Roll PDFs."""

    @classmethod
    def extract_page_by_grid(
        cls, 
        page: fitz.Page, 
        page_no: int, 
        current_serial: int,
        cols: int = 3, 
        rows: int = 10,
        metadata: Optional[Dict] = None
    ) -> List[VoterRecord]:
        """
        Divides page into a 3x10 (or 2x15) grid box matrix and extracts text
        isolated to each voter box.
        """
        rect = page.rect
        width = rect.width
        height = rect.height
        
        # In standard UP voter lists:
        # Top header is approx 7% - 12% from top
        # Bottom footer is approx 3% - 5% from bottom
        # Left/Right margins are approx 3% - 5%
        header_height = height * 0.08
        footer_height = height * 0.04
        left_margin = width * 0.03
        right_margin = width * 0.03
        
        usable_width = width - left_margin - right_margin
        usable_height = height - header_height - footer_height
        
        col_width = usable_width / cols
        row_height = usable_height / rows
        
        voters: List[VoterRecord] = []
        serial_tracker = current_serial
        
        # Iterate row by row (or col by col: voter lists are usually row-major or column-major)
        # UP voter lists are indexed row-major: Box (0,0)=1, Box (0,1)=2, Box (0,2)=3, Box (1,0)=4...
        for r in range(rows):
            for c in range(cols):
                box_x0 = left_margin + (c * col_width)
                box_y0 = header_height + (r * row_height)
                box_x1 = box_x0 + col_width
                box_y1 = box_y0 + row_height
                
                box_rect = fitz.Rect(box_x0, box_y0, box_x1, box_y1)
                box_text = page.get_text("text", clip=box_rect).strip()
                
                is_del = bool(UPFieldParser.IS_DELETED_REGEX.search(box_text))
                if box_text and (is_del or any(kw in box_text for kw in ['नाम', 'Name', 'आयु', 'उम्र', 'लिंग', 'मकान', 'DELETED', 'विलोपित', 'निरस्त']) or len(box_text) > 15):
                    voter = UPFieldParser.parse_single_voter_box(
                        box_text=box_text,
                        default_serial=serial_tracker,
                        page_no=page_no,
                        metadata=metadata
                    )
                    if voter and (voter.name or voter.epic_no or voter.is_deleted):
                        voters.append(voter)
                        serial_tracker = voter.serial_no + 1
                        
        return voters

    @classmethod
    def extract_page_by_blocks(cls, page: fitz.Page, page_no: int, current_serial: int, metadata: Optional[Dict] = None) -> List[VoterRecord]:
        """
        Extracts text blocks using PyMuPDF and clusters them column-wise.
        """
        blocks = page.get_text("blocks")
        # blocks format: (x0, y0, x1, y1, text, block_no, block_type)
        if not blocks:
            return []
            
        page_width = page.rect.width
        
        # Filter out header/footer blocks
        filtered_blocks = [
            b for b in blocks 
            if b[6] == 0 and b[1] > page.rect.height * 0.06 and b[3] < page.rect.height * 0.96
        ]
        
        # Sort blocks: determine column first (x0), then top-to-bottom (y0)
        # Assuming 3 columns:
        def get_col_index(x_mid):
            if x_mid < page_width * 0.35:
                return 0
            elif x_mid < page_width * 0.68:
                return 1
            else:
                return 2
                
        # Group by column
        col_groups: Dict[int, List] = {0: [], 1: [], 2: []}
        for b in filtered_blocks:
            x_mid = (b[0] + b[2]) / 2
            c_idx = get_col_index(x_mid)
            col_groups[c_idx].append(b)
            
        # Sort each column top to bottom
        for c in col_groups:
            col_groups[c].sort(key=lambda b: b[1])
            
        # Reconstruct text row-major (Left to Right, Top to Bottom)
        col_voters_list: List[List[VoterRecord]] = []
        for c in range(3):
            col_text = "\n".join([b[4] for b in col_groups[c]])
            cv = UPFieldParser.parse_full_page_text(col_text, page_no=page_no, current_serial=current_serial + c, metadata=metadata)
            col_voters_list.append(cv)
            
        voters: List[VoterRecord] = []
        max_rows = max(len(cv) for cv in col_voters_list) if col_voters_list else 0
        for r in range(max_rows):
            for c in range(3):
                if r < len(col_voters_list[c]):
                    v = col_voters_list[c][r]
                    expected_s = current_serial + (r * 3 + c)
                    if not v.serial_no or v.serial_no <= 0 or abs(v.serial_no - expected_s) > 2:
                        v.serial_no = expected_s
                    voters.append(v)
                
        return voters

    @classmethod
    def process_page(cls, pdf_path: str, page_index: int, current_serial: int = 1) -> PageProcessingResult:
        """
        Processes a single page from the PDF using digital extraction techniques.
        """
        page_no = page_index + 1
        try:
            doc = fitz.open(pdf_path)
            if page_index >= len(doc):
                doc.close()
                return PageProcessingResult(
                    page_no=page_no,
                    voter_count=0,
                    voters=[],
                    success=False,
                    error_message=f"पृष्ठ {page_no} PDF में मौजूद नहीं है।"
                )
                
            page = doc[page_index]
            full_text = page.get_text("text")
            metadata = UPFieldParser.extract_header_metadata(full_text)
            
            # Try Grid Extraction first (3 cols x 10 rows)
            voters = cls.extract_page_by_grid(page, page_no=page_no, current_serial=current_serial, cols=3, rows=10, metadata=metadata)
            
            # If grid didn't find enough voters, try 2 cols x 15 rows
            if len(voters) < 5:
                voters_2col = cls.extract_page_by_grid(page, page_no=page_no, current_serial=current_serial, cols=2, rows=15, metadata=metadata)
                if len(voters_2col) > len(voters):
                    voters = voters_2col
                    
            # If still low, try block-based spatial parsing
            if len(voters) < 5:
                voters_block = cls.extract_page_by_blocks(page, page_no=page_no, current_serial=current_serial, metadata=metadata)
                if len(voters_block) > len(voters):
                    voters = voters_block
                    
            # Fallback to full page regex stream parser if needed
            if len(voters) < 3:
                voters_stream = UPFieldParser.parse_full_page_text(full_text, page_no=page_no, current_serial=current_serial)
                if len(voters_stream) > len(voters):
                    voters = voters_stream
                    
            doc.close()
            
            return PageProcessingResult(
                page_no=page_no,
                voter_count=len(voters),
                voters=voters,
                raw_text_snippet=full_text[:300] if full_text else "",
                extraction_method="digital",
                success=True
            )
        except Exception as e:
            return PageProcessingResult(
                page_no=page_no,
                voter_count=0,
                voters=[],
                success=False,
                error_message=f"पेज {page_no} प्रोसेसिंग में त्रुटि: {str(e)}"
            )
