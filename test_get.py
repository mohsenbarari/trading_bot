import asyncio
import aioboto3

async def test_get():
    print("Testing get_object native")
    session = aioboto3.Session()
    async with session.client(
        's3',
        endpoint_url="https://c973477.parspack.net",
        aws_access_key_id="C9MigUpA2axAs4Ho",
        aws_secret_access_key="BJ0QswEDy3CVFCPUHBVrBfoBZXJwy7rN",
    ) as s3_client:
        try:
            print("Fetching object...")
            response = await s3_client.get_object(Bucket="coin.gold-trade", Key="test_put.txt")
            data = await response['Body'].read()
            print("GET Object Successful! Data:", data)
        except Exception as e:
            print("GET Failed:", e)

asyncio.run(test_get())
