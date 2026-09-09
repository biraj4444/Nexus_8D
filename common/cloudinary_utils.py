"""
Cloudinary helpers compatible with Cloudflare Python Workers.

Uses Cloudinary's HTTPS API through httpx (async) instead of the Cloudinary Python SDK.
Keeps the same function interface used by public_site and admin_site.

IMPORTANT: In Cloudflare Python Workers, only ASYNC httpx works.
The sync httpx.Client will silently fail because there is no socket layer
in the Pyodide runtime.  We use httpx.AsyncClient and bridge it into the
synchronous Flask/WSGI handler via pyodide.ffi.run_sync — the same
technique used in common/db.py for D1 calls.
"""

import base64
import hashlib
import hmac
import time
from urllib.parse import urlencode

import httpx

from .config import Config

# run_sync bridges async coroutines into the synchronous WSGI handler.
# This is the same mechanism used by common/db.py for D1 queries.
try:
    from pyodide.ffi import run_sync
except ImportError:
    run_sync = None


def _credentials():
    """Return configured Cloudinary credentials."""
    cloud_name = Config.CLOUDINARY_CLOUD_NAME
    api_key = Config.CLOUDINARY_API_KEY
    api_secret = Config.CLOUDINARY_API_SECRET

    if not cloud_name or not api_key or not api_secret:
        return None

    return cloud_name, api_key, api_secret


def _signature(params: dict, api_secret: str) -> str:
    """
    Create Cloudinary API signature.

    Cloudinary's algorithm is NOT HMAC. Per Cloudinary's docs, you
    concatenate the sorted "key=value&..." parameter string with the
    raw api_secret appended at the end (no separator), then take a
    plain SHA-1 (or SHA-256) digest of that combined string.
    """
    filtered = {
        str(k): str(v)
        for k, v in params.items()
        if v is not None and v != ""
    }

    payload = "&".join(
        f"{key}={filtered[key]}"
        for key in sorted(filtered)
    )

    to_sign = (payload + api_secret).encode("utf-8")

    return hashlib.sha1(to_sign).hexdigest()


def _resource_type(resource_type: str) -> str:
    """Convert application type to Cloudinary resource type."""
    return {
        "image": "image",
        "video": "video",
        "file": "raw",
        "link": "raw",
    }.get(resource_type, "auto")


def _upload_endpoint(cloud_name: str, resource_type: str) -> str:
    return (
        f"https://api.cloudinary.com/v1_1/"
        f"{cloud_name}/{resource_type}/upload"
    )


# ─────────────────────────────────────────────────────────────────────────
# Async helpers — these run on the Workers fetch() transport
# ─────────────────────────────────────────────────────────────────────────

async def _async_upload(endpoint, data, files):
    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(endpoint, data=data, files=files)
    return response


async def _async_delete(endpoint, data):
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(endpoint, data=data)
    return response


async def _async_get(endpoint):
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(endpoint)
    return response


def _run_async(coro):
    """
    Bridge an async coroutine into the synchronous Flask/WSGI handler.

    In Cloudflare Workers (Pyodide), run_sync suspends Python execution
    until the JavaScript promise resolves.

    For local development (non-Pyodide), fall back to asyncio.
    """
    if run_sync is not None:
        return run_sync(coro)

    # Local development fallback
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─────────────────────────────────────────────────────────────────────────
# Public API (same interface as before)
# ─────────────────────────────────────────────────────────────────────────

