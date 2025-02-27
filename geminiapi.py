import streamlit as st
import fitz  # PyMuPDF
import pytesseract
from pdf2image import convert_from_bytes
import google.generativeai as genai
import time
import pdfplumber  # Đừng quên import pdfplumber
import json
import pyodbc
from pydantic import BaseModel
from typing import List, Dict, Union
import re 
import os
import shutil
local_tessdata_path = "vie.traineddata"

# Tạo thư mục tạm thời để lưu tessdata
temp_tessdata_dir = "/tmp/tessdata"
os.makedirs(temp_tessdata_dir, exist_ok=True)

# Sao chép vie.traineddata vào thư mục tạm
temp_vie_path = os.path.join(temp_tessdata_dir, "vie.traineddata")
shutil.copy(local_tessdata_path, temp_vie_path)

# Cấu hình Tesseract để sử dụng thư mục tessdata tạm thời
pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"
tessdata_config = f"--tessdata-dir {temp_tessdata_dir}"
# Cấu hình API Gemini
API_KEY = "AIzaSyBB6h6vrCjAM9CZNPWeovPueudVLnjjYR4"
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# Đường dẫn đến Tesseract OCR
server = r"HIEU-NTM\SQLEXPRESS"  # Cổng kết nối SQL Server
database = "ocr"  # Tên cơ sở dữ liệu
conn = pyodbc.connect(f'DRIVER={{SQL Server}};SERVER={server};DATABASE={database};Trusted_Connection=yes;')

cursor = conn.cursor()

# Hàm để chèn dữ liệu vào bảng ocr_txt
def insert_ocr_txt(file_name, extracted_text, processed_text):
    query = """
    INSERT INTO ocr_txt (file_name, extracted_text, processed_text)
    VALUES (?, ?, ?)
    """
    cursor.execute(query, (file_name, extracted_text, processed_text))
    conn.commit()

# Hàm để chèn dữ liệu vào bảng ocr_json
def insert_ocr_json(file_name, json_data):
    query = """
    INSERT INTO ocr_json (file_name, json_data)
    VALUES (?, ?)
    """
    cursor.execute(query, (file_name, json_data))
    conn.commit()

# Định nghĩa Pydantic models
class FinancialData:
    def __init__(self, year: str, value: str):
        self.year = year
        self.value = value

    def dict(self):
        return {self.year: self.value}

class FinancialStatement:
    def __init__(self, metric: str, values: List[FinancialData]):
        self.metric = metric
        self.values = {v.year: v.value for v in values}

    def dict(self):
        result = {"values": self.values}
        if self.metric:  # Chỉ thêm metric nếu nó không rỗng
            result["metric"] = self.metric
        return result

class FinancialReport:
    def __init__(self, report_day, company_name, industry, financial_statement, forecast, reasons, target_price, risks):
        self.report_day = report_day 
        self.company_name = company_name
        self.industry = industry
        self.financial_statement = financial_statement
        self.forecast = forecast
        self.reasons = reasons
        self.target_price = target_price
        self.risks = risks

    def dict(self):
        return {
            "report_day": self.report_day,
            "company_name": self.company_name,
            "industry": self.industry,
            "financial_statement": [fs.dict() for fs in self.financial_statement],
            "forecast": self.forecast,
            "reasons": self.reasons,
            "target_price": self.target_price,
            "risks": self.risks
        }

def parse_summary_to_model(summary_text: str) -> FinancialReport:
    lines = summary_text.split("\n")
    data = {
        "report_day": "",
        "company_name": "",
        "industry": "",
        "financial_statement": [],
        "forecast": "",
        "reasons": [],
        "target_price": "",
        "risks": []
    }
    
    years = []
    forecast_section = False
    reasons_section = False
    risks_section = False
    table_section = False

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if "Ngày báo cáo" in line and ":" in line:
            data["report_day"] = line.split(":")[1].strip()
        if "Tên công ty" in line and ":" in line:
            data["company_name"] = line.split(":")[1].strip()
        elif "Lĩnh vực hoạt động" in line and ":" in line:
            data["industry"] = line.split(":")[1].strip()
        elif "Kết quả hoạt động kinh doanh" in line:
            table_section = True
            continue
        elif table_section:
            if "|" in line:
                parts = [p.strip() for p in line.split("|")]
                if "Chỉ tiêu" in line:
                    years = parts[1:]
                else:
                    metric = parts[0]
                    values = [FinancialData(year=years[i], value=parts[i+1]) for i in range(len(years))]
                    data["financial_statement"].append(FinancialStatement(metric=metric, values=values))
            else:
                table_section = False

        elif "Dự báo doanh thu" in line:
            forecast_section = True
            reasons_section = False
            risks_section = False
        elif "Nguyên nhân" in line:
            forecast_section = False
            reasons_section = True
            risks_section = False
        elif "Rủi ro đầu tư" in line:
            forecast_section = False
            reasons_section = False
            risks_section = True
        elif "Giá mục tiêu" in line and ":" in line:
            data["target_price"] = line.split(":")[1].strip()
        elif forecast_section:
            data["forecast"] += line.strip() + " "
        elif reasons_section:
            data["reasons"].append(line.strip())
        elif risks_section:
            data["risks"].append(line.strip())
    
    data["forecast"] = data["forecast"].strip()
    
    return FinancialReport(**data)

