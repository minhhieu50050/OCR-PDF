import streamlit as st
import fitz  # PyMuPDF
import pytesseract
from pdf2image import convert_from_bytes
import google.generativeai as genai
import time
import pdfplumber  # Đừng quên import pdfplumber

# Cấu hình API Gemini
API_KEY = "AIzaSyBB6h6vrCjAM9CZNPWeovPueudVLnjjYR4"
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash")

# Đường dẫn đến Tesseract OCR
pytesseract.pytesseract.tesseract_cmd = r"C:\Users\hieu.ntm\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"

# Gửi dữ liệu đến Gemini để tóm tắt
def generate_summary(extracted_text, custom_prompt=None):
    prompt = """Hãy trích xuất các thông tin tài chính từ báo cáo PDF trên một cách chính xác và đầy đủ theo ngữ cảnh báo cáo. Nội dung cần đọc kỹ và tổng hợp sao cho dễ hiểu.
    🔹 **Các thông tin quan trọng cần trích xuất gồm:**
    - **Tên công ty** (Công ty này hoạt động trong lĩnh vực nào?)
    - **Doanh thu (DT)** theo từng năm
    - **Lợi nhuận sau thuế (LNST)** theo từng năm
    - **Lợi nhuận trước thuế (LNTT)** theo từng năm
    - **Lợi nhuận gộp (LNG)** theo từng năm
    - **%YoY** theo từng năm
    - **Dự báo doanh thu**
    🔹 **Yêu cầu:**
    - Thông tin trích xuất phải chính xác đầy đủ, không tự suy diễn hoặc chế nội dung.
    - Trình bày dưới dạng **bảng** nếu có nhiều năm.
    - Nội dung trả lời bằng **tiếng Việt**."""
    
    if custom_prompt:
        prompt = custom_prompt  # Nếu có prompt tùy chỉnh, thay thế prompt mặc định
    
    response = model.generate_content([
        {"text": extracted_text},
        {"text": prompt}
    ])
    return response.text

# Phương thức trích xuất văn bản từ PDF
def extract_text_from_pdf(pdf_bytes, method):
    if method == "Tesseract":
        images = convert_from_bytes(pdf_bytes)
        extracted_text = "\n".join([pytesseract.image_to_string(img, lang="eng+vie") for img in images])
    elif method == "PyMuPDF":
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        extracted_text = "\n".join([page.get_text("text") for page in doc if page.get_text("text").strip()])
        doc.close()
    elif method == "PDFPlumber":
        with pdfplumber.open(pdf_bytes) as pdf:
            extracted_text = "\n".join([page.extract_text() for page in pdf.pages])
    else:
        extracted_text = ""
    return extracted_text

# Giao diện Streamlit
st.title("📄 Trích xuất và phân tích báo cáo tài chính từ PDF")

uploaded_file = st.file_uploader("📂 Tải lên file PDF", type=["pdf"])

if uploaded_file is not None:
    st.write("✅ File đã được tải lên thành công!")
    pdf_bytes = uploaded_file.read()
    
    # Chọn phương pháp trích xuất
    method = st.selectbox("Chọn phương pháp trích xuất", ["Tesseract", "PyMuPDF", "PDFPlumber"])
    
    extracted_text = extract_text_from_pdf(pdf_bytes, method)
    
    # Hiển thị phương pháp trích xuất
    st.write(f"🔍 **Phương pháp trích xuất:** {method}")
    
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

    # Gửi dữ liệu đến Gemini
    if st.button("🚀 Phân tích bằng Gemini"):
        st.write("⏳ Đang xử lý...")
        start_time = time.time()
        summary = generate_summary(extracted_text, prompt_to_use)
        end_time = time.time()
        input_tokens = model.count_tokens(extracted_text)  # Đếm số token của văn bản đầu vào
        output_tokens = model.count_tokens(summary)  # Thay summary.text thành summary
        
        # Hiển thị kết quả
        st.write("✅ **Phân tích hoàn tất!**")
        st.write(summary)
        st.write(f"⏳ **Thời gian xử lý:** {end_time - start_time:.2f} giây")
        st.write(f"⏳ **Input Token:** {input_tokens}")
        st.write(f"⏳ **Output Token:** {output_tokens} ")
        
        # Lưu kết quả
        with open("gemini_output.txt", "w", encoding="utf-8") as f:
            f.write(summary)
        st.download_button(label="📥 Tải kết quả", data=summary, file_name="gemini_output.txt")