def upload_to_cloudinary(file_obj, resource_type: str = "auto"):
    """
    Upload a Flask FileStorage/file-like object to Cloudinary.

    Returns:
        {
            "public_id": str,
            "url": str,
            "thumbnail_url": str,
            "bytes": int,
            "format": str,
            "width": int,
            "height": int,
            "duration": int|float
        }

    Returns None on failure.
    """
    credentials = _credentials()

    if not credentials:
        print("[Cloudinary] Missing Cloudinary credentials.")
        return None

    cloud_name, api_key, api_secret = credentials
    cld_type = _resource_type(resource_type)

    try:
        # Read uploaded file.
        if hasattr(file_obj, "read"):
            file_data = file_obj.read()
        else:
            with open(file_obj, "rb") as f:
                file_data = f.read()

        if not file_data:
            print("[Cloudinary] Empty upload.")
            return None

        # Cloudinary signed upload parameters.
        timestamp = int(time.time())

        upload_params = {
            "folder": "nexusvault",
            "timestamp": timestamp,
            "use_filename": "true",
            "unique_filename": "true",
        }

        signature = _signature(upload_params, api_secret)

        filename = getattr(file_obj, "filename", None) or "upload"

        data = {
            "api_key": api_key,
            "timestamp": str(timestamp),
            "signature": signature,
            "folder": "nexusvault",
            "use_filename": "true",
            "unique_filename": "true",
        }

        files = {
            "file": (
                filename,
                file_data,
                "application/octet-stream",
            )
        }

        endpoint = _upload_endpoint(cloud_name, cld_type)

        # Use async httpx (the only mode that works in Python Workers)
        response = _run_async(_async_upload(endpoint, data, files))

        if response.status_code >= 400:
            print(
                f"[Cloudinary] Upload failed "
                f"({response.status_code}): {response.text}"
            )
            return None

        result = response.json()

        secure_url = result.get("secure_url", "")
        public_id = result.get("public_id", "")

        # Build a Cloudinary video thumbnail without using the SDK.
        thumbnail_url = secure_url

        if resource_type == "video" and public_id:
            thumbnail_url = (
                f"https://res.cloudinary.com/{cloud_name}/video/upload/"
                f"c_fill,w_480,h_270,f_jpg/"
                f"{public_id}.jpg"
            )

        return {
            "public_id": public_id,
            "url": secure_url,
            "thumbnail_url": thumbnail_url,
            "bytes": result.get("bytes", 0),
            "format": result.get("format", ""),
            "width": result.get("width", 0),
            "height": result.get("height", 0),
            "duration": result.get("duration", 0),
        }

    except Exception as e:
        print(f"[Cloudinary] Upload error: {e}")
        return None


def delete_from_cloudinary(
    public_id: str,
    resource_type: str = "raw",
) -> bool:
    """
    Delete an asset from Cloudinary using the destroy API.
    """
    credentials = _credentials()

    if not credentials or not public_id:
        return False

    cloud_name, api_key, api_secret = credentials

    cld_type = {
        "video": "video",
        "image": "image",
        "file": "raw",
        "link": "raw",
    }.get(resource_type, resource_type)

    try:
        timestamp = int(time.time())

        params = {
            "public_id": public_id,
            "timestamp": timestamp,
        }

        signature = _signature(params, api_secret)

        endpoint = (
            f"https://api.cloudinary.com/v1_1/"
            f"{cloud_name}/{cld_type}/destroy"
        )

        data = {
            "public_id": public_id,
            "timestamp": str(timestamp),
            "api_key": api_key,
            "signature": signature,
        }

        response = _run_async(_async_delete(endpoint, data))

        if response.status_code >= 400:
            print(
                f"[Cloudinary] Delete failed "
                f"({response.status_code}): {response.text}"
            )
            return False

        result = response.json()

        return result.get("result") in (
            "ok",
            "not found",
        )

    except Exception as e:
        print(f"[Cloudinary] Delete error: {e}")
        return False


def test_cloudinary_connection() -> dict:
    """
    Test Cloudinary API connectivity/configuration.

    Returns:
        {
            "ok": bool,
            "status": int|None,
            "cert_used": str,
            "error": str|None
        }
    """
    credentials = _credentials()

    out = {
        "ok": False,
        "status": None,
        "cert_used": "httpx/Cloudflare TLS",
        "error": None,
    }

    if not credentials:
        out["error"] = "Cloudinary credentials are not configured."
        return out

    cloud_name, _, _ = credentials

    try:
        endpoint = f"https://api.cloudinary.com/v1_1/{cloud_name}/resources/image"

        response = _run_async(_async_get(endpoint))

        out["status"] = response.status_code
        # 401 means the Cloudinary API itself is reachable.
        # It simply requires authenticated resource access.
        out["ok"] = response.status_code < 500

    except Exception as e:
        out["error"] = str(e)

    return out