def format_summary_to_json(summary_text: str) -> str:
    report = parse_summary_to_model(summary_text)
    return json.dumps(report.dict(), indent=4, ensure_ascii=False)

# Gửi dữ liệu đến Gemini để tóm tắt
def generate_summary(extracted_text, custom_prompt=None):
    prompt = """
    Trích xuất thông tin tài chính từ báo cáo PDF:
    - Ngày báo cáo (report_day) : Ngày cập nhập hoặc báo cáo của file
    - Tên công ty (company_name): Tên công ty
    - Lĩnh vực hoạt động (industry): Lĩnh vực hoạt động
    - Kết quả hoạt động kinh doanh (financial_statement): Dữ liệu dạng bảng bao gồm các chỉ tiêu tài chính theo  năm:
        + Doanh thu (revenue): Thu nhập lãi thuần
        + Tổng thu nhập hoạt động (operating_income): Tổng thu nhập hoạt động 
        + Lợi nhuận trước thuế (pre_tax_profit): Lợi nhuận trước thuế c
        + Lợi nhuận ròng (net_profit): Lợi nhuận sau thuế
        + Tăng trưởng LNST YoY (yoy_growth): Tăng trưởng lợi nhuận sau thuế so với cùng kỳ năm trước (nếu có).
        + Dự báo doanh thu (forecast): Dự báo về doanh thu
    - Nguyên nhân dự báo (reasons): Các yếu tố dẫn đến dự báo doanh thu.
    - Giá mục tiêu (target_price): Giá mục tiêu của cổ phiếu.
    - Rủi ro đầu tư (risks): Các rủi ro có thể ảnh hưởng đến hoạt động kinh doanh 
    🔹 Yêu cầu:
    - Thông tin phải được trích xuất chính xác, không suy diễn
    - Dữ liệu bảng có tiêu đề là "Kết quả hoạt động kinh doanh".
    - Nếu một chỉ tiêu tài chính không có trong dữ liệu, có thể bỏ qua 
    - Trả lời bằng tiếng Việt.
    - Nếu báo cáo có đánh giá về triển vọng hoặc tiềm năng tăng giá, hãy tóm tắt ngắn gọn
    - Xử lý đúng các từ viết tắt, tránh sót dữ liệu quan trọng.
    - Không Ra dạng Json
    - Tiêu đề của mỗi phần luôn phải đi kèm từ tiếng Anh 
    """
    
    if custom_prompt:
        prompt = custom_prompt  # Nếu có prompt tùy chỉnh, thay thế prompt mặc định
    
    response = model.generate_content([{"text": extracted_text}, {"text": prompt}])
    return response.text
# Phương thức trích xuất văn bản từ PDF
def extract_text_from_pdf(pdf_path):
    text_results = []
    
    # Mở PDF bằng PyMuPDF
    doc = fitz.open(pdf_path)
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text")  # Thử trích xuất văn bản

        if text.strip():  # Nếu có văn bản, lưu lại
            text_results.append(text)
        else:  # Nếu không, dùng OCR
            images = convert_from_path(pdf_path, first_page=page_num + 1, last_page=page_num + 1)
            for img in images:
                text_ocr = pytesseract.image_to_string(img, lang="vie")  # Hoặc "vie" nếu là tiếng Việt
                text_results.append(text_ocr)
    
    return "\n".join(text_results)
