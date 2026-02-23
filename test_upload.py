import requests
import jwt
from datetime import datetime, timedelta

# Create valid token
token = jwt.encode(
    {"sub": "admin", "exp": datetime.utcnow() + timedelta(days=1)},
    "5c8b7f8e9d6a2c3b4a5f9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c",
    algorithm="HS256"
)

# Create a valid tiny PNG file in memory
tiny_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\tpHYs\x00\x00\x0b\x13\x00\x00\x0b\x13\x01\x00\x9a\x9c\x18\x00\x00\x00\x0bIDAT\x08\x99c\xf8\x0f\x04\x00\x09\xfb\x03\xfd\xe3U\xf2\x9c\x00\x00\x00\x00IEND\xaeB`\x82'

files = {
    'file': ('test.png', tiny_png, 'image/png')
}
data = {
    'thumbnail': 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='
}

headers = {
    "Authorization": f"Bearer {token}"
}

print("Sending request...")
res = requests.post("http://localhost:8000/api/chat/upload-image", headers=headers, files=files, data=data)
print(f"Status: {res.status_code}")
print(f"Body: {res.text}")
