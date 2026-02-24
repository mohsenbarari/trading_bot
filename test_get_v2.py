import asyncio
import aioboto3
from botocore.config import Config

async def test_get_v2():
    print("Testing get_object native with Signature Version 2")
    session = aioboto3.Session()
    conf = Config(signature_version='s3')
    async with session.client(
        's3',
        endpoint_url="https://c973477.parspack.net",
        aws_access_key_id="C9MigUpA2axAs4Ho",
        aws_secret_access_key="BJ0QswEDy3CVFCPUHBVrBfoBZXJwy7rN",
        config=conf
    ) as s3_client:
        try:
            print("Fetching object...")
            response = await s3_client.get_object(Bucket="coin.gold-trade", Key="test_put.txt")
            data = await response['Body'].read()
            print("GET Object Successful! Data:", data)
        except Exception as e:
            print("GET Failed:", e)

asyncio.run(test_get_v2())
