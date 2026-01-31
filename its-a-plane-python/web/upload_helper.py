import requests
import os

# Pi B (server) URL
SERVER_URL = "http://c0wsaysmoo.ddnsgeek.com:8081"

def get_upload_token() -> str:
    """Request a new upload token from the server."""
    try:
        resp = requests.get(f"{SERVER_URL}/get-token", timeout=5)
        resp.raise_for_status()
        token_line = resp.text.strip()
        # Expecting format: "Your upload token: <token>"
        token = token_line.split(":")[-1].strip()
        return token
    except Exception as e:
        print(f"⚠️ Failed to get upload token: {e}")
        return ""

def upload_map_to_server(local_path: str) -> str:
    """
    Upload a map file to Pi B using a dynamically obtained token.
    Returns the public URL (or empty string on failure).
    """
    if not os.path.isfile(local_path):
        print(f"⚠️ File not found: {local_path}")
        return ""

    token = get_upload_token()
    if not token:
        return ""

    upload_url = f"{SERVER_URL}/upload/{token}"
    try:
        with open(local_path, "rb") as f:
            files = {"file": f}
            resp = requests.post(upload_url, files=files, timeout=10)
            resp.raise_for_status()
            # The server responds with "Uploaded as <filename>"
            uploaded_name = resp.text.strip().split("Uploaded as")[-1].strip()
            return f"https://c0wsaysmoo.ddnsgeek.com/maps/{uploaded_name}"
    except Exception as e:
        print(f"⚠️ Failed to upload map: {e}")

        return ""
