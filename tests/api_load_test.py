# tests/api_load_test.py
"""
تست فشار API - Trading Bot

این اسکریپت API endpoints را به صورت همزمان تست می‌کند.

استفاده:
    python tests/api_load_test.py --base-url http://localhost:8000 --concurrent 20 --requests 100
"""

import asyncio
import httpx
import random
import time
import argparse
import logging
from datetime import datetime
from typing import Dict, List, Any
from dataclasses import dataclass, field

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class APITestResult:
    """نتیجه یک درخواست API"""
    endpoint: str
    method: str
    status_code: int
    response_time_ms: float
    success: bool
    error: str = None


@dataclass
class APITestStats:
    """آمار کلی تست API"""
    results: List[APITestResult] = field(default_factory=list)
    start_time: datetime = None
    end_time: datetime = None
    
    def add_result(self, result: APITestResult):
        self.results.append(result)
    
    @property
    def total_requests(self) -> int:
        return len(self.results)
    
    @property
    def successful_requests(self) -> int:
        return len([r for r in self.results if r.success])
    
    @property
    def failed_requests(self) -> int:
        return len([r for r in self.results if not r.success])
    
    @property
    def avg_response_time(self) -> float:
        if not self.results:
            return 0
        return sum(r.response_time_ms for r in self.results) / len(self.results)
    
    @property
    def min_response_time(self) -> float:
        if not self.results:
            return 0
        return min(r.response_time_ms for r in self.results)
    
    @property
    def max_response_time(self) -> float:
        if not self.results:
            return 0
        return max(r.response_time_ms for r in self.results)
    
    @property
    def p95_response_time(self) -> float:
        if not self.results:
            return 0
        sorted_times = sorted(r.response_time_ms for r in self.results)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[idx] if idx < len(sorted_times) else sorted_times[-1]
    
    def report(self) -> str:
        duration = (self.end_time - self.start_time).total_seconds()
        
        # گروه‌بندی بر اساس endpoint
        endpoint_stats = {}
        for r in self.results:
            key = f"{r.method} {r.endpoint}"
            if key not in endpoint_stats:
                endpoint_stats[key] = {"count": 0, "success": 0, "total_time": 0}
            endpoint_stats[key]["count"] += 1
            endpoint_stats[key]["success"] += 1 if r.success else 0
            endpoint_stats[key]["total_time"] += r.response_time_ms
        
        endpoint_report = "\n".join([
            f"  {key}: {stats['success']}/{stats['count']} ({stats['total_time']/stats['count']:.1f}ms avg)"
            for key, stats in endpoint_stats.items()
        ])
        
        return f"""
═══════════════════════════════════════════════════════════════
                    📊 نتایج تست فشار API
═══════════════════════════════════════════════════════════════
⏱️  مدت زمان کل:           {duration:.2f} ثانیه
📊 نرخ درخواست:            {self.total_requests / duration:.2f} req/sec

📈 آمار درخواست‌ها:
   - کل:                   {self.total_requests}
   - موفق:                 {self.successful_requests}
   - ناموفق:               {self.failed_requests}
   - نرخ موفقیت:           {100 * self.successful_requests / max(1, self.total_requests):.1f}%

⏱️ زمان پاسخ:
   - میانگین:              {self.avg_response_time:.1f}ms
   - حداقل:                {self.min_response_time:.1f}ms
   - حداکثر:               {self.max_response_time:.1f}ms
   - P95:                  {self.p95_response_time:.1f}ms

📋 جزئیات Endpoints:
{endpoint_report}
═══════════════════════════════════════════════════════════════
"""


