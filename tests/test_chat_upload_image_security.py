"""Bounded, in-memory regressions for chat-image decompression handling.

Run with APP_ENV_FILE=config/unit-test.env.example and the test network fence.
No database, upload directory, real user image or large allocation is needed.
"""

import io
import struct
import unittest
import zlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from PIL import Image, ImageOps

from core.services import chat_upload_session_service as service


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _small_apng_with_larger_canvas() -> bytes:
    """Small canvas + lower test limit exercise the real APNG allocation order."""
    return b"\x89PNG\r\n\x1a\n" + b"".join(
        [
            _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 256, 256, 8, 6, 0, 0, 0)),
            _png_chunk(b"acTL", struct.pack(">II", 2, 0)),
            _png_chunk(b"fcTL", struct.pack(">IIIIIHHBB", 0, 1, 1, 0, 0, 1, 10, 1, 0)),
            _png_chunk(b"IDAT", zlib.compress(b"\x00" * 5)),
            _png_chunk(b"IEND", b""),
        ]
    )


class ChatUploadImageSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def _persist(self, contents: bytes, mime: str = "image/png"):
        db = SimpleNamespace(add=Mock(), flush=AsyncMock())
        file_handle = AsyncMock()
        file_handle.__aenter__.return_value = file_handle
        with (
            patch.object(service.magic, "from_buffer", return_value=mime),
            patch.object(service, "_ensure_directory", new=AsyncMock()) as mkdir,
            patch.object(service.aiofiles, "open", return_value=file_handle) as file_open,
        ):
            try:
                result = await service.persist_chat_media_file_bytes(
                    db,
                    uploader_id=1,
                    file_name="test-image.png",
                    declared_content_type=mime,
                    contents=contents,
                )
            except HTTPException:
                mkdir.assert_not_awaited()
                file_open.assert_not_called()
                db.add.assert_not_called()
                raise
        return result, file_handle.write.await_args.args[0]

    async def test_apng_is_rejected_before_canvas_fill_and_never_saved_original(self):
        # Even an accidentally old Pillow cannot allocate a large buffer in this
        # regression: the fixture is tiny and core.fill is replaced entirely.
        with (
            patch.object(service, "CHAT_MEDIA_MAX_IMAGE_PIXELS", 63),
            patch.object(Image, "open", wraps=Image.open) as image_open,
            patch.object(Image.core, "fill", side_effect=AssertionError("early canvas allocation")) as fill,
            patch.object(service.logger, "warning") as fallback_warning,
        ):
            with self.assertRaises(HTTPException) as caught:
                await self._persist(_small_apng_with_larger_canvas())
        self.assertEqual(caught.exception.status_code, 413)
        image_open.assert_not_called()
        fill.assert_not_called()
        fallback_warning.assert_not_called()

    async def test_upstream_apng_bomb_check_also_precedes_canvas_allocation(self):
        with (
            patch.object(service, "CHAT_MEDIA_MAX_IMAGE_PIXELS", 256 * 256),
            patch.object(Image, "MAX_IMAGE_PIXELS", 64),
            patch.object(Image.core, "fill", side_effect=AssertionError("early canvas allocation")) as fill,
        ):
            with self.assertRaises(HTTPException) as caught:
                await self._persist(_small_apng_with_larger_canvas())
        self.assertEqual(caught.exception.status_code, 413)
        fill.assert_not_called()

    async def test_noncanonical_png_headers_still_hit_post_open_pixel_limit(self):
        # Pillow tolerates noncanonical IHDR placement/repetition. The 24-byte
        # optimization is not an authority: pinned Pillow13 + the Image.size
        # check must still reject these before EXIF, decoding or persistence.
        canonical = _small_apng_with_larger_canvas()
        extras = (
            _png_chunk(b"tEXt", b"note\x00test"),
            _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)),
        )
        for extra in extras:
            contents = canonical[:8] + extra + canonical[8:]
            with (
                self.subTest(chunk_type=extra[4:8]),
                patch.object(service, "CHAT_MEDIA_MAX_IMAGE_PIXELS", 63),
                patch.object(ImageOps, "exif_transpose") as transpose,
                patch.object(Image.core, "fill", side_effect=AssertionError("early canvas allocation")) as fill,
            ):
                with self.assertRaises(HTTPException) as caught:
                    await self._persist(contents)
                self.assertEqual(caught.exception.status_code, 413)
                transpose.assert_not_called()
                fill.assert_not_called()

    async def test_pixel_limit_is_checked_before_exif_decode(self):
        with io.BytesIO() as output:
            with Image.new("RGB", (8, 8)) as image:
                image.save(output, format="JPEG")
            contents = output.getvalue()
        with (
            patch.object(service, "CHAT_MEDIA_MAX_IMAGE_PIXELS", 63),
            patch.object(ImageOps, "exif_transpose") as transpose,
        ):
            with self.assertRaises(HTTPException) as caught:
                await self._persist(contents, "image/jpeg")
        self.assertEqual(caught.exception.status_code, 413)
        transpose.assert_not_called()

    async def test_normal_jpeg_keeps_exif_orientation_behavior(self):
        with io.BytesIO() as output:
            with Image.new("RGB", (3, 2), "red") as image:
                exif = image.getexif()
                exif[274] = 6
                image.save(output, format="JPEG", exif=exif)
            contents = output.getvalue()
        result, saved = await self._persist(contents, "image/jpeg")
        self.assertEqual((result.width, result.height), (2, 3))
        self.assertEqual(result.mime_type, "image/jpeg")
        self.assertEqual(result.size, len(saved))
        with Image.open(io.BytesIO(saved)) as image:
            self.assertEqual(image.size, (2, 3))
            self.assertNotEqual(image.getexif().get(274), 6)

    async def test_normal_png_preserves_pixels_and_output_type(self):
        with io.BytesIO() as output:
            with Image.new("RGBA", (4, 3), (10, 20, 30, 40)) as image:
                image.save(output, format="PNG")
            contents = output.getvalue()
        result, saved = await self._persist(contents)
        self.assertEqual((result.width, result.height), (4, 3))
        self.assertEqual(result.mime_type, "image/png")
        with Image.open(io.BytesIO(saved)) as image:
            self.assertEqual(image.getpixel((0, 0)), (10, 20, 30, 40))

    async def test_oversized_bytes_rejected_before_magic_or_image_processing(self):
        with (
            patch.object(service, "CHAT_MEDIA_MAX_UPLOAD_BYTES", 2),
            patch.object(service.magic, "from_buffer") as sniff,
        ):
            with self.assertRaises(HTTPException) as caught:
                await service.persist_chat_media_file_bytes(
                    Mock(), uploader_id=1, file_name="x.png",
                    declared_content_type="image/png", contents=b"123",
                )
        self.assertEqual(caught.exception.status_code, 413)
        sniff.assert_not_called()

    async def test_reencoding_cannot_bypass_stored_byte_limit(self):
        async def worker(fn, *args, **kwargs):
            if fn.__name__ == "_exif_transpose_sync":
                return b"1234", 1, 1, "image/png", "png"
            return fn(*args, **kwargs)

        with (
            patch.object(service, "CHAT_MEDIA_MAX_UPLOAD_BYTES", 3),
            patch.object(service.asyncio, "to_thread", side_effect=worker),
        ):
            with self.assertRaises(HTTPException) as caught:
                await self._persist(b"1")
        self.assertEqual(caught.exception.status_code, 413)
