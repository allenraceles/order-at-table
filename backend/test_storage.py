"""Focused checks for staff image upload validation."""

import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend.app.main import upload_image


class RequestStub:
    def __init__(self, body: bytes, content_type: str):
        self._body = body
        self.headers = {"content-type": content_type, "content-length": str(len(body))}

    async def body(self):
        return self._body


class StorageUploadTests(unittest.TestCase):
    def test_valid_webp_is_uploaded_to_the_requested_folder(self):
        body = b"RIFF" + (4).to_bytes(4, "little") + b"WEBPtest"
        with patch("backend.app.main.storage_request") as request:
            with patch("backend.app.main.storage_config", return_value=("https://ftdzbagcesvujvrbaqdl.supabase.co", "secret")):
                result = asyncio.run(upload_image(RequestStub(body, "image/webp"), "menu"))
        self.assertEqual(result["bytes"], len(body))
        self.assertIn("/restaurant-media/menu/", result["url"])
        self.assertTrue(result["url"].endswith(".webp"))
        self.assertEqual(request.call_args.kwargs["content_type"], "image/webp")

    def test_non_image_upload_is_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(upload_image(RequestStub(b"plain text", "text/plain"), "logo"))
        self.assertEqual(raised.exception.status_code, 415)

    def test_spoofed_image_mime_type_is_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(upload_image(RequestStub(b"not a real image", "image/png"), "menu"))
        self.assertEqual(raised.exception.status_code, 415)


if __name__ == "__main__":
    unittest.main()