class APILoadTester:
    """کلاس تست فشار API"""
    
    def __init__(
        self, 
        base_url: str,
        concurrent_users: int,
        total_requests: int
    ):
        self.base_url = base_url.rstrip("/")
        self.concurrent_users = concurrent_users
        self.total_requests = total_requests
        self.stats = APITestStats()
        self.tokens: Dict[str, str] = {}  # user_id -> token
        
    async def make_request(
        self, 
        client: httpx.AsyncClient,
        method: str,
        endpoint: str,
        **kwargs
    ) -> APITestResult:
        """اجرای یک درخواست و ثبت نتیجه"""
        url = f"{self.base_url}{endpoint}"
        start_time = time.time()
        
        try:
            response = await client.request(method, url, **kwargs)
            response_time = (time.time() - start_time) * 1000
            
            success = 200 <= response.status_code < 400
            
            result = APITestResult(
                endpoint=endpoint,
                method=method,
                status_code=response.status_code,
                response_time_ms=response_time,
                success=success,
                error=None if success else response.text[:100]
            )
            
        except Exception as e:
            response_time = (time.time() - start_time) * 1000
            result = APITestResult(
                endpoint=endpoint,
                method=method,
                status_code=0,
                response_time_ms=response_time,
                success=False,
                error=str(e)[:100]
            )
        
        self.stats.add_result(result)
        return result
    
    async def test_public_endpoints(self, client: httpx.AsyncClient):
        """تست endpoints عمومی"""
        endpoints = [
            ("GET", "/api/config"),
            ("GET", "/api/commodities/"),
        ]
        
        endpoint = random.choice(endpoints)
        await self.make_request(client, endpoint[0], endpoint[1])
    
    async def test_offers_endpoints(self, client: httpx.AsyncClient, token: str = None):
        """تست endpoints لفظ‌ها"""
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        
        # لیست لفظ‌های فعال
        await self.make_request(
            client, "GET", "/api/offers/active",
            headers=headers
        )
        
        # لفظ‌های من
        if token:
            await self.make_request(
                client, "GET", "/api/offers/my",
                headers=headers
            )
    
    async def test_trades_endpoints(self, client: httpx.AsyncClient, token: str = None):
        """تست endpoints معاملات"""
        if not token:
            return
            
        headers = {"Authorization": f"Bearer {token}"}
        
        # تاریخچه معاملات
        await self.make_request(
            client, "GET", "/api/trades/my",
            headers=headers
        )
    
    async def test_realtime_endpoint(self, client: httpx.AsyncClient):
        """تست SSE endpoint"""
        try:
            # فقط اتصال و قطع سریع
            async with client.stream("GET", f"{self.base_url}/api/realtime/stream", timeout=2.0) as response:
                async for line in response.aiter_lines():
                    if line:
                        break  # فقط یک خط بخوان و خارج شو
        except httpx.ReadTimeout:
            pass  # انتظار می‌رود
        except Exception as e:
            logger.debug(f"SSE test: {e}")
    
    async def worker(self, worker_id: int, requests_per_worker: int):
        """یک worker که درخواست‌ها را اجرا می‌کند"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            for i in range(requests_per_worker):
                try:
                    # انتخاب تصادفی نوع تست
                    test_type = random.choice([
                        'public', 'public', 'public',  # بیشتر public
                        'offers', 'offers',
                        'trades',
                        'realtime'
                    ])
                    
                    if test_type == 'public':
                        await self.test_public_endpoints(client)
                    elif test_type == 'offers':
                        await self.test_offers_endpoints(client)
                    elif test_type == 'trades':
                        await self.test_trades_endpoints(client)
                    elif test_type == 'realtime':
                        await self.test_realtime_endpoint(client)
                        
                    # کمی تأخیر تصادفی
                    await asyncio.sleep(random.uniform(0.01, 0.1))
                    
                except Exception as e:
                    logger.error(f"Worker {worker_id} error: {e}")
    
    async def run(self):
        """اجرای تست"""
        self.stats.start_time = datetime.now()
        
        logger.info("═" * 60)
        logger.info("           🧪 شروع تست فشار API")
        logger.info("═" * 60)
        logger.info(f"🌐 Base URL: {self.base_url}")
        logger.info(f"👥 Concurrent users: {self.concurrent_users}")
        logger.info(f"📊 Total requests: {self.total_requests}")
        
        # تقسیم درخواست‌ها بین workers
        requests_per_worker = self.total_requests // self.concurrent_users
        
        # اجرای workers
        tasks = [
            self.worker(i, requests_per_worker)
            for i in range(self.concurrent_users)
        ]
        
        await asyncio.gather(*tasks)
        
        self.stats.end_time = datetime.now()
        print(self.stats.report())


class WebSocketLoadTester:
    """تست فشار WebSocket"""
    
    def __init__(self, base_url: str, num_connections: int, duration_seconds: int):
        self.ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
        self.ws_url = f"{self.ws_url}/api/realtime/ws"
        self.num_connections = num_connections
        self.duration_seconds = duration_seconds
        self.messages_received = 0
        self.connections_successful = 0
        self.connections_failed = 0
    
    async def connect_and_listen(self, client_id: int):
        """اتصال WebSocket و گوش دادن"""
        import websockets
        
        try:
            async with websockets.connect(self.ws_url) as ws:
                self.connections_successful += 1
                logger.debug(f"Client {client_id} connected")
                
                # گوش دادن به پیام‌ها
                end_time = time.time() + self.duration_seconds
                while time.time() < end_time:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        self.messages_received += 1
                    except asyncio.TimeoutError:
                        # ارسال ping
                        await ws.send("ping")
                    except Exception:
                        break
                        
        except Exception as e:
            self.connections_failed += 1
            logger.debug(f"Client {client_id} failed: {e}")
    
    async def run(self):
        """اجرای تست WebSocket"""
        logger.info("═" * 60)
        logger.info("           🔌 شروع تست فشار WebSocket")
        logger.info("═" * 60)
        logger.info(f"🔗 WebSocket URL: {self.ws_url}")
        logger.info(f"👥 Connections: {self.num_connections}")
        logger.info(f"⏱️ Duration: {self.duration_seconds}s")
        
        start_time = time.time()
        
        tasks = [
            self.connect_and_listen(i)
            for i in range(self.num_connections)
        ]
        
        await asyncio.gather(*tasks, return_exceptions=True)
        
        duration = time.time() - start_time
        
        print(f"""
