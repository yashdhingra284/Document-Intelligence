from pathlib import Path
from fastapi import FastAPI, File, HTTPException, UploadFile
from io import BytesIO
import filetype
from PIL import Image
from pypdf import PdfReader
from uuid import uuid4
from database import getDbConnection

# Upload configuration
UPLOAD_DIR = Path("uploads")

MAX_FILES = 20
MAX_FILE_SIZE = 10 * 1024 * 1024       # 10 MiB per file
MAX_BATCH_SIZE = 200 * 1024 * 1024     # 200 MiB per batch

# Supported MIME types and their storage extensions
ALLOWED_TYPES = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}

# Create the permanent storage directory if it doesn't exist.
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Document Classifier Validator API",
    description="API for uploading and validating documents.",
    version="1.0.0",
)

def validate_file_signature(file_bytes: bytes, expected_mime: str) -> bool:
    detected_type = filetype.guess(file_bytes)

    if detected_type is None:
        return False

    return detected_type.mime == expected_mime


def validate_file_structure(file_bytes: bytes, expected_mime: str) -> bool:
    try:
        if expected_mime == "application/pdf":
            pdf = PdfReader(BytesIO(file_bytes), strict=True)

            if pdf.is_encrypted:
                return False

            # Attempt to read the PDF page tree.
            _ = len(pdf.pages)

            return True

        if expected_mime in ("image/png", "image/jpeg"):
            expected_format = {
                "image/png": "PNG",
                "image/jpeg": "JPEG",
            }

            with Image.open(BytesIO(file_bytes)) as image:
                if image.format != expected_format[expected_mime]:
                    return False

                image.verify()

            return True

        return False

    except Exception:
        return False


@app.get("/")
def home():
    return {
        "message": "Document Classifier Validator API is running"
    }

@app.post("/documents/upload")
async def upload_documents(
    files: list[UploadFile] = File(
        description="Upload PDF, PNG, or JPEG documents"
    )
):
    # 1. Validate the number of files.
    if not files:
        raise HTTPException(
            status_code=400,
            detail="No files provided."
        )

    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_FILES} files allowed per request."
        )

    accepted_docs = []
    rejected_docs = []

    total_bytes_read = 0
    batch_limit_reached = False

    connection = None
    cursor = None

    try:
        # 2. Connect to MySQL.
        connection = getDbConnection()
        cursor = connection.cursor()

        # 3. Process every file independently.
        for index, file in enumerate(files):
            file_bytes = bytearray()
            file_size = 0
            rejection_reason = None
            destination = None

            try:
                # Check the claimed MIME type.
                expected_mime = file.content_type
                extension = ALLOWED_TYPES.get(expected_mime)

                if extension is None:
                    rejection_reason = "Unsupported file type."

                elif batch_limit_reached:
                    rejection_reason = "Batch size limit exceeded."
                
                else:
                    # Read incrementally and enforce size limits.
                    while True:
                        chunk = await file.read(64 * 1024)

                        if not chunk:
                            break

                        file_size += len(chunk)
                        total_bytes_read += len(chunk)

                        if file_size > MAX_FILE_SIZE:
                            rejection_reason = (
                                "File exceeds the 10 MiB limit."
                            )
                            break

                        if total_bytes_read > MAX_BATCH_SIZE:
                            rejection_reason = (
                                "Batch exceeds the 200 MiB limit."
                            )
                            batch_limit_reached = True
                            break

                        file_bytes.extend(chunk)

                    if rejection_reason is None and file_size == 0:
                        rejection_reason = "Empty file"
                    
                    # Validate the binary signature in memory.
                    if rejection_reason is None:
                        if not validate_file_signature(
                            bytes(file_bytes),
                            expected_mime
                        ):
                            rejection_reason = (
                                "File signature does not match "
                                "the claimed MIME type."
                            )
                    
                    # Validate the actual file structure in memory.
                    if rejection_reason is None:
                        if not validate_file_structure(
                            bytes(file_bytes),
                            expected_mime
                        ):
                            rejection_reason = (
                                "File structure is invalid or unreadable."
                            )

                    # Only validated files reach permanent storage.
                    if rejection_reason is None:
                        stored_name = f"{uuid4().hex}{extension}"
                        destination = UPLOAD_DIR / stored_name

                        with destination.open("xb") as output:
                            output.write(file_bytes)

                        # Insert this document's metadata.
                        cursor.execute(
                            """
                            INSERT INTO documents (
                                original_filename,
                                stored_filename,
                                status
                            )
                            VALUES (%s, %s, %s)
                            """,
                            (
                                file.filename or "unnamed",
                                stored_name,
                                "uploaded",
                            ),
                        )
                        document_id = cursor.lastrowid

                        # Commit this document independently.
                        connection.commit()

                        accepted_docs.append({
                            "document_id": document_id,
                            "original_filename": file.filename,
                            "stored_filename": stored_name,
                            "size_bytes": file_size,
                            "status": "uploaded",
                        })
            
            except Exception as exc:
                # Remove only the current file if it was saved.
                if destination is not None:
                    try:
                        destination.unlink(missing_ok=True)
                    except OSError:
                        pass
                
                # Roll back the current database transaction if possible.
                if connection is not None:
                    try:
                        connection.rollback()
                    except Exception:
                        pass

                # Operational errors are not ordinary file rejections.
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "A storage or database error occurred. "
                        "Previously accepted documents were preserved "
                        "where possible."
                    ),
                ) from exc
            finally:
                file_bytes.clear()
            
            # Record an invalid file and continue with the next one.
            if rejection_reason is not None:
                rejected_docs.append({
                    "original_filename": file.filename,
                    "reason": rejection_reason,
                })

                # Once the batch limit is exceeded, reject remaining files.
                if batch_limit_reached:
                    for remaining_file in files[index + 1:]:
                        rejected_docs.append({
                            "original_filename": remaining_file.filename,
                            "reason": "Batch size limit exceeded.",
                        })
                    break
        return {
            "message": "Upload processing finished.",
            "accepted_count": len(accepted_docs),
            "rejected_count": len(rejected_docs),
            "accepted_docs": accepted_docs,
            "rejected_docs": rejected_docs,
        }
    finally:
        # Close every uploaded file, including files not processed.
        for file in files:
            await file.close()

        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()

@app.get("/documents")
def get_documents():
    connection = None
    cursor = None

    try:
        connection = getDbConnection()
        cursor = connection.cursor(dictionary = True)
        cursor.execute("""
            SELECT
                document_id,
                original_filename,
                stored_filename,
                uploaded_at,
                status
            FROM documents
            ORDER BY uploaded_at DESC
        """)

        documents = cursor.fetchall()
        # Convert datetime values into JSON-compatible strings.
        for document in documents:
            if document["uploaded_at"] is not None:
                document["uploaded_at"] = (
                    document["uploaded_at"].isoformat()
                )

        return {
            "count": len(documents),
            "documents": documents
        }
    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()

@app.get("/documents/{document_id}")
def get_document_by_id(document_id :int):
    connection = None
    cursor = None
    try:
        connection = getDbConnection()
        cursor = connection.cursor(dictionary = True)

        cursor.execute("""
            SELECT
            document_id,
            original_filename,
            stored_filename,
            uploaded_at,
            status
            FROM documents
            WHERE document_id = %s
            """,
            (document_id,))

        document = cursor.fetchone()
        if document is None:
            raise HTTPException(
                status_code = 404,
                detail = "Document Not Found"
            )

        if document["uploaded_at"] is not None:
            document["uploaded_at"] = (
                document["uploaded_at"].isoformat()
            )

        return document

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()
        