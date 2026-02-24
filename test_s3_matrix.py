import asyncio
import aioboto3
import urllib.request
from urllib.error import HTTPError, URLError
from botocore.config import Config

async def test_s3(addressing, sig_ver):
    print(f"\n--- Testing addressing={addressing}, sig_ver={sig_ver} ---")
    session = aioboto3.Session()
    conf = Config(signature_version=sig_ver, s3={'addressing_style': addressing})
    
    async with session.client(
        's3',
        endpoint_url="https://c973477.parspack.net",
        aws_access_key_id="C9MigUpA2axAs4Ho",
        aws_secret_access_key="BJ0QswEDy3CVFCPUHBVrBfoBZXJwy7rN",
        config=conf,
        region_name="us-east-1"
    ) as s3_client:
        # Create a tiny text file to see if we can PUT and GET
        try:
            url = await s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': 'coin.gold-trade',
                    'Key': 'chat_files/test.jpg'
                },
                ExpiresIn=3600
            )
            print("Generated GET URL:", url)
            
            req = urllib.request.Request(url)
            res = urllib.request.urlopen(req)
            print("HTTP Status:", res.status)
        except HTTPError as e:
            print("HTTP Fetch Error:", e.code, e.reason)
            print("Response:", e.read().decode('utf-8'))
        except Exception as e:
            print("Other Error:", e)

async def main():
    await test_s3('path', 's3v4')
    await test_s3('virtual', 's3v4')
    await test_s3('path', 's3')
    await test_s3('virtual', 's3')

asyncio.run(main())
