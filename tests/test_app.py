"""
Automated unit & integration tests for UP Voter List Converter.
"""

import os
import sys
from pathlib import Path

# Configure utf-8 stdout
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from backend.modules.sample_generator import SamplePDFGenerator
from backend.modules.pdf_detector import PDFDetector
from backend.modules.digital_extractor import DigitalVoterExtractor
from backend.modules.field_parser import UPFieldParser
from backend.modules.excel_builder import ExcelBuilder
from backend.models.voter import VoterRecord


def run_tests():
    print("==================================================")
    print("🔍 UP Voter List AI Converter - Automated Tests")
    print("==================================================")
    
    test_pdf_path = str(BASE_DIR / "samples" / "test_sample_up.pdf")
    test_excel_path = str(BASE_DIR / "outputs" / "test_voter_output.xlsx")
    
    # Test 1: Sample PDF Generation
    print("\n[1/5] Testing Sample PDF Generation...")
    SamplePDFGenerator.create_sample_pdf(test_pdf_path, pages=2)
    assert os.path.exists(test_pdf_path), "Sample PDF generation failed!"
    print("  ✓ Sample PDF created successfully at:", test_pdf_path)
    
    # Test 2: PDF Type Detection
    print("\n[2/5] Testing PDF Detector...")
    inspection = PDFDetector.inspect_pdf(test_pdf_path)
    print("  Inspection Result: total_pages=", inspection["total_pages"], "type=", inspection["primary_type"])
    assert inspection["total_pages"] == 2, "Expected 2 pages"
    assert inspection["primary_type"] == "digital", "Expected digital PDF"
    print("  ✓ PDF correctly identified as digital with 2 pages.")
    
    # Test 3: Digital Voter Extraction (Page 1)
    print("\n[3/5] Testing Digital Voter Extractor (Page 1)...")
    res1 = DigitalVoterExtractor.process_page(test_pdf_path, page_index=0, current_serial=1)
    print(f"  Extracted {res1.voter_count} voters from Page 1 (Success={res1.success})")
    assert res1.success, f"Extraction failed: {res1.error_message}"
    assert res1.voter_count >= 20, f"Expected at least 20 voters, got {res1.voter_count}"
    
    sample_voter = res1.voters[0]
    print(f"  First Voter Sample: S.No={sample_voter.serial_no}, Name={sample_voter.name}, Relation={sample_voter.relation_type} ({sample_voter.relation_name}), House={sample_voter.house_no}, Age={sample_voter.age}, Gender={sample_voter.gender}, EPIC={sample_voter.epic_no}")
    assert sample_voter.name != "", "Voter name should not be empty"
    assert sample_voter.epic_no != "", "EPIC number should not be empty"
    print("  ✓ Voter fields parsed accurately.")
    
    # Test 4: Digital Voter Extraction (Page 2)
    print("\n[4/5] Testing Digital Voter Extractor (Page 2)...")
    res2 = DigitalVoterExtractor.process_page(test_pdf_path, page_index=1, current_serial=res1.voter_count + 1)
    print(f"  Extracted {res2.voter_count} voters from Page 2 (Success={res2.success})")
    all_voters = res1.voters + res2.voters
    print(f"  Total Voters Extracted: {len(all_voters)}")
    
    # Test 5: Excel Generation & Statistics
    print("\n[5/5] Testing Excel Workbook Generation & Analytics...")
    stats = ExcelBuilder.calculate_stats(all_voters)
    print(f"  Stats: Total={stats.total_voters}, Males={stats.total_males}, Females={stats.total_females}, Ratio={stats.gender_ratio}, AvgAge={stats.avg_age}")
    assert stats.total_voters == len(all_voters)
    
    ExcelBuilder.generate_excel(
        records=all_voters,
        output_path=test_excel_path,
        assembly_name="174 - लखनऊ मध्य",
        part_no="125"
    )
    assert os.path.exists(test_excel_path), "Excel generation failed!"
    file_size_kb = round(os.path.getsize(test_excel_path) / 1024, 1)
    print(f"  ✓ Excel workbook successfully created ({file_size_kb} KB) at: {test_excel_path}")
    
    print("\n==================================================")
    print("🎉 ALL TESTS PASSED SUCCESSFULLY! 100% OPERATIONAL")
    print("==================================================")


if __name__ == "__main__":
    run_tests()
