import asyncio
import aioboto3
import urllib.request
from urllib.error import HTTPError
from botocore.config import Config

async def test(ep, bucket, key, addr):
    print(f"\n--- EP: {ep} | B: {bucket} | K: {key} | Addr: {addr} ---")
    session = aioboto3.Session()
    conf = Config(signature_version='s3v4', s3={'addressing_style': addr})
    try:
        async with session.client('s3', endpoint_url=ep, aws_access_key_id="C9MigUpA2axAs4Ho", aws_secret_access_key="BJ0QswEDy3CVFCPUHBVrBfoBZXJwy7rN", config=conf, region_name="us-east-1") as s3:
            # try to put an object first to see if we can write
            try:
                await s3.put_object(Bucket=bucket, Key=key, Body=b"test")
                print("PUT successful!")
            except Exception as e:
                print("PUT Failed:", type(e).__name__, str(e))
                
            url = await s3.generate_presigned_url('get_object', Params={'Bucket': bucket, 'Key': key}, ExpiresIn=3600)
            print("GET URL:", url)
            req = urllib.request.Request(url)
            res = urllib.request.urlopen(req)
            print("GET Status:", res.status)
    except HTTPError as e:
        print("HTTP Error:", e.code)
        print("Response:", e.read().decode('utf-8'))
    except Exception as e:
        print("Error:", e)

async def main():
    await test("https://parspack.net", "c973477", "coin.gold-trade/test.txt", "virtual")
    await test("https://s3.parspack.net", "c973477", "coin.gold-trade/test.txt", "virtual")
    await test("https://c973477.parspack.net", "c973477", "coin.gold-trade/test.txt", "path")
    await test("https://parspack.net", "c973477", "test.txt", "virtual")

asyncio.run(main())