def clean_ocr_text(text):
    # Chuẩn hóa số và loại bỏ ký tự thừa
    text = re.sub(r'[^\w\s.,:-]', '', text)  # Chỉ giữ chữ, số, dấu câu cơ bản
    text = re.sub(r'\s+', ' ', text)  # Loại bỏ khoảng trắng thừa
    return text.strip()

# Giao diện Streamlit
st.title("📄 Trích xuất và phân tích báo cáo tài chính từ PDF")

# Khởi tạo session state cho summary và summary_json
if "summary" not in st.session_state:
    st.session_state.summary = ""
if "summary_json" not in st.session_state:
    st.session_state.summary_json = ""

uploaded_file = st.file_uploader("📂 Tải lên file PDF", type=["pdf"])

if uploaded_file is not None:
    st.write("✅ File đã được tải lên thành công!")
    pdf_bytes = uploaded_file.read()
# Mở PDF
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    full_text = []
    use_ocr = False  # Mặc định không dùng OCR
    
    for page in doc:
        text = page.get_text("text")
        if text.strip():
            full_text.append(text)
        else:
            use_ocr = True
            break  # Nếu có trang rỗng thì dùng OCR
    doc.close()
    
    # Nếu PDF có thể đọc được
    if not use_ocr:
        extracted_text = "\n".join(full_text)
        method_used = "Fitz (PyMuPDF)"
    else:
        images = convert_from_bytes(pdf_bytes)
        extracted_text = "\n".join([clean_ocr_text(pytesseract.image_to_string(img, lang="vie")) for img in images])       
        method_used = "Tesseract OCR"
    
    st.write(f"🔍 **Phương pháp trích xuất:** {method_used}")
    
    with st.expander("📜 Xem nội dung trích xuất"):
        st.text_area("Nội dung PDF", extracted_text, height=300)

    # Thêm tab ở Sidebar để nhập và lưu prompt
    st.sidebar.title("⚙️ Prompt Tùy Chỉnh")

    # Lấy và lưu prompt trong session state
    if "saved_prompt" not in st.session_state:
        st.session_state.saved_prompt = ""  # Nếu chưa có prompt đã lưu, khởi tạo là rỗng
    
    # Nhập hoặc thay đổi prompt tùy chỉnh
    custom_prompt = st.sidebar.text_area("Nhập Prompt", height=200, placeholder="Nhập prompt tùy chỉnh của bạn ở đây...", value=st.session_state.saved_prompt)
    
    # Lưu prompt vào session_state khi nhấn lưu
    if st.sidebar.button("Lưu Prompt"):
        st.session_state.saved_prompt = custom_prompt  # Lưu prompt vào session state
    
    # Sử dụng prompt đã lưu nếu có
    prompt_to_use = st.session_state.saved_prompt

    # Gửi dữ liệu đến Gemini nếu chưa có kết quả phân tích
    if st.button("🚀 Phân tích bằng Gemini"):
        st.write("⏳ Đang xử lý...")
        start_time = time.time()
        summary = generate_summary(extracted_text, prompt_to_use)
        end_time = time.time()
        st.session_state.summary = summary
        st.session_state.summary_json = format_summary_to_json(summary)
        
        st.write("✅ **Phân tích hoàn tất!**")
        input_tokens = model.count_tokens(extracted_text)  # Đếm số token của văn bản đầu vào
        output_tokens = model.count_tokens(summary)  # Thay summary.text thành summary
        
        # Hiển thị kết quả
        st.write(summary)
        st.write(f"⏳ *Thời gian xử lý:* {end_time - start_time:.2f} giây")
        st.write(f"⏳ *Input Token:* {input_tokens}")
        st.write(f"⏳ *Output Token:* {output_tokens}")

        summary_json = format_summary_to_json(summary)
        st.write("🗂 **Kết quả dạng JSON:**")
        st.json(summary_json)  # Hiển thị dữ liệu JSON trong Streamlit
        file_name = uploaded_file.name
        insert_ocr_txt(file_name, extracted_text, summary)
        insert_ocr_json(file_name, summary_json)
    else:
        st.write("✅ **Phân tích hoàn tất!**")
        st.write(st.session_state.summary)
        st.write("🗂 **Kết quả dạng JSON:**")
        st.json(st.session_state.summary_json)
        
    # Tải xuống TXT & JSON
    st.download_button("📥 Tải kết quả TXT", st.session_state.summary, file_name="gemini_output.txt", mime="text/plain")
    st.download_button("📥 Tải kết quả JSON", st.session_state.summary_json, file_name="gemini_output.json", mime="application/json")
