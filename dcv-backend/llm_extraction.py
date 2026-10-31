import requests
import json

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "qwen2.5:3b"

def extract_invoice_fields(ocr_text:str) -> dict:
        prompt = f"""
            You are an information extraction system for invoices and receipts.

Extract the requested fields from the OCR text provided below.

Return exactly one valid JSON object with these keys:
- vendor_name
- invoice_number
- invoice_date
- total_amount

Extraction rules:

1. vendor_name:
   Identify the company or seller that issued the invoice.
   Look for the seller's name in the header, logo text, or issuer
   declaration near the bottom of the document.
   Do not use the customer's billing or shipping name as the vendor.
   If the vendor cannot be identified reliably, return null.

2. invoice_number:
   Extract the value explicitly associated with "Invoice No."
   Do not confuse it with the order number.

3. invoice_date:
   Extract the value associated with "Invoice date".
   Preserve the date format found in the OCR text.

4. total_amount:
   Extract the final payable invoice amount, not the unit price,
   subtotal, tax amount, or shipping charge.
   Prefer the value associated with "Total Invoice Amount".
   Do not calculate or invent a replacement amount.
   Return the amount as a JSON number, without currency symbols
   or thousands separators.

5. If a field is missing or cannot be identified reliably, use null.
6. Do not invent information.
7. Return JSON only, without Markdown or explanations.

OCR text:
{ocr_text}
"""


        response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0
            }
        },
        timeout=(10,300)
        )

        response.raise_for_status()
        result = response.json()
        extracted_fields = json.loads(result["response"])

        return extracted_fields