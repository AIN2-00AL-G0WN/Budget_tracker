"""
app/services/export_service.py
------------------------------
Responsible for generating an in-memory Bytes buffer natively utilizing `openpyxl`.
"""

import io
from typing import List, Dict, Any
import openpyxl
from pydantic import BaseModel

def generate_excel_export(data: List[Any], sheet_name: str = "Export") -> io.BytesIO:
    """
    Dynamically generate an in-memory Excel Workbook explicitly bounding
    the Keys natively across schemas mapped in the payload.
    """
    buffer = io.BytesIO()
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = sheet_name

    if not data:
        # Save empty workbook directly
        workbook.save(buffer)
        buffer.seek(0)
        return buffer

    # Handle Pydantic models vs dicts. We convert all payload logic natively to dictionaries iteratively
    first_item = data[0] if isinstance(data[0], dict) else data[0].model_dump()
    headers = list(first_item.keys())
    
    # Write dynamic headers
    for col_idx, header in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=col_idx, value=str(header))
        cell.font = openpyxl.styles.Font(bold=True)

    # Write dictionary mappings to row values sequentially
    for row_idx, item in enumerate(data, start=2):
        item_dict = item if isinstance(item, dict) else item.model_dump()
        for col_idx, header in enumerate(headers, start=1):
            val = item_dict.get(header)
            # handle dates formatting explicitly if needed natively handling None blocks
            sheet.cell(row=row_idx, column=col_idx, value=str(val) if val is not None else "")

    workbook.save(buffer)
    buffer.seek(0)
    return buffer