═══════════════════════════════════════════════════════════════
                    📊 نتایج تست WebSocket
═══════════════════════════════════════════════════════════════
⏱️  مدت زمان:              {duration:.2f} ثانیه
🔗 اتصالات موفق:           {self.connections_successful}
❌ اتصالات ناموفق:          {self.connections_failed}
📩 پیام‌های دریافتی:        {self.messages_received}
═══════════════════════════════════════════════════════════════
""")


async def main():
    parser = argparse.ArgumentParser(description="تست فشار API Trading Bot")
    parser.add_argument(
        "--base-url", 
        type=str, 
        default="http://localhost:8000",
        help="آدرس پایه API"
    )
    parser.add_argument(
        "--concurrent", 
        type=int, 
        default=20,
        help="تعداد کاربران همزمان"
    )
    parser.add_argument(
        "--requests", 
        type=int, 
        default=100,
        help="تعداد کل درخواست‌ها"
    )
    parser.add_argument(
        "--websocket", 
        action="store_true",
        help="تست WebSocket"
    )
    parser.add_argument(
        "--ws-connections", 
        type=int, 
        default=50,
        help="تعداد اتصالات WebSocket"
    )
    parser.add_argument(
        "--ws-duration", 
        type=int, 
        default=30,
        help="مدت تست WebSocket (ثانیه)"
    )
    
    args = parser.parse_args()
    
    if args.websocket:
        tester = WebSocketLoadTester(
            base_url=args.base_url,
            num_connections=args.ws_connections,
            duration_seconds=args.ws_duration
        )
    else:
        tester = APILoadTester(
            base_url=args.base_url,
            concurrent_users=args.concurrent,
            total_requests=args.requests
        )
    
    await tester.run()


if __name__ == "__main__":
    asyncio.run(main())
