import streamlit as st
import pandas as pd
import os
import re
import datetime
from io import BytesIO
import tempfile
from PIL import Image as PILImage, ImageDraw, ImageFont
import base64

# ReportLab imports for PDF generation
from reportlab.lib.pagesizes import landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph, PageBreak, Image
from reportlab.lib.units import cm, inch
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

# Define sticker dimensions
STICKER_WIDTH = 10 * cm
STICKER_HEIGHT = 15 * cm
STICKER_PAGESIZE = (STICKER_WIDTH, STICKER_HEIGHT)

# Define content box dimensions
CONTENT_BOX_WIDTH = 9.8 * cm
CONTENT_BOX_HEIGHT = 5 * cm

def normalize_column_name(col_name):
    """Normalize column names by removing all non-alphanumeric characters and converting to lowercase"""
    return re.sub(r'[^a-zA-Z0-9]', '', str(col_name)).lower()

def find_column(df, possible_names):
    """Find a column in the DataFrame that matches any of the possible names"""
    normalized_df_columns = {normalize_column_name(col): col for col in df.columns}
    normalized_possible_names = [normalize_column_name(name) for name in possible_names]

    for norm_name in normalized_possible_names:
        if norm_name in normalized_df_columns:
            return normalized_df_columns[norm_name]

    # Check for partial matches
    for norm_name in normalized_possible_names:
        for df_norm_name, original_name in normalized_df_columns.items():
            if norm_name in df_norm_name or df_norm_name in norm_name:
                return original_name

    # Check for line location keywords
    for df_norm_name, original_name in normalized_df_columns.items():
        if ('line' in df_norm_name and 'location' in df_norm_name) or 'lineloc' in df_norm_name:
            return original_name

    return None

def process_uploaded_logo(uploaded_logo, target_width_cm, target_height_cm):
    """Process uploaded logo to fit the specified dimensions - CLEAR 23% WIDTH"""
    try:
        # Load image from uploaded file
        logo_img = PILImage.open(uploaded_logo)

        # Convert to RGB if necessary
        if logo_img.mode in ('RGBA', 'LA', 'P'):
            # Create white background
            background = PILImage.new('RGB', logo_img.size, (255, 255, 255))
            if logo_img.mode == 'P':
                logo_img = logo_img.convert('RGBA')
            background.paste(logo_img, mask=logo_img.split()[-1] if logo_img.mode in ('RGBA', 'LA') else None)
            logo_img = background

        # Convert cm to pixels for resizing (using 300 DPI)
        dpi = 300
        box_width_px = int(target_width_cm * dpi / 2.54)
        box_height_px = int(target_height_cm * dpi / 2.54)

        # Get original dimensions
        orig_width, orig_height = logo_img.size
        
        # Calculate aspect ratio and resize to fit within bounds while maintaining aspect ratio
        aspect_ratio = orig_width / orig_height
        target_aspect = box_width_px / box_height_px
        
        if aspect_ratio > target_aspect:
            # Image is wider, fit to width
            new_width = box_width_px
            new_height = int(box_width_px / aspect_ratio)
        else:
            # Image is taller, fit to height
            new_height = box_height_px
            new_width = int(box_height_px * aspect_ratio)
        
        # Resize with high quality
        logo_img = logo_img.resize((new_width, new_height), PILImage.Resampling.LANCZOS)

        # Convert to bytes for ReportLab
        img_buffer = BytesIO()
        logo_img.save(img_buffer, format='PNG', quality=100, optimize=False)
        img_buffer.seek(0)

        # Convert back to cm for ReportLab
        final_width_cm = new_width * 2.54 / dpi
        final_height_cm = new_height * 2.54 / dpi

        print(f"LOGO DEBUG: Target: {target_width_cm:.2f}cm x {target_height_cm:.2f}cm")
        print(f"LOGO DEBUG: Final: {final_width_cm:.2f}cm x {final_height_cm:.2f}cm")
        print(f"LOGO DEBUG: Pixels: {new_width}px x {new_height}px")
        
        # Create ReportLab Image with actual dimensions
        return Image(img_buffer, width=final_width_cm*cm, height=final_height_cm*cm)

    except Exception as e:
        st.error(f"Error processing uploaded logo: {e}")
        return None

