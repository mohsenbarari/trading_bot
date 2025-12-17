# Clean Architecture Structure

## 📁 ساختار پروژه

```
src/
├── core/                    # 🔵 هسته - بدون وابستگی خارجی
│   ├── entities/            # موجودیت‌های دامنه (Pure Python)
│   ├── schemas/             # Pydantic DTOs
│   ├── services/            # Use Cases / Business Logic
│   ├── repositories/        # Repository Interfaces (Protocols)
│   ├── exceptions/          # Domain Exceptions
│   └── config/              # تنظیمات هسته
│
├── infrastructure/          # 🟢 پیاده‌سازی زیرساخت
│   ├── database/
│   │   ├── connection.py    # SQLAlchemy setup
│   │   ├── models/          # ORM Models
│   │   └── repositories/    # Concrete Repository implementations
│   ├── cache/               # Redis implementation
│   └── external/            # External services
│
├── interfaces/              # 🟡 رابط‌های کاربری
│   ├── telegram_bot/        # 🤖 Telegram Bot handlers
│   │   ├── dependencies.py  # DI for bot
│   │   ├── handlers/
│   │   ├── keyboards/
│   │   └── states/
│   │
│   └── http_api/            # 🌐 HTTP API (Web/Android)
│       ├── dependencies.py  # DI for FastAPI
│       ├── routers/
│       └── middlewares/
│
└── shared/                  # 🔶 ابزارهای مشترک
    └── utils/
```

## 🔑 اصول کلیدی

### 1. ایزولاسیون هسته
- `core/services/` هیچ import از aiogram، FastAPI یا SQLAlchemy ندارد
- فقط با Pydantic schemas و Repository interfaces کار می‌کند

### 2. جداسازی رابط‌ها
- `telegram_bot/` و `http_api/` کاملاً مستقل هستند
- هر کدام Dependency Injection خود را دارند

### 3. تست‌پذیری
- Repository ها قابل Mock هستند
- Services را می‌توان بدون دیتابیس تست کرد

## 📝 نحوه استفاده

### در Telegram Bot:
```python
from src.interfaces.telegram_bot.dependencies import get_user_service

@router.message(Command("profile"))
async def handle_profile(message: types.Message):
    service = await get_user_service()
    user = await service.get_by_telegram(message.from_user.id)
```

### در FastAPI:
```python
from src.interfaces.http_api.dependencies import get_user_service

@router.get("/users/{user_id}")
async def get_user(
    user_id: int,
    service: UserService = Depends(get_user_service)
):
    return await service.get_user(user_id)
```

## 🚀 مهاجرت تدریجی

این ساختار به صورت موازی با کد فعلی کار می‌کند:
1. کد فعلی (`bot/`, `api/`, `models/`) بدون تغییر است
2. کد جدید در `src/` توسعه می‌یابد
3. به تدریج فیچرها به ساختار جدید منتقل می‌شوند

## 📦 اضافه کردن سرویس جدید

1. Entity را در `core/entities/` تعریف کنید
2. Schemas را در `core/schemas/` بسازید
3. Repository Interface را در `core/repositories/` تعریف کنید
4. Service را در `core/services/` پیاده‌سازی کنید
5. Repository Implementation را در `infrastructure/database/repositories/` بسازید
6. در هندلر/روتر از Dependency Injection استفاده کنید
