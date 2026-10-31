
from pathlib import Path
import tempfile

import fitz
import pypdfium2 as pdfium
from paddleocr import PaddleOCR


# Initialize PaddleOCR once and reuse it across requests
ocr = PaddleOCR(
    use_textline_orientation=True,
    lang="en"
)


def validate_pdf_text(text: str) -> bool:
    """
    Check whether extracted PDF text is usable.

    This is a basic quality check, not a guarantee
    that every extracted character is correct.
    """

    if not text or not text.strip():
        return False

    alphanumeric_count = sum(
        character.isalnum() for character in text
    )

    total_characters = len(text.strip())

    alphanumeric_ratio = alphanumeric_count / total_characters

    if alphanumeric_count < 20:
        return False

    if alphanumeric_ratio < 0.25:
        return False

    return True


def extract_pdf_text(file_path: Path) -> tuple[str, int]:
    """
    Extract embedded PDF text page by page using PyMuPDF.
    """

    extracted_pages = []

    with fitz.open(file_path) as pdf:
        total_pages = len(pdf)

        for page_number, page in enumerate(pdf, start=1):
            text = page.get_text("text", sort=True).strip()

            extracted_pages.append(
                f"Page {page_number}\n{text}"
            )

    return "\n\n".join(extracted_pages).strip(), total_pages


def extract_paddle_text(image_path: Path) -> str:
    """
    Extract text from an image using PaddleOCR.
    """

    results = ocr.predict(input=str(image_path))

    extracted_text = []

    for result in results:
        data = result.json

        if isinstance(data, dict):
            data = data.get("res", data)

        texts = (
            data.get("rec_texts", [])
            if isinstance(data, dict)
            else []
        )

        extracted_text.extend(texts)

    return "\n".join(extracted_text).strip()


def extract_pdf_with_ocr(file_path: Path) -> str:
    """
    Render PDF pages and extract text using PaddleOCR.
    """

    extracted_pages = []

    pdf = pdfium.PdfDocument(str(file_path))

    try:
        with tempfile.TemporaryDirectory() as temp_dir:

            for page_index in range(len(pdf)):
                page = pdf[page_index]
                bitmap = page.render(scale=2)
                image = bitmap.to_pil()

                try:
                    image_path = (
                        Path(temp_dir) /
                        f"page_{page_index + 1}.png"
                    )

                    image.save(image_path)

                    text = extract_paddle_text(image_path)

                    extracted_pages.append(
                        f"Page {page_index + 1}\n{text}"
                    )

                finally:
                    image.close()
                    page.close()
                    bitmap.close()

    finally:
        pdf.close()

    return "\n\n".join(extracted_pages).strip()


def extract_text_from_document(file_path: str) -> str:
    """
    Main document extraction function.

    Text-based PDFs: PyMuPDF
    Scanned/unusable PDF pages: PaddleOCR
    Images: PaddleOCR
    """

    path = Path(file_path)
    extension = path.suffix.lower()

    # 1. Image documents
    if extension in (".png", ".jpg", ".jpeg"):
        print("Extracted using PaddleOCR")
        return extract_paddle_text(path)

    # 2. PDF documents
    if extension == ".pdf":

        extracted_pages = []

        with fitz.open(path) as pdf:
            total_pages = len(pdf)

            # Keep a copy of the native text for each page
            for page_number, page in enumerate(pdf, start=1):

                text = page.get_text(
                    "text",
                    sort=True
                ).strip()

                if validate_pdf_text(text):
                    print("Extracted using PyMuPDF")
                    extracted_pages.append(
                        f"Page {page_number}\n{text}"
                    )

                else:
                    # Mark this page for OCR fallback
                    extracted_pages.append(None)

        # Check whether every page has usable native text
        if all(page is not None for page in extracted_pages):
            print("All pages successfully extracted using PyMuPDF.")
            return "\n\n".join(extracted_pages).strip()

        # 3. OCR only the pages with unusable native text
        pdf = pdfium.PdfDocument(str(path))

        try:
            with tempfile.TemporaryDirectory() as temp_dir:

                for page_index, text in enumerate(extracted_pages):

                    if text is not None:
                        continue
                    print(f"Page {page_index + 1}: Extracted using PaddleOCR")

                    page = pdf[page_index]
                    bitmap = page.render(scale=2)
                    image = bitmap.to_pil()

                    try:
                        image_path = (
                            Path(temp_dir) /
                            f"page_{page_index + 1}.png"
                        )

                        image.save(image_path)

                        ocr_text = extract_paddle_text(image_path)

                        extracted_pages[page_index] = (
                            f"Page {page_index + 1}\n{ocr_text}"
                        )

                    finally:
                        image.close()
                        page.close()
                        bitmap.close()

        finally:
            pdf.close()

        return "\n\n".join(extracted_pages).strip()

    raise ValueError("Unsupported document format")
