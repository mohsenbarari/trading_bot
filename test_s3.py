import asyncio
import aioboto3
import urllib.request
from urllib.error import HTTPError, URLError

async def main():
    session = aioboto3.Session()
    async with session.client(
        's3',
        endpoint_url="https://c973477.parspack.net",
        aws_access_key_id="C9MigUpA2axAs4Ho",
        aws_secret_access_key="BJ0QswEDy3CVFCPUHBVrBfoBZXJwy7rN",
    ) as s3_client:
        url = await s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': 'coin.gold-trade',
                'Key': 'chat_files/test.jpg'
            },
            ExpiresIn=3600
        )
        print("Generated URL:\n", url)
        
        try:
            req = urllib.request.Request(url)
            res = urllib.request.urlopen(req)
            print("HTTP Status:", res.status)
        except HTTPError as e:
            print("HTTP Fetch Error:", e.code, e.reason)
            print("Response:", e.read().decode('utf-8'))
        except URLError as e:
            print("URL Fetch Error:", e.reason)
        except Exception as e:
            print("Other Error:", e)

asyncio.run(main())
