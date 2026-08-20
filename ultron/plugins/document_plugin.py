import os

def read_document(file_path: str, page: int = None) -> str:
    """Reads a PDF, DOCX, or image file. For images (PNG, JPG, BMP, TIFF, WEBP), extracts text using OCR.
    If the document is long, you MUST provide a page number (1-indexed) to read it piece by piece."""
    if not os.path.exists(file_path):
        return f"Error: File '{file_path}' not found."

    ext = os.path.splitext(file_path)[1].lower()

    _image_exts = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}

    try:
        if ext == ".pdf":
            return _read_pdf(file_path, page)
        elif ext == ".docx":
            return _read_docx(file_path, page)
        elif ext in _image_exts:
            return _read_image(file_path)
        else:
            return f"Error: Unsupported file format '{ext}'. Supported: .pdf, .docx, .png, .jpg, .jpeg, .bmp, .tiff, .webp"
    except Exception as e:
        return f"Error reading document: {e}"

def _read_pdf(file_path: str, page: int = None) -> str:
    try:
        import pypdf
    except ImportError:
        return "Error: pypdf library is not installed. Please run: pip install pypdf"

    with open(file_path, 'rb') as f:
        reader = pypdf.PdfReader(f)
        num_pages = len(reader.pages)
        
        if num_pages == 0:
            return "Document is empty."

        if page is not None:
            if page < 1 or page > num_pages:
                return f"Error: Invalid page number. The document has {num_pages} pages."
            
            page_obj = reader.pages[page - 1]
            text = page_obj.extract_text()
            return f"--- Page {page} of {num_pages} ---\n\n{text}"
            
        else:
            # If no page provided, and document is short, read everything.
            if num_pages <= 3:
                full_text = []
                for i in range(num_pages):
                    text = reader.pages[i].extract_text()
                    full_text.append(f"--- Page {i + 1} ---\n{text}")
                return "\n\n".join(full_text)
            else:
                # Document is too long, return pagination summary
                return (f"The PDF document '{os.path.basename(file_path)}' is long ({num_pages} pages). "
                        f"To read it, please call the tool again and provide a specific page number, e.g., page=1.")

def _read_docx(file_path: str, page: int = None) -> str:
    try:
        import docx
    except ImportError:
        return "Error: python-docx library is not installed. Please run: pip install python-docx"

    doc = docx.Document(file_path)
    full_text = "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
    
    if not full_text.strip():
        return "Document is empty."

    # Chunk the text into roughly 4000 character segments
    chunk_size = 4000
    chunks = [full_text[i:i+chunk_size] for i in range(0, len(full_text), chunk_size)]
    num_chunks = len(chunks)

    if page is not None:
        if page < 1 or page > num_chunks:
            return f"Error: Invalid page number. The document has {num_chunks} pages/chunks."
        
        text = chunks[page - 1]
        return f"--- Page/Chunk {page} of {num_chunks} ---\n\n{text}"
        
    else:
        # If no page provided, and document is short, read everything.
        if num_chunks <= 3:
            return full_text
        else:
            # Document is too long, return pagination summary
            return (f"The DOCX document '{os.path.basename(file_path)}' is long (divided into {num_chunks} chunks). "
                    f"To read it, please call the tool again and provide a specific page number, e.g., page=1.")

# Where Tesseract ends up on Windows. The installer does not put it on PATH,
# and pytesseract only looks there — so a perfectly good installation reports
# itself as missing.
TESSERACT_LOCATIONS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""),
                 "Programs", "Tesseract-OCR", "tesseract.exe"),
]


def find_tesseract() -> str:
    """The Tesseract executable, or "" if there is genuinely none.

    PATH first, so a deliberate choice of build wins, then the usual install
    locations. Looking these up costs nothing and saves the user editing
    environment variables to make a feature work that is already installed.
    """
    import shutil

    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in TESSERACT_LOCATIONS:
        if candidate and os.path.isfile(candidate):
            return candidate
    return ""


def _read_image(file_path: str) -> str:
    """Extract text from an image file using Tesseract OCR."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return "Error: pytesseract or Pillow is not installed. Please run: pip install pytesseract pillow"

    executable = find_tesseract()
    if not executable:
        return ("Error: Tesseract OCR is not installed, so I cannot read text "
                "out of an image. Install it with: "
                "winget install UB-Mannheim.TesseractOCR")
    pytesseract.pytesseract.tesseract_cmd = executable

    img = Image.open(file_path)
    text = pytesseract.image_to_string(img)

    if not text.strip():
        return f"No text detected in image '{os.path.basename(file_path)}'."

    return f"--- Text extracted from '{os.path.basename(file_path)}' ---\n\n{text.strip()}"
