import asyncio
import aioboto3
import urllib.request
from urllib.error import HTTPError

async def test_native():
    print("Testing native boto3 config (same as backend)")
    session = aioboto3.Session()
    async with session.client(
        's3',
        endpoint_url="https://c973477.parspack.net",
        aws_access_key_id="C9MigUpA2axAs4Ho",
        aws_secret_access_key="BJ0QswEDy3CVFCPUHBVrBfoBZXJwy7rN",
    ) as s3_client:
        try:
            print("Putting object...")
            await s3_client.put_object(Bucket="coin.gold-trade", Key="test_put.txt", Body=b"hello")
            print("PUT Object Successful!")
        except Exception as e:
            print("PUT Failed:", e)
            return

        try:
            print("Generating presigned URL...")
            url = await s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': 'coin.gold-trade', 'Key': 'test_put.txt'},
                ExpiresIn=3600
            )
            print("GET URL:", url)
            
            print("Fetching GET URL...")
            req = urllib.request.Request(url)
            res = urllib.request.urlopen(req)
            print("GET Status:", res.status)
        except HTTPError as e:
            print("HTTP Fetch Error:", e.code, e.reason)
            print("Response:", e.read().decode('utf-8'))
        except Exception as e:
            print("Other Error:", e)

asyncio.run(test_native())
