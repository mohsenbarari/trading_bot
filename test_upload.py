import requests
import jwt
import sys
import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
load_dotenv('.env')

# Create a valid token with actual JWT secret from .env
secret = os.getenv('JWT_SECRET_KEY')
if not secret:
    print("Cannot find JWT_SECRET_KEY")
    sys.exit(1)

# we need an active user id, assume user 1 (the admin user)
token = jwt.encode(
    {"sub": "1", "exp": datetime.utcnow() + timedelta(days=1)},
    secret,
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
