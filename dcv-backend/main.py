import filetype
from typing import Annotated
from pathlib import Path
from uuid import uuid4
from fastapi.openapi.utils import get_openapi
from fastapi import FastAPI, File, HTTPException, UploadFile
from database import getDbConnection

app = FastAPI(title="Document Intelligence API")

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILES = 20
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_BATCH_SIZE = 200 * 1024 * 1024  # 200 MB

ALLOWED_TYPES = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}


@app.get("/")
def home():
    return {"message": "Document Intelligence API is running"}



def validate_file_content(file_path: Path, expected_type: str) -> bool:
    # filetype.guess() examines the binary signature of the file
    detected_type = filetype.guess(str(file_path))

    if detected_type is None:
        return False

    return detected_type.mime == expected_type


@app.post("/documents/upload")
async def upload_documents(
    files: list[UploadFile] = File(
    description="Select PDF or image files"
)
):
    if not files:
        raise HTTPException(
            status_code=400,
            detail="No files provided",
        )

    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_FILES} files allowed per request",
        )

    saved_files = []
    created_paths = []
    total_size = 0

    connection = None
    cursor = None

    try:
        connection = getDbConnection()
        cursor = connection.cursor()
        for file in files:
            extension = ALLOWED_TYPES.get(file.content_type)

            if extension is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported file type: {file.filename}",
                )

            file_size = 0
            stored_name = f"{uuid4().hex}{extension}"
            destination = UPLOAD_DIR / stored_name

            # Track the path before writing, so partial files
            # can also be deleted if an error occurs.
            created_paths.append(destination)

            with destination.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    file_size += len(chunk)
                    total_size += len(chunk)

                    if file_size > MAX_FILE_SIZE:
                        raise HTTPException(
                            status_code=413,
                            detail=f"{file.filename} exceeds the 10 MB limit",
                        )

                    if total_size > MAX_BATCH_SIZE:
                        raise HTTPException(
                            status_code=413,
                            detail="Batch exceeds the 200 MB limit",
                        )

                    output.write(chunk)

            if file_size == 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"{file.filename} is empty",
                )
            if not validate_file_content(destination, file.content_type):
                raise HTTPException(
                    status_code=400,
                    detail=f"File contents do not match the claimed type: {file.filename}"
                )
            cursor.execute(
                """
                INSERT INTO documents (
                    original_filename,
                    stored_filename,
                    status
                )
                VALUES (%s, %s, %s)
                """,
                (file.filename, stored_name, "uploaded"),
            )

            document_id = cursor.lastrowid
            saved_files.append({
                "document_id": document_id,
                "original_filename": file.filename,
                "stored_filename": stored_name,
                "size_bytes": file_size,
                "content_type": file.content_type,
            })

        connection.commit()
        
        return {
            "message": "Documents uploaded successfully",
            "count": len(saved_files),
            "total_size_bytes": total_size,
            "documents": saved_files,
        }

    except Exception:
        if connection is not None and connection.is_connected():
            connection.rollback()

        # Remove every file created during this batch,
        # including a partially written current file.
        for path in created_paths:
            path.unlink(missing_ok=True)

        # Preserve the original exception and HTTP status.
        raise

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None and connection.is_connected():
            connection.close()


        for file in files:
            await file.close()
        

# this code below shows the file picker option instead of inputting some text string.

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )

    schemas = schema.get("components", {}).get("schemas", {})

    for model in schemas.values():
        for prop in model.get("properties", {}).values():

            if prop.get("contentMediaType") == "application/octet-stream":
                prop.pop("contentMediaType", None)
                prop["format"] = "binary"

            items = prop.get("items", {})
            if items.get("contentMediaType") == "application/octet-stream":
                items.pop("contentMediaType", None)
                items["format"] = "binary"

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi



