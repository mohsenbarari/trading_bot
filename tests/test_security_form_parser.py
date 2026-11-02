"""Small streamed inputs verify the installed upstream form-parser fixes."""

import unittest

from fastapi import FastAPI, File, Form, UploadFile
from httpx import ASGITransport, AsyncClient
from starlette.exceptions import HTTPException
from starlette.requests import Request


class FormParserSecurityTests(unittest.IsolatedAsyncioTestCase):
    def _request(self, content_type: bytes, chunks: list[bytes]) -> Request:
        pending = iter(enumerate(chunks))

        async def receive():
            index, data = next(pending)
            return {"type": "http.request", "body": data, "more_body": index < len(chunks) - 1}

        return Request(
            {
                "type": "http", "method": "POST", "path": "/", "root_path": "",
                "query_string": b"", "headers": [(b"content-type", content_type)],
                # Request converts parser errors to HTTP 400 inside an ASGI app.
                "app": object(),
            },
            receive,
        )

    async def test_urlencoded_field_count_limit_is_not_silently_ignored(self):
        request = self._request(b"application/x-www-form-urlencoded", [b"a=1&", b"b=2&c=3"])
        with self.assertRaises(HTTPException) as caught:
            await request.form(max_fields=2)
        self.assertEqual(caught.exception.status_code, 400)

    async def test_urlencoded_streamed_field_size_limit_is_not_ignored(self):
        request = self._request(b"application/x-www-form-urlencoded", [b"field=1234", b"567890123"])
        with self.assertRaises(HTTPException) as caught:
            await request.form(max_part_size=8)
        self.assertEqual(caught.exception.status_code, 400)

    async def test_urlencoded_field_name_size_is_also_bounded(self):
        request = self._request(b"application/x-www-form-urlencoded", [b"verylong", b"fieldname=x"])
        with self.assertRaises(HTTPException) as caught:
            await request.form(max_part_size=8)
        self.assertEqual(caught.exception.status_code, 400)

    async def test_multipart_non_file_field_size_is_bounded(self):
        request = self._request(
            b"multipart/form-data; boundary=test-boundary",
            [b'--test-boundary\r\nContent-Disposition: form-data; name="caption"\r\n\r\n',
             b"123456789\r\n--test-boundary--\r\n"],
        )
        with self.assertRaises(HTTPException) as caught:
            await request.form(max_part_size=8)
        self.assertEqual(caught.exception.status_code, 400)

    async def test_fastapi_form_and_file_route_still_accepts_normal_upload(self):
        app = FastAPI()

        @app.post("/upload")
        async def upload(file: UploadFile = File(...), caption: str = Form(...)):
            return {"caption": caption, "name": file.filename, "size": len(await file.read())}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://unit-test") as client:
            response = await client.post("/upload", data={"caption": "test"}, files={"file": ("test.txt", b"abc")})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"caption": "test", "name": "test.txt", "size": 3})

    async def test_fastapi_default_parser_rejects_too_many_fields_before_handler(self):
        app = FastAPI()
        reached_handler = False

        @app.post("/form")
        async def form(caption: str = Form(...)):
            nonlocal reached_handler
            reached_handler = True
            return {"caption": caption}

        body = "caption=test&" + "&".join(f"field{i}=x" for i in range(1001))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://unit-test") as client:
            response = await client.post("/form", content=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(reached_handler)