def generate_qr_code(data_string):
    """Generate a QR code from the given data string"""
    try:
        import qrcode
        from PIL import Image as PILImage

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )

        qr.add_data(data_string)
        qr.make(fit=True)

        qr_img = qr.make_image(fill_color="black", back_color="white")

        img_buffer = BytesIO()
        qr_img.save(img_buffer, format='PNG')
        img_buffer.seek(0)

        return Image(img_buffer, width=1.8*cm, height=1.8*cm)
    except Exception as e:
        st.error(f"Error generating QR code: {e}")
        return None

def parse_line_location(location_string):
    """Parse line location string and split into 4 boxes"""
    if not location_string or pd.isna(location_string):
        return ["", "", "", ""]

    parts = str(location_string).split("_")
    result = parts[:4] + [""] * (4 - len(parts))
    return result[:4]

def generate_sticker_labels(df, line_loc_header_width, line_loc_box1_width,
                          line_loc_box2_width, line_loc_box3_width, line_loc_box4_width,
                          uploaded_first_box_logo=None):
    """Generate sticker labels with QR code from DataFrame"""
    try:
        # Define column mappings - Added part_status mapping
        column_mappings = {
            'ASSLY': ['assly', 'ASSY NAME', 'Assy Name', 'assy name', 'assyname',
                     'assy_name', 'Assy_name', 'Assembly', 'Assembly Name', 'ASSEMBLY', 'Assembly_Name'],
            'part_no': ['PARTNO', 'PARTNO.', 'Part No', 'Part Number', 'PartNo',
                       'partnumber', 'part no', 'partnum', 'PART', 'part', 'Product Code',
                       'Item Number', 'Item ID', 'Item No', 'item', 'Item'],
            'description': ['DESCRIPTION', 'Description', 'Desc', 'Part Description',
                           'ItemDescription', 'item description', 'Product Description',
                           'Item Description', 'NAME', 'Item Name', 'Product Name'],
            'Part_per_veh': ['QYT', 'QTY / VEH', 'Qty/Veh', 'Qty Bin', 'Quantity per Bin',
                            'qty bin', 'qtybin', 'quantity bin', 'BIN QTY', 'BINQTY',
                            'QTY_BIN', 'QTY_PER_BIN', 'Bin Quantity', 'BIN'],
            'Type': ['TYPE', 'type', 'Type', 'tyPe', 'Type name'],
            'line_location': ['LINE LOCATION', 'Line Location', 'line location', 'LINELOCATION',
                             'linelocation', 'Line_Location', 'line_location', 'LINE_LOCATION',
                             'LineLocation', 'line_loc', 'lineloc', 'LINELOC', 'Line Loc'],
            'part_status': ['PART STATUS', 'Part Status', 'part status', 'PARTSTATUS',
                           'partstatus', 'Part_Status', 'part_status', 'PART_STATUS',
                           'PartStatus', 'STATUS', 'Status', 'status', 'Item Status',
                           'Component Status', 'Part State', 'State']
        }

        # Find columns
        found_columns = {}
        for key, possible_names in column_mappings.items():
            found_col = find_column(df, possible_names)
            if found_col:
                found_columns[key] = found_col

        # Check required columns
        required_columns = ['ASSLY', 'part_no', 'description']
        missing_required = [col for col in required_columns if col not in found_columns]

        if missing_required:
            st.error(f"Missing required columns: {missing_required}")
            return None, None

        # Create a temporary file for PDF
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
            output_pdf_path = tmp_file.name

        # Create PDF with adjusted margins
        def draw_border(canvas, doc):
            canvas.saveState()
            x_offset = (STICKER_WIDTH - CONTENT_BOX_WIDTH) / 2
            y_offset = STICKER_HEIGHT - CONTENT_BOX_HEIGHT - 0.2*cm
            canvas.setStrokeColor(colors.black)
            canvas.setLineWidth(1.5)
            canvas.rect(
                x_offset,
                y_offset,
                CONTENT_BOX_WIDTH,
                CONTENT_BOX_HEIGHT
            )
            canvas.restoreState()

        doc = SimpleDocTemplate(output_pdf_path, pagesize=STICKER_PAGESIZE,
                              topMargin=0.2*cm,
                              bottomMargin=(STICKER_HEIGHT - CONTENT_BOX_HEIGHT - 0.2*cm),
                              leftMargin=(STICKER_WIDTH - CONTENT_BOX_WIDTH) / 2,
                              rightMargin=(STICKER_WIDTH - CONTENT_BOX_WIDTH) / 2)

        # Define styles
        header_style = ParagraphStyle(name='HEADER', fontName='Helvetica-Bold', fontSize=8, alignment=TA_CENTER, leading=9)
        ASSLY_style = ParagraphStyle(
            name='ASSLY',
            fontName='Helvetica',
            fontSize=9,
            alignment=TA_LEFT,
            leading=11,
            spaceAfter=0,
            wordWrap='CJK',
            autoLeading="max"
        )
        Part_style = ParagraphStyle(
            name='PART NO',
            fontName='Helvetica-Bold',
            fontSize=11,
            alignment=TA_LEFT,
            leading=13,
            spaceAfter=0,
            wordWrap='CJK',
            autoLeading="max"
        )
        # New style for part status box
        Part_status_style = ParagraphStyle(
            name='PART STATUS',
            fontName='Helvetica-Bold',
            fontSize=9,
            alignment=TA_CENTER,
            leading=11,
            spaceAfter=0,
            wordWrap='CJK',
            autoLeading="max"
        )
        desc_style = ParagraphStyle(name='PART DESC', fontName='Helvetica', fontSize=7, alignment=TA_LEFT, leading=8, spaceAfter=0, wordWrap='CJK', autoLeading="max")
        partper_style = ParagraphStyle(name='Quantity', fontName='Helvetica', fontSize=9, alignment=TA_LEFT, leading=12)
        Type_style = ParagraphStyle(name='Quantity', fontName='Helvetica', fontSize=9, alignment=TA_LEFT, leading=12)
        date_style = ParagraphStyle(name='DATE', fontName='Helvetica', fontSize=9, alignment=TA_LEFT, leading=12)
        location_style = ParagraphStyle(name='Location', fontName='Helvetica', fontSize=8, alignment=TA_CENTER, leading=10)

        content_width = CONTENT_BOX_WIDTH
        all_elements = []
        today_date = datetime.datetime.now().strftime("%d-%m-%Y")

        # Handle uploaded logo for first box - CLEAR 23% WIDTH
        first_box_logo = None
        if uploaded_first_box_logo is not None:
            # FIXED: Logo takes 23% of total content width
            logo_width_cm = (content_width * 0.23) / cm  # 23% of content width in cm
            logo_height_cm = 0.75  # 0.75cm height (within 0.85cm row height)

            print(f"LOGO CALCULATION:")
            print(f"Content width: {content_width/cm:.2f}cm")
            print(f"Logo width (23%): {logo_width_cm:.2f}cm")
            print(f"Logo height: {logo_height_cm:.2f}cm")

            first_box_logo = process_uploaded_logo(uploaded_first_box_logo, logo_width_cm, logo_height_cm)
            if first_box_logo:
                st.success(f"✅ Logo processed - Size: {logo_width_cm:.2f}cm x {logo_height_cm:.2f}cm (23% width)")
            else:
                st.error("❌ Failed to process uploaded logo")

        # Process each row
        total_rows = len(df)
        progress_bar = st.progress(0)

        for index, row in df.iterrows():
            progress_bar.progress((index + 1) / total_rows)

            elements = []

            # Extract data - Added part_status extraction
            ASSLY = str(row[found_columns.get('ASSLY', '')]) if 'ASSLY' in found_columns else "N/A"
            part_no = str(row[found_columns.get('part_no', '')]) if 'part_no' in found_columns else "N/A"
            desc = str(row[found_columns.get('description', '')]) if 'description' in found_columns else "N/A"
            Part_per_veh = str(row[found_columns.get('Part_per_veh', '')]) if 'Part_per_veh' in found_columns and pd.notna(row[found_columns['Part_per_veh']]) else ""
            Type = str(row[found_columns.get('Type', '')]) if 'Type' in found_columns and pd.notna(row[found_columns['Type']]) else ""
            line_location_raw = str(row[found_columns.get('line_location', '')]) if 'line_location' in found_columns and pd.notna(row[found_columns['line_location']]) else ""
            part_status = str(row[found_columns.get('part_status', '')]) if 'part_status' in found_columns and pd.notna(row[found_columns['part_status']]) else ""
            location_boxes = parse_line_location(line_location_raw)

            # Generate QR code - Added part_status to QR data
            qr_data = f"ASSLY: {ASSLY}\nPart No: {part_no}\nDescription: {desc}\n"
            if Part_per_veh:
                qr_data += f"QTY/VEH: {Part_per_veh}\n"
            if Type:
                qr_data += f"Type: {Type}\n"
            if part_status:
                qr_data += f"Part Status: {part_status}\n"
            if line_location_raw:
                qr_data += f"Line Location: {line_location_raw}\n"
            qr_data += f"Date: {today_date}"

            qr_image = generate_qr_code(qr_data)
            if qr_image:
                qr_cell = qr_image
            else:
                qr_cell = Paragraph("QR", ParagraphStyle(name='QRPlaceholder', fontName='Helvetica-Bold', fontSize=12, alignment=TA_CENTER))

            # Row heights
            ASSLY_row_height = 0.85*cm
            part_row_height = 0.8*cm   
            desc_row_height = 0.5*cm
            bottom_row_height = 0.6*cm
            location_row_height = 0.5*cm

            # Process line location boxes
            location_box_1 = Paragraph(location_boxes[0], location_style) if location_boxes[0] else ""
            location_box_2 = Paragraph(location_boxes[1], location_style) if location_boxes[1] else ""
            location_box_3 = Paragraph(location_boxes[2], location_style) if location_boxes[2] else ""
            location_box_4 = Paragraph(location_boxes[3], location_style) if location_boxes[3] else ""

            # Create ASSLY row content
            first_box_content = first_box_logo if first_box_logo else ""

            # MODIFIED: Create table data with 3-box PART NO row
            unified_table_data = [
                [first_box_content, "ASSLY", Paragraph(ASSLY, ASSLY_style)],
                ["PART NO", Paragraph(f"<b>{part_no}</b>", Part_style), "", Paragraph(f"<b>{part_status}</b>", Part_status_style)],  # Modified row with 4 columns
                ["PART DESC", Paragraph(desc, desc_style)],
                ["QTY/VEH", Paragraph(str(Part_per_veh), partper_style), qr_cell],
                ["TYPE", Paragraph(str(Type), Type_style), ""],
                ["DATE", Paragraph(today_date, date_style), ""],
                ["LINE LOCATION", location_box_1, location_box_2, location_box_3, location_box_4]
            ]

            # Column widths
            col_widths_assly = [
                content_width * 0.25,    # Logo box: 25%
                content_width * 0.15,    # Header: 15%
                content_width * 0.60     # Value: 60%
            ]

            # MODIFIED: New column widths for 4-column PART NO row
            col_widths_partno = [
                content_width * 0.25,    # Header: 25%
                content_width * 0.35,    # Part number: 35%
                content_width * 0.15,    # Empty box: 15%
                content_width * 0.25     # Part status: 25%
            ]

            col_widths_standard = [content_width * 0.25, content_width * 0.75]
            col_widths_middle = [content_width * 0.25, content_width * 0.35, content_width * 0.40]
            col_widths_bottom = [
                content_width * line_loc_header_width,
                content_width * line_loc_box1_width,
                content_width * line_loc_box2_width,
                content_width * line_loc_box3_width,
                content_width * line_loc_box4_width
            ]

            row_heights = [ASSLY_row_height, part_row_height, desc_row_height, bottom_row_height, bottom_row_height, bottom_row_height, location_row_height]

            # MODIFIED: Create separate tables with new PART NO table structure
            assly_table = Table([unified_table_data[0]], colWidths=col_widths_assly, rowHeights=[row_heights[0]])
            partno_table = Table([unified_table_data[1]], colWidths=col_widths_partno, rowHeights=[row_heights[1]])  # New 4-column table
            desc_table = Table([unified_table_data[2]], colWidths=col_widths_standard, rowHeights=[row_heights[2]])
            middle_table = Table(unified_table_data[3:6], colWidths=col_widths_middle, rowHeights=row_heights[3:6])
            bottom_table = Table([unified_table_data[6]], colWidths=col_widths_bottom, rowHeights=[row_heights[6]])

            # Apply table styles
            assly_style = [
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTNAME', (1, 0), (1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('ALIGN', (0, 0), (0, 0), 'CENTER'),
                ('ALIGN', (1, 0), (1, 0), 'CENTER'),
                ('ALIGN', (2, 0), (2, 0), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('LEFTPADDING', (0, 0), (-1, -1), 2),
                ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                ('TOPPADDING', (0, 0), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ]

            # MODIFIED: New style for 4-column PART NO table
            partno_style = [
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),  # Header bold
                ('FONTNAME', (1, 0), (1, 0), 'Helvetica-Bold'),  # Part number bold
                ('FONTNAME', (3, 0), (3, 0), 'Helvetica-Bold'),  # Part status bold
                ('FONTSIZE', (0, 0), (0, 0), 8),                # Header font size
                ('FONTSIZE', (1, 0), (1, 0), 11),               # Part number font size
                ('FONTSIZE', (3, 0), (3, 0), 9),                # Part status font size
                ('ALIGN', (0, 0), (0, 0), 'CENTER'),            # Header centered
                ('ALIGN', (1, 0), (1, 0), 'LEFT'),              # Part number left
                ('ALIGN', (2, 0), (2, 0), 'CENTER'),            # Empty box centered
                ('ALIGN', (3, 0), (3, 0), 'CENTER'),            # Part status centered
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                ('TOPPADDING', (0, 0), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ]

            desc_style_table = [
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (0, -1), 8),
                ('FONTSIZE', (1, 0), (-1, 0), 7),
                ('ALIGN', (0, 0), (0, -1), 'CENTER'),
                ('ALIGN', (1, 0), (1, 0), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                ('TOPPADDING', (0, 0), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ]

            middle_style = [
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (0, 0), 8),
                ('FONTSIZE', (0, 1), (0, 2), 8),
                ('FONTSIZE', (1, 0), (-1, -1), 10),
                ('ALIGN', (0, 0), (0, -1), 'CENTER'),
                ('ALIGN', (1, 0), (1, -1), 'LEFT'),
                ('ALIGN', (2, 0), (2, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                ('TOPPADDING', (0, 0), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                ('SPAN', (2, 0), (2, 2)),
            ]

            bottom_style = [
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('ALIGN', (0, 0), (0, 0), 'CENTER'),
                ('ALIGN', (1, 0), (-1, 0), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                ('TOPPADDING', (0, 0), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ]

            # Apply table styles
            assly_table.setStyle(TableStyle(assly_style))
            partno_table.setStyle(TableStyle(partno_style))  # New part number table
            desc_table.setStyle(TableStyle(desc_style_table))
            middle_table.setStyle(TableStyle(middle_style))
            bottom_table.setStyle(TableStyle(bottom_style))

            # MODIFIED: Add tables to elements with new part number table
            elements.extend([assly_table, partno_table, desc_table, middle_table, bottom_table])

            # Add page break after each sticker except the last one
            if index < len(df) - 1:
                elements.append(PageBreak())

            all_elements.extend(elements)

        # Build PDF
        doc.build(all_elements, onFirstPage=draw_border, onLaterPages=draw_border)

        progress_bar.empty()
        st.success(f"✅ Successfully generated {total_rows} sticker labels with part status boxes!")

        # Read the generated PDF
        with open(output_pdf_path, 'rb') as pdf_file:
            pdf_data = pdf_file.read()

        # Clean up temporary file
        os.unlink(output_pdf_path)

        return pdf_data, f"sticker_labels_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

    except Exception as e:
        st.error(f"Error generating sticker labels: {str(e)}")
        return None, None

def main():
    """Main Streamlit application"""
    st.set_page_config(page_title="Sticker Label Generator", layout="wide")

    st.title("🏷️ Sticker Label Generator")
    st.markdown("Generate professional sticker labels with QR codes from your CSV/Excel data")

    # Create tabs
    tab1, tab2, tab3 = st.tabs(["📊 Upload Data", "🖼️ Upload Logo", "⚙️ Settings"])

    # Initialize session state for uploaded files
    if 'uploaded_file' not in st.session_state:
        st.session_state.uploaded_file = None
    if 'uploaded_logo' not in st.session_state:
        st.session_state.uploaded_logo = None

    # Tab 1: Data Upload
    with tab1:
        st.header("📊 Upload Your Data File")
        uploaded_file = st.file_uploader(
            "Choose CSV or Excel file",
            type=['csv', 'xlsx', 'xls'],
            help="Upload your data file containing part information",
            key="data_uploader"
        )

        if uploaded_file is not None:
            st.session_state.uploaded_file = uploaded_file
            try:
                # Read the uploaded file
                if uploaded_file.name.endswith('.csv'):
                    df = pd.read_csv(uploaded_file)
                else:
                    df = pd.read_excel(uploaded_file)

                st.success(f"✅ File uploaded successfully! Found {len(df)} rows.")

                # Display data preview
                with st.expander("📋 Data Preview", expanded=True):
                    st.dataframe(df.head(10), use_container_width=True)

                # Show column information
                with st.expander("📝 Column Information"):
                    st.write("**Available Columns:**")
                    for i, col in enumerate(df.columns, 1):
                        st.write(f"{i}. `{col}`")

            except Exception as e:
                st.error(f"❌ Error processing file: {str(e)}")
                st.session_state.uploaded_file = None

    # Tab 2: Logo Upload
    with tab2:
        st.header("🖼️ Upload Your Logo")
        uploaded_logo = st.file_uploader(
            "Choose logo image file",
            type=['png', 'jpg', 'jpeg', 'gif', 'bmp'],
            help="Upload a logo to appear in the first box of each sticker (23% of sticker width)",
            key="logo_uploader"
        )

        if uploaded_logo is not None:
            st.session_state.uploaded_logo = uploaded_logo
            try:
                # Display logo preview
                st.success("✅ Logo uploaded successfully!")
                logo_img = PILImage.open(uploaded_logo)
                
                col1, col2, col3 = st.columns([1, 2, 1])
                with col2:
                    st.image(logo_img, caption="Logo Preview", use_column_width=True)
                
                # Show logo dimensions
                st.write(f"**Original dimensions:** {logo_img.size[0]} x {logo_img.size[1]} pixels")
                st.info("📏 Logo will be automatically resized to fit 23% of sticker width while maintaining aspect ratio")
                
            except Exception as e:
                st.error(f"❌ Error processing logo: {str(e)}")
                st.session_state.uploaded_logo = None

    # Tab 3: Settings
    with tab3:
        st.header("⚙️ Line Location Settings")
        st.markdown("Configure the column widths for the Line Location row")
        
        col1, col2 = st.columns(2)
        
        with col1:
            line_loc_header_width = st.slider(
                "Line Location Header Width",
                min_value=0.10,
                max_value=0.40,
                value=0.25,
                step=0.01,
                format="%.2f",
                help="Width of 'LINE LOCATION' header as fraction of total width"
            )
            
            line_loc_box1_width = st.slider(
                "Box 1 Width",
                min_value=0.10,
                max_value=0.30,
                value=0.1875,
                step=0.01,
                format="%.2f",
                help="Width of first location box"
            )
            
            line_loc_box2_width = st.slider(
                "Box 2 Width",
                min_value=0.10,
                max_value=0.30,
                value=0.1875,
                step=0.01,
                format="%.2f",
                help="Width of second location box"
            )
        
        with col2:
            line_loc_box3_width = st.slider(
                "Box 3 Width",
                min_value=0.10,
                max_value=0.30,
                value=0.1875,
                step=0.01,
                format="%.2f",
                help="Width of third location box"
            )
            
            line_loc_box4_width = st.slider(
                "Box 4 Width",
                min_value=0.10,
                max_value=0.30,
                value=0.1875,
                step=0.01,
                format="%.2f",
                help="Width of fourth location box"
            )
        
        # Calculate total width
        total_width = (line_loc_header_width + line_loc_box1_width + 
                      line_loc_box2_width + line_loc_box3_width + line_loc_box4_width)
        
        if abs(total_width - 1.0) > 0.01:
            st.warning(f"⚠️ Total width is {total_width:.3f} (should be 1.000)")
            st.info("Adjust the sliders so the total equals 1.000")
        else:
            st.success(f"✅ Total width: {total_width:.3f}")

    # Generation Section
    st.header("🏷️ Generate Sticker Labels")
    
    if st.session_state.uploaded_file is not None:
        try:
            # Read the file again for generation
            if st.session_state.uploaded_file.name.endswith('.csv'):
                df = pd.read_csv(st.session_state.uploaded_file)
            else:
                df = pd.read_excel(st.session_state.uploaded_file)
            
            col1, col2, col3 = st.columns([2, 1, 2])
            
            with col2:
                if st.button("🚀 Generate Labels", type="primary", use_container_width=True):
                    with st.spinner("Generating sticker labels..."):
                        pdf_data, filename = generate_sticker_labels(
                            df,
                            line_loc_header_width,
                            line_loc_box1_width,
                            line_loc_box2_width,
                            line_loc_box3_width,
                            line_loc_box4_width,
                            st.session_state.uploaded_logo
                        )
                        
                        if pdf_data:
                            # Create download button
                            st.download_button(
                                label="📥 Download PDF",
                                data=pdf_data,
                                file_name=filename,
                                mime="application/pdf",
                                type="secondary",
                                use_container_width=True
                            )
                            
                            # Display file info
                            file_size_mb = len(pdf_data) / (1024 * 1024)
                            st.success(f"✅ PDF generated successfully! Size: {file_size_mb:.2f} MB")
                        else:
                            st.error("❌ Failed to generate PDF. Please check your data and try again.")
            
            # Show summary information
            st.markdown("---")
            st.subheader("📋 Generation Summary")
            
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.metric("Total Rows", len(df))
            
            with col2:
                st.metric("Total Columns", len(df.columns))
            
            with col3:
                logo_status = "Yes" if st.session_state.uploaded_logo else "No"
                st.metric("Logo Included", logo_status)
            
            with col4:
                st.metric("Sticker Size", "10×15 cm")
            
        except Exception as e:
            st.error(f"❌ Error reading data file: {str(e)}")
    else:
        st.info("📂 Please upload a data file in the 'Upload Data' tab to generate labels.")
    
    # Help Section
    with st.expander("❓ Help & Information"):
        st.markdown("""
        ### Required Columns
        Your data file must contain these columns (case-insensitive):
        - **ASSLY/Assembly**: Assembly name
        - **Part No/Part Number**: Part number
        - **Description**: Part description
        
        ### Optional Columns
        - **QTY/VEH**: Quantity per vehicle
        - **Type**: Part type
        - **Line Location**: Location information (separated by underscore)
        - **Part Status**: Status of the part (NEW, ACTIVE, etc.)
        
        ### Features
        - ✅ QR codes with all part information
        - ✅ Customizable logo placement (23% width)
        - ✅ Configurable line location boxes
        - ✅ Professional PDF output
        - ✅ 10×15 cm sticker format
        - ✅ Automatic column detection
        - ✅ Part status display box
        
        ### Tips
        - Use clear, readable fonts in your logo
        - Keep part descriptions concise
        - Line location format: "Box1_Box2_Box3_Box4"
        - File formats: CSV, Excel (.xlsx, .xls)
        """)

if __name__ == "__main__":
    main()
