import asyncio
import aioboto3
from botocore.config import Config

async def test_get_virtual():
    print("Testing get_object with parspack.net + virtual bucket c973477")
    session = aioboto3.Session()
    conf = Config(signature_version='s3v4', s3={'addressing_style': 'virtual'})
    async with session.client(
        's3',
        endpoint_url="https://parspack.net",
        aws_access_key_id="C9MigUpA2axAs4Ho",
        aws_secret_access_key="BJ0QswEDy3CVFCPUHBVrBfoBZXJwy7rN",
        config=conf
    ) as s3_client:
        try:
            print("Putting object...")
            await s3_client.put_object(Bucket="c973477", Key="coin.gold-trade/test2.txt", Body=b"hello")
            print("PUT Object Successful!")
        except Exception as e:
            print("PUT Failed:", e)

        try:
            print("Fetching object native...")
            res = await s3_client.get_object(Bucket="c973477", Key="coin.gold-trade/test2.txt")
            data = await res['Body'].read()
            print("GET Native Successful:", data)
        except Exception as e:
            print("GET Native Failed:", e)

asyncio.run(test_get_virtual())
