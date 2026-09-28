"""
State-of-the-art Excel Workbook Generator using openpyxl.
Creates professionally styled Excel sheets with Header Banners,
Zebra Striping, Demographic Summary Analytics, and Auto-fitting columns.
"""

import os
from datetime import datetime
from typing import List, Dict, Any, Optional
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from ..models.voter import VoterRecord, VoterStats
from .caste_detector import CASTE_PRESETS


class ExcelBuilder:
    """Builds styled Excel workbooks from VoterRecords."""

    # Color Palette
    PRIMARY_COLOR = "1E3A8A"      # Deep Navy Blue
    HEADER_TEXT_COLOR = "FFFFFF"  # White
    ZEBRA_FILL = "F8FAFC"         # Very light slate/blue
    WHITE_FILL = "FFFFFF"
    BORDER_COLOR = "CBD5E1"       # Slate 300
    WARNING_FILL = "FEF3C7"       # Soft Amber
    MALE_FILL = "EFF6FF"          # Light Blue
    FEMALE_FILL = "FDF2F8"        # Soft Pink
    ACCENT_GREEN = "059669"       # Emerald 600

    @classmethod
    def calculate_stats(cls, records: List[VoterRecord]) -> VoterStats:
        """Calculates aggregate demographic statistics for the records."""
        stats = VoterStats()
        stats.total_voters = len(records)
        
        if not records:
            return stats

        ages = []
        for r in records:
            if getattr(r, 'is_deleted', False):
                stats.total_deleted += 1
                continue

            if r.gender == "महिला":
                stats.total_females += 1
            elif r.gender == "अन्य":
                stats.total_other += 1
            else:
                stats.total_males += 1

            if r.is_muslim:
                stats.muslim_voters += 1
            else:
                stats.non_muslim_voters += 1
                
            if not r.epic_no:
                stats.missing_epic_count += 1
            if not r.house_no:
                stats.missing_house_count += 1

            if r.age:
                ages.append(r.age)
                if 18 <= r.age <= 25:
                    stats.age_groups["18-25"] += 1
                elif 26 <= r.age <= 40:
                    stats.age_groups["26-40"] += 1
                elif 41 <= r.age <= 60:
                    stats.age_groups["41-60"] += 1
                elif r.age > 60:
                    stats.age_groups["60+"] += 1
                else:
                    stats.age_groups["अज्ञात"] += 1
            else:
                stats.age_groups["अज्ञात"] += 1

        stats.total_voters = len(records) - stats.total_deleted
                
        if stats.total_males > 0:
            stats.gender_ratio = round((stats.total_females / stats.total_males) * 1000, 1)
        else:
            stats.gender_ratio = 0.0

        if stats.total_voters > 0:
            stats.muslim_percentage = round((stats.muslim_voters / stats.total_voters) * 100, 1)
            
        if ages:
            stats.avg_age = round(sum(ages) / len(ages), 1)
            
        return stats

    @classmethod
    def generate_excel(
        cls, 
        records: List[VoterRecord], 
        output_path: str,
        assembly_name: Optional[str] = None,
        part_no: Optional[str] = None,
        polling_station: Optional[str] = None,
        filename_source: str = "Voter_List"
    ) -> str:
        """
        Creates an executive-grade Excel workbook with 2 sheets:
        1. Voter List (मतदाता सूची)
        2. Summary & Stats (सांख्यिकी व सारांश)
        """
        wb = openpyxl.Workbook()
        
        # Styles definition
        thin_border = Border(
            left=Side(style='thin', color=cls.BORDER_COLOR),
            right=Side(style='thin', color=cls.BORDER_COLOR),
            top=Side(style='thin', color=cls.BORDER_COLOR),
            bottom=Side(style='thin', color=cls.BORDER_COLOR)
        )
        
        header_font = Font(name="Calibri", size=11, bold=True, color=cls.HEADER_TEXT_COLOR)
        header_fill = PatternFill(start_color=cls.PRIMARY_COLOR, end_color=cls.PRIMARY_COLOR, fill_type="solid")
        
        title_font = Font(name="Calibri", size=15, bold=True, color="1E293B")
        subtitle_font = Font(name="Calibri", size=10, italic=True, color="64748B")
        
        data_font = Font(name="Calibri", size=10)
        bold_data_font = Font(name="Calibri", size=10, bold=True)
        epic_font = Font(name="Consolas", size=10, bold=True, color="0F172A")
        
        # =========================================================================
        # SHEET 1: VOTER LIST (मतदाता सूची)
        # =========================================================================
        ws1 = wb.active
        ws1.title = "मतदाता सूची (Voter List)"
        ws1.views.sheetView[0].showGridLines = True
        
        # Title Banner
        is_ulb = any(
            ("वार्ड" in str(getattr(r, "assembly", "") or "") or "निकाय" in str(getattr(r, "assembly", "") or ""))
            for r in records[:20]
        ) or bool(assembly_name and ("वार्ड" in str(assembly_name) or "निकाय" in str(assembly_name) or "नगर" in str(assembly_name)))

        ws1.merge_cells("A1:N1")
        banner_cell = ws1["A1"]
        if is_ulb:
            banner_title = "राज्य निर्वाचन आयोग, उत्तर प्रदेश — नगरीय निकाय सामान्य निर्वाचन नामावली"
            if assembly_name:
                banner_title += f" | निकाय / वार्ड: {assembly_name}"
        else:
            banner_title = "भारत निर्वाचन आयोग — उत्तर प्रदेश मतदाता सूची (Electoral Roll)"
            if assembly_name:
                banner_title += f" | विधान सभा: {assembly_name}"
        if part_no:
            banner_title += f" | भाग सं.: {part_no}"
        if polling_station:
            banner_title += f" | मतदान स्थल: {polling_station}"
        banner_cell.value = banner_title
        banner_cell.font = title_font
        banner_cell.alignment = Alignment(horizontal="left", vertical="center")
        ws1.row_dimensions[1].height = 28
        
        # Subtitle Info
        ws1.merge_cells("A2:N2")
        info_cell = ws1["A2"]
        info_cell.value = f"कुल मतदाता: {len(records)} | निर्यात दिनांक: {datetime.now().strftime('%d-%m-%Y %H:%M')} | डिजिटल कनवर्टर द्वारा तैयार"
        info_cell.font = subtitle_font
        info_cell.alignment = Alignment(horizontal="left", vertical="center")
        ws1.row_dimensions[2].height = 18
        
        # Empty row for spacing
        ws1.row_dimensions[3].height = 8
        
        # Table Headers (Row 4)
        headers = [
            ("क्र. सं.", 8, "center"),
            ("मतदाता का नाम", 24, "left"),
            ("सम्बन्ध", 10, "center"),
            ("पिता / पति का नाम", 24, "left"),
            ("मकान संख्या", 14, "center"),
            ("आयु", 8, "center"),
            ("लिंग", 10, "center"),
            ("EPIC नं. (Voter ID)", 18, "center"),
            ("समुदाय (पहचान)", 16, "center"),
            ("जाति (Caste)", 20, "center"),
            ("निकाय / विधान सभा", 20, "left"),
            ("भाग सं.", 10, "center"),
            ("मतदान स्थल", 32, "left"),
            ("पेज", 8, "center"),
            ("स्थिति (Status)", 16, "center"),
            ("जाँच टिप्पणी", 22, "left")
        ]
        
        header_row = 4
        ws1.row_dimensions[header_row].height = 25
        
        for col_idx, (header_text, width, align) in enumerate(headers, 1):
            cell = ws1.cell(row=header_row, column=col_idx, value=header_text)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal=align, vertical="center", wrap_text=True)
            cell.border = thin_border
            
        # Data Rows
        zebra_pattern = PatternFill(start_color=cls.ZEBRA_FILL, end_color=cls.ZEBRA_FILL, fill_type="solid")
        white_pattern = PatternFill(start_color=cls.WHITE_FILL, end_color=cls.WHITE_FILL, fill_type="solid")
        warning_pattern = PatternFill(start_color=cls.WARNING_FILL, end_color=cls.WARNING_FILL, fill_type="solid")
        muslim_font = Font(name="Calibri", size=10, bold=True, color="047857")
        deleted_font = Font(name="Calibri", size=10, bold=True, color="DC2626")
        caste_font = Font(name="Calibri", size=10, bold=True, color="1E3A8A")
        
        for row_idx, r in enumerate(records, start=5):
            ws1.row_dimensions[row_idx].height = 20
            row_fill = zebra_pattern if row_idx % 2 == 0 else white_pattern
            if r.has_warning:
                row_fill = warning_pattern
                
            community_text = "मुस्लिम" if r.is_muslim else "सामान्य / अन्य"
            community_style = muslim_font if r.is_muslim else data_font

            # Caste label formatting
            # Rule: When voter is Muslim, they do NOT come under a caste based on house number or otherwise
            if r.is_muslim:
                caste_text = "--"
            else:
                caste_key = getattr(r, 'caste_key', None)
                caste_source = getattr(r, 'caste_source', None)
                if caste_key and caste_key != "muslim" and caste_key in CASTE_PRESETS:
                    caste_name = CASTE_PRESETS[caste_key]["label"].split("/")[0].strip()
                    caste_text = f"{caste_name} (🏠 परिवार)" if caste_source == "household_ai" else caste_name
                else:
                    caste_text = "--"

            status_text = "विलोपित (DELETED)" if r.is_deleted else "सक्रिय"
            status_style = deleted_font if r.is_deleted else data_font

            row_values = [
                (r.serial_no, "center", bold_data_font),
                (r.name, "left", data_font),
                (r.relation_type, "center", data_font),
                (r.relation_name, "left", data_font),
                (r.house_no, "center", data_font),
                (r.age if r.age else "", "center", data_font),
                (r.gender, "center", bold_data_font),
                (r.epic_no, "center", epic_font),
                (community_text, "center", community_style),
                (caste_text, "center", caste_font if caste_text != "--" else data_font),
                (r.assembly or assembly_name or "", "left", data_font),
                (r.part_no or part_no or "", "center", data_font),
                (r.polling_station or polling_station or "", "left", data_font),
                (r.page_no, "center", data_font),
                (status_text, "center", status_style),
                (r.warning_message or "✓ सही", "left", data_font)
            ]
            
            for col_idx, (val, align, font_style) in enumerate(row_values, 1):
                cell = ws1.cell(row=row_idx, column=col_idx, value=val)
                cell.font = font_style
                cell.fill = row_fill
                cell.alignment = Alignment(horizontal=align, vertical="center")
                cell.border = thin_border
                
        # Freeze header row & Enable AutoFilter
        ws1.freeze_panes = "A5"
        ws1.auto_filter.ref = f"A4:P{max(4, len(records) + 4)}"
        
        # Set Column Widths
        for col_idx, (_, width, _) in enumerate(headers, 1):
            col_letter = get_column_letter(col_idx)
            ws1.column_dimensions[col_letter].width = width
            
        # =========================================================================
        # SHEET 2: SUMMARY & STATS (सांख्यिकी व सारांश)
        # =========================================================================
        ws2 = wb.create_sheet(title="सांख्यिकी व सारांश (Summary)")
        ws2.views.sheetView[0].showGridLines = True
        
        stats = cls.calculate_stats(records)
        
        # Header
        ws2.merge_cells("A1:D1")
        s_title = ws2["A1"]
        s_title.value = "📊 मतदाता सूची सांख्यिकी व डेमोग्राफिक विश्लेषण"
        s_title.font = title_font
        ws2.row_dimensions[1].height = 30
        
        # Section 1: Overview Cards
        overview_data = [
            ("कुल मतदाता (Total Voters)", stats.total_voters),
            ("पुरुष मतदाता (Total Male Voters)", stats.total_males),
            ("महिला मतदाता (Total Female Voters)", stats.total_females),
            ("अन्य / तृतीय लिंग (Other Voters)", stats.total_other),
            ("लिंगानुपात (Gender Ratio / 1000 Males)", f"{stats.gender_ratio} : 1000"),
            ("औसत आयु (Average Age)", f"{stats.avg_age} वर्ष"),
            ("मुस्लिम मतदाता (Muslim Voters)", f"{stats.muslim_voters} ({stats.muslim_percentage}%)"),
            ("हिन्दू मतदाता (Hindu Voters)", f"{stats.non_muslim_voters}"),
            ("पहचान पत्र (EPIC) उपलब्ध", f"{stats.total_voters - stats.missing_epic_count} / {stats.total_voters}"),
            ("मकान संख्या दर्ज", f"{stats.total_voters - stats.missing_house_count} / {stats.total_voters}"),
            ("विलोपित मतदाता (Deleted Voters)", stats.total_deleted)
        ]
        
        ws2.cell(row=3, column=1, value="मुख्य संकेतक (Key Metrics)").font = Font(name="Calibri", size=12, bold=True, color="1E3A8A")
        ws2.cell(row=3, column=2, value="संख्या / मान").font = Font(name="Calibri", size=12, bold=True, color="1E3A8A")
        
        for idx, (label, val) in enumerate(overview_data, start=4):
            ws2.row_dimensions[idx].height = 22
            c1 = ws2.cell(row=idx, column=1, value=label)
            c2 = ws2.cell(row=idx, column=2, value=val)
            c1.font = data_font
            c2.font = bold_data_font
            c1.border = thin_border
            c2.border = thin_border
            c1.fill = white_pattern
            c2.fill = zebra_pattern
            
        # Section 2: Age Demographics
        start_age_row = 14
        ws2.cell(row=start_age_row, column=1, value="आयु वर्ग (Age Groups)").font = Font(name="Calibri", size=12, bold=True, color="1E3A8A")
        ws2.cell(row=start_age_row, column=2, value="मतदाता संख्या").font = Font(name="Calibri", size=12, bold=True, color="1E3A8A")
        ws2.cell(row=start_age_row, column=3, value="प्रतिशत (%)").font = Font(name="Calibri", size=12, bold=True, color="1E3A8A")
        
        for idx, (group, count) in enumerate(stats.age_groups.items(), start=start_age_row + 1):
            ws2.row_dimensions[idx].height = 20
            pct = f"{round((count / stats.total_voters) * 100, 1)}%" if stats.total_voters > 0 else "0%"
            c1 = ws2.cell(row=idx, column=1, value=f"{group} वर्ष")
            c2 = ws2.cell(row=idx, column=2, value=count)
            c3 = ws2.cell(row=idx, column=3, value=pct)
            for c in [c1, c2, c3]:
                c.font = data_font
                c.border = thin_border
                c.fill = white_pattern
            c2.font = bold_data_font
            
        ws2.column_dimensions["A"].width = 38
        ws2.column_dimensions["B"].width = 22
        ws2.column_dimensions["C"].width = 18
        
        # Save Workbook
        dir_path = os.path.dirname(output_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)
        wb.save(output_path)
        return output_path
