import asyncio
import aioboto3

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
                'Key': 'test.jpg'
            },
            ExpiresIn=3600
        )
        print("URL:", url)
        
        import urllib.request
        try:
            req = urllib.request.Request(url)
            res = urllib.request.urlopen(req)
            print("Status:", res.status)
        except Exception as e:
            print("Fetch Error:", e)

asyncio.run(main())
